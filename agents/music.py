"""
Music agent — prompt + duration → WAV file via Lyria RealTime.

Output is converted from raw 16-bit PCM (48 kHz stereo) to a WAV file.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

client = genai.Client(
    api_key=os.getenv("GEMINI_API_KEY"),
    http_options={"api_version": "v1alpha"},
)
MODEL = "models/lyria-realtime-exp"


async def _generate_pcm(prompt: str, duration: float) -> bytes:
    chunks: list[bytes] = []

    async def _receive(session):
        async for message in session.receive():
            if message.server_content and message.server_content.audio_chunks:
                chunks.append(message.server_content.audio_chunks[0].data)

    async with client.aio.live.music.connect(model=MODEL) as session:
        await session.set_weighted_prompts(
            prompts=[types.WeightedPrompt(text=prompt, weight=1.0)]
        )
        await session.set_music_generation_config(
            config=types.LiveMusicGenerationConfig(temperature=1.0)
        )
        await session.play()

        receive_task = asyncio.create_task(_receive(session))
        await asyncio.sleep(duration)
        receive_task.cancel()
        try:
            await receive_task
        except asyncio.CancelledError:
            pass

    return b"".join(chunks)


def generate_music(prompt: str, duration: float, dst: str) -> str:
    """Generate background music and save as a WAV file at dst."""
    pcm = asyncio.run(_generate_pcm(prompt, duration))

    with tempfile.NamedTemporaryFile(suffix=".pcm", delete=False) as f:
        f.write(pcm)
        pcm_path = f.name

    try:
        subprocess.run([
            "ffmpeg", "-y",
            "-f", "s16le",   # signed 16-bit little-endian PCM
            "-ar", "48000",  # 48 kHz
            "-ac", "2",      # stereo
            "-i", pcm_path,
            dst,
        ], check=True, capture_output=True)
    finally:
        os.unlink(pcm_path)

    return dst


# ── Example usage ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    generate_music("upbeat lo-fi hip hop", duration=30, dst="bg_music.wav")
    print("Saved → bg_music.wav")
