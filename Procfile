web: sh -c "echo \"$GOOGLE_CREDENTIALS_B64\" | base64 -d > stt_creds.json && export GOOGLE_APPLICATION_CREDENTIALS=\"$PWD/stt_creds.json\" && python app.py --port $PORT --stream_type bidirectional"
