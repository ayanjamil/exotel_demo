import io
import re
import sys
import argparse
import time
import os
from google.cloud import speech_v1p1beta1 as speech
import base64
import json
import signal
import logging
import threading

from elevenlabs_client import ElevenLabsClient
from flask import Flask
from flask_sockets import Sockets
from six.moves import queue
from threading import Thread
from gevent import pywsgi
from geventwebsocket.handler import WebSocketHandler    

from dotenv import load_dotenv
load_dotenv()

ELEVEN_LABS_AGENT_ID = os.getenv('ELEVENLABS_AGENT_ID')
ELEVEN_LABS_API_KEYS = os.getenv('ELEVENLABS_API_KEY')

app = Flask(__name__)
sockets = Sockets(app)
app.logger.setLevel(logging.DEBUG)

def signal_handler(sig, frame):
    sys.exit(0)

def listen_print_loop(responses):
    """Iterates through server responses and prints them.

    The responses passed is a generator that will block until a response
    is provided by the server.

    Each response may contain multiple results, and each result may contain
    multiple alternatives; for details, see https://goo.gl/tjCPAU.  Here we
    print only the transcription for the top alternative of the top result.

    In this case, responses are provided for interim results as well. If the
    response is an interim one, print a line feed at the end of it, to allow
    the next result to overwrite it, until the response is a final one. For the
    final one, print a newline to preserve the finalized transcription.
    """
    num_chars_printed = 0
    for response in responses:
        if not response.results:
            continue

        # The `results` list is consecutive. For streaming, we only care about
        # the first result being considered, since once it's `is_final`, it
        # moves on to considering the next utterance.
        result = response.results[0]
        if not result.alternatives:
            continue

        # Display the transcription of the top alternative.
        transcript = result.alternatives[0].transcript

        # Display interim results, but with a carriage return at the end of the
        # line, so subsequent lines will overwrite them.
        #
        # If the previous result was longer than this one, we need to print
        # some extra spaces to overwrite the previous result
        overwrite_chars = " " * (num_chars_printed - len(transcript))

        if not result.is_final:
            sys.stdout.write(transcript + overwrite_chars + "\r")
            sys.stdout.flush()

            num_chars_printed = len(transcript)

        else:
            print(transcript + overwrite_chars)

            # Exit recognition if any of the transcribed phrases could be
            # one of our keywords.
            if re.search(r"\b(exit|quit)\b", transcript, re.I):
                print("Exiting..")
                break

            num_chars_printed = 0


class Stream(object):
    """Opens a recording stream as a generator yielding the audio chunks."""

    def __init__(self, rate, chunk):
        self._rate = rate
        self._chunk = chunk

        # Create a thread-safe buffer of audio data
        self.buff = queue.Queue()
        self.closed = True

    def __enter__(self):
        self.closed = False

        return self

    def __exit__(self, type, value, traceback):
        self.closed = True
        # Signal the generator to terminate so that the client's
        # streaming_recognize method will not block the process termination.
        self.buff.put(None)

    def fill_buffer(self, in_data):
        """Continuously collect data from the audio stream, into the buffer."""
        self.buff.put(in_data)
        return self

    def generator(self):
        while True:
            # Use a blocking get() to ensure there's at least one chunk of
            # data, and stop iteration if the chunk is None, indicating the
            # end of the audio stream.
            chunk = self.buff.get()
            if chunk is None:
                return
            data = [chunk]

            # Now consume whatever other data's still buffered.
            while True:
                try:
                    chunk = self.buff.get(block=False)
                    if chunk is None:
                        return
                    data.append(chunk)
                except queue.Empty:
                    break

            yield b"".join(data)

def send_to_exotel(audio_chunk):
    if exotel_ws and current_sid:
        payload = {
            'event': 'media',
            'stream_sid': current_sid,
            'media': {
                'payload': base64.b64encode(audio_chunk).decode("ascii")
            }
        }
        exotel_ws.send(json.dumps(payload))

current_sid = None
exotel_ws = None
agent_id = ELEVEN_LABS_AGENT_ID
api_key = ELEVEN_LABS_API_KEYS
elevenlabs = ElevenLabsClient(agent_id, api_key, send_to_exotel)
elevenlabs.connect()

@sockets.route('/media')
def handle_exotel(ws):
    global current_sid, exotel_ws
    exotel_ws = ws

    while not ws.closed:
        msg = ws.receive()
        if msg is None:
            continue

        data = json.loads(msg)
        event = data.get("event")

        if event == "start":
            current_sid = data.get("stream_sid")

        elif event == "media":
            payload = data["media"]["payload"]
            chunk = base64.b64decode(payload)
            elevenlabs.send_audio(chunk)

        elif event == "stop":
            break



def stream_transcript():
    while True:
        audio_generator = stream.generator()
        try:
            requests = (
                speech.StreamingRecognizeRequest(audio_content=content)
                for content in audio_generator
            )
            responses = client.streaming_recognize(streaming_config, requests)
            # Now, put the transcription responses to use.
            listen_print_loop(responses)
        except:
            pass
        time.sleep(5)

def stream_playback(ws, stream_sid):
    while not ws.closed:
        audio_generator = stream.generator()
        try:
            for content in audio_generator:
                d = json.dumps({
                    'event': 'media',
                    'stream_sid': stream_sid,
                    'media': {
                        'payload': base64.b64encode(content).decode("ascii")
                    }
                })
                time.sleep(0.25)
                ws.send(d)
                time.sleep(0.20)
        except:
            pass

if __name__ == '__main__':
    app.logger.setLevel(logging.DEBUG)
    parser = argparse.ArgumentParser(description='ExoWS client to enable WS communication')
    parser.add_argument('--port', type=int, default=5000, help='Specify the port on which WS server should be listening')
    parser.add_argument('--stream_type', type=str, required=True, choices=['unidirectional', 'bidirectional'], help='Specify the type of stream')
    args = parser.parse_args()

    # Audio recording parameters
    RATE = 8000
    CHUNK = int(RATE / 10)  # 100ms

    HTTP_SERVER_PORT = args.port

    language_code = "en-IN"  # a BCP-47 language tag

    client = speech.SpeechClient()
    config = speech.RecognitionConfig(
        encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
        sample_rate_hertz=RATE,
        language_code=language_code,
        enable_speaker_diarization=True,
    )

    streaming_config = speech.StreamingRecognitionConfig(
        config=config, interim_results=True
    )
    stream = Stream(RATE, CHUNK)
    if args.stream_type == "unidirectional":
        t1 = threading.Thread(target=stream_transcript)
        t1.daemon = True
        t1.start()

    signal.signal(signal.SIGINT, signal_handler)

    server = pywsgi.WSGIServer(('', HTTP_SERVER_PORT), app, handler_class=WebSocketHandler)
    print("Server listening on: http://localhost:" + str(HTTP_SERVER_PORT))
    print("Route for media: http://localhost:" + str(HTTP_SERVER_PORT) + '/media')
    server.serve_forever()
