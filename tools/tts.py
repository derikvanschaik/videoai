"""
TTS agent — text -> wav file via the local voice-clone server.

Start the server first:
    cd voice-clone && python server.py
"""
import json
import os
import shutil
import urllib.request


SERVER_URL = os.getenv("CLONE_TTS_URL", "http://localhost:8765/generate")


def tts(text: str, dst: str) -> str:
    """Send text to the voice-clone server and save the WAV to dst."""
    payload = json.dumps({"text": text}).encode()
    req     = urllib.request.Request(
        SERVER_URL,
        data    = payload,
        headers = {"Content-Type": "application/json"},
        method  = "POST",
    )
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read())

    shutil.copy2(result["file_path"], dst)
    return dst
