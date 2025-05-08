import base64
import json
import threading
import time
from websocket import WebSocketApp

class ElevenLabsClient:
    def __init__(self, agent_id, api_key, on_audio_callback):
        print("AGENT DETAILS ", agent_id, api_key)
        self.agent_id = agent_id
        self.api_key = api_key
        self.on_audio_callback = on_audio_callback

        self.url = f"wss://api.elevenlabs.io/v1/convai/conversation?agent_id={self.agent_id}"
        self.ws = None
        self.connected = False
        self._lock = threading.Lock()

    def on_message(self, ws, message):
        try:
            data = json.loads(message)
            print("[ElevenLabsClient] Message received:",  str(data)[:100]) 

            if data.get("type") == "audio":
                audio_event = data.get("audio_event", {})
                # audio_b64 = audio_event.get("audio")
                audio_b64 = audio_event.get("audio_base_64")  

                if audio_b64:
                    audio_chunk = base64.b64decode(audio_b64)
                    self.on_audio_callback(audio_chunk)
                else:
                    print("[ElevenLabsClient] No 'audio' field in 'audio_event'")
            else:
                print(f"[ElevenLabsClient] Ignored message type: {data.get('type')}")

        except Exception as e:
            print(f"[ElevenLabsClient] WebSocket error: {e}")


    def on_open(self, ws):
        print("[ElevenLabsClient] WebSocket connected.")
        self.connected = True

    def on_close(self, ws, close_status_code, close_msg):
        print(f"[ElevenLabsClient] WebSocket closed: {close_status_code} - {close_msg}")
        self.connected = False
        time.sleep(2)
        print("[ElevenLabsClient] Attempting reconnect...")
        self.connect()

    def on_error(self, ws, error):
        print(f"[ElevenLabsClient] WebSocket error: {error}")
        self.connected = False

    def connect(self):
        with self._lock:
            if self.connected:
                return
            self.ws = WebSocketApp(
                self.url,
                on_message=self.on_message,
                on_open=self.on_open,
                on_close=self.on_close,
                on_error=self.on_error
            )
            thread = threading.Thread(target=self.ws.run_forever)
            thread.daemon = True
            thread.start()

    def send_audio(self, pcm_chunk):
        if self.ws and self.ws.sock and self.ws.sock.connected:
            try:
                audio_b64 = base64.b64encode(pcm_chunk).decode("ascii")
                self.ws.send(json.dumps({
                    "type": "user_audio_chunk",
                    "user_audio_chunk": audio_b64
                }))
            except Exception as e:
                print(f"[ElevenLabsClient] Failed to send audio: {e}")
        else:
            print("[ElevenLabsClient] WebSocket is not connected.")
