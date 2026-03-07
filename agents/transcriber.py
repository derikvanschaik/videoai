"""
Transcriber agent — video file → list of transcribed word segments with MM:SS timestamps.
"""
from __future__ import annotations

import os

from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel

load_dotenv()

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
MODEL  = os.getenv("TRANSCRIBER_MODEL", "gemini-3.1-pro-preview")


# ── Schema ───────────────────────────────────────────────────────────────────────

class WordSegment(BaseModel):
    text:  str  # the spoken word or short phrase
    start: str  # MM:SS
    end:   str  # MM:SS

class Transcription(BaseModel):
    segments: list[WordSegment]


# ── Agent ────────────────────────────────────────────────────────────────────────

def transcribe_video(video_path: str) -> Transcription:
    """Open an mp4 and return word-level transcription with MM:SS timestamps."""
    parts = [
        types.Part(inline_data=types.Blob(
            data=open(video_path, "rb").read(),
            mime_type="video/mp4",
        )),
        types.Part(text="""Transcribe every spoken word in this video.

Return a list of segments. Each segment should contain:
- text:  the spoken word or a few words that make sense together (no more than 4 though)
- start: timestamp when the word/phrase starts, formatted as MM:SS  (e.g. "00:04")
- end:   timestamp when the word/phrase ends,   formatted as MM:SS  (e.g. "00:07")

Be precise with timestamps. Cover the entire audio — do not skip any speech.
"""),
    ]

    response = client.models.generate_content(
        model=MODEL,
        contents=types.Content(parts=parts),
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=Transcription,
        ),
    )

    return Transcription.model_validate_json(response.text)


# ── Example usage ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "test.mp4"
    result = transcribe_video(path)
    for seg in result.segments:
        print(f"[{seg.start} → {seg.end}]  {seg.text}")
