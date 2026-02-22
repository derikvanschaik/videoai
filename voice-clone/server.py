#!/usr/bin/env python3
"""
Voice clone TTS server — loads the model once, generates audio on demand.

Usage:
    ./venv/bin/python server.py

Request:
    POST http://localhost:8765/generate
    Content-Type: application/json
    {"text": "whatever you want the voice to say"}

Response:
    {"file_path": "/absolute/path/to/outputs/<id>/audio_000.wav"}
"""

import json
import os
import uuid
import numpy as np
from http.server import BaseHTTPRequestHandler, HTTPServer

from dotenv import load_dotenv
from mlx_audio.tts.utils import load_model
from mlx_audio.utils import load_audio
from mlx_audio.audio_io import write as audio_write

# ── Config ─────────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(dotenv_path=os.path.join(BASE_DIR, "..", ".env"))

MODEL_ID       = os.environ["VOICE_CLONE_MODEL_ID"]
REF_AUDIO_PATH = os.environ["VOICE_CLONE_REF_AUDIO"]
REF_TEXT_FILE  = os.environ["VOICE_CLONE_REF_TEXT"]
OUTPUT_DIR     = os.path.join(BASE_DIR, os.getenv("VOICE_CLONE_OUTPUT_DIR", "outputs"))
PORT           = int(os.getenv("VOICE_CLONE_PORT", "8765"))

# ── Load ref text ──────────────────────────────────────────────────────────────
with open(REF_TEXT_FILE, "r") as f:
    REF_TEXT = f.read().strip()

# ── Load model & ref audio once ────────────────────────────────────────────────
print(f"Loading TTS model: {MODEL_ID} ...")
MODEL = load_model(model_path=MODEL_ID)
print("Loading reference audio ...")
REF_AUDIO = load_audio(REF_AUDIO_PATH, sample_rate=MODEL.sample_rate)
os.makedirs(OUTPUT_DIR, exist_ok=True)
print(f"Server ready on http://localhost:{PORT}")
print(f'  POST /generate  {{"text": "your text here"}}')

# ── HTTP handler ───────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/generate":
            self._send(404, {"error": "not found — use POST /generate"})
            return

        length = int(self.headers.get("Content-Length", 0))
        try:
            data = json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            self._send(400, {"error": "invalid JSON"})
            return

        text = (data.get("text") or "").strip()
        lang_code = (data.get("lang_code") or "en").strip()
        if not text:
            self._send(400, {"error": "missing or empty 'text' field"})
            return

        # Unique subfolder per request so files never collide
        run_id  = str(uuid.uuid4())[:8]
        out_dir = os.path.join(OUTPUT_DIR, run_id)
        os.makedirs(out_dir, exist_ok=True)

        try:
            results = MODEL.generate(
                text=text,
                voice=None,
                speed=1.0,
                lang_code=lang_code,
                ref_audio=REF_AUDIO,
                ref_text=REF_TEXT,
                temperature=0.7,
                max_tokens=1200,
                verbose=False,
                stream=False,
            )

            saved = []
            for i, result in enumerate(results):
                path = os.path.join(out_dir, f"audio_{i:03d}.wav")
                audio_write(path, np.array(result.audio), result.sample_rate, format="wav")
                saved.append(os.path.abspath(path))
                print(f"[{run_id}] saved {path}")

            if saved:
                self._send(200, {"file_path": saved[0]})
            else:
                self._send(500, {"error": "no audio was generated"})

        except Exception as exc:
            import traceback
            traceback.print_exc()
            self._send(500, {"error": str(exc)})

    def _send(self, code: int, payload: dict):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(f"[{self.address_string()}] {fmt % args}")


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
