import os
import subprocess
import tempfile
import json

from agents.editor import create_edit_plan
from agents.transcriber import transcribe_video
from agents.music import generate_music
from tools.clip import clip
from tools.tts import tts
from tools.cap import build_caption_ass, burn_captions_from_ass, Caption, word_highlight, word_reveal

ROOT_PATH = '/Users/projectcoordinator/Desktop/videoai/videos'
OUTPUT    = 'test.mp4'
QUERY     = 'a concept in Mathematics of Emergent Computation in Distributed Systems explained in 15 seconds'

clips = [os.path.join(ROOT_PATH, f'clip{i}.mp4') for i in range(1, 9)]


# ── 1. Get edit plan ─────────────────────────────────────────────────────────────

plan = create_edit_plan(QUERY, clips)

# ── 2. Cut each scene ────────────────────────────────────────────────────────────

with tempfile.TemporaryDirectory() as tmp:
    scene_paths = []

    for scene in sorted(plan.scenes, key=lambda s: s.index):
        src = clips[int(scene.clip_key)]
        dst = os.path.join(tmp, f"scene_{scene.index:03d}.mp4")

        print(f"[{scene.index}] {os.path.basename(src)}  {scene.clip_start} → {scene.clip_end}")
        clip(src, scene.clip_start, scene.clip_end, dst)
        scene_paths.append(dst)

    # ── 3. Concatenate into final video ──────────────────────────────────────────

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        for path in scene_paths:
            f.write(f"file '{path}'\n")
        list_file = f.name

    subprocess.run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", list_file,
        "-c", "copy",
        OUTPUT,
    ], check=True, capture_output=True)
    os.unlink(list_file)


# ── 4. TTS + mix narration ────────────────────────────────────────────────────────

with tempfile.TemporaryDirectory() as tmp:
    wav_paths = []
    for scene in sorted(plan.scenes, key=lambda s: s.index):
        dst = os.path.join(tmp, f"narration_{scene.index:03d}.wav")
        print(f"[TTS] scene {scene.index}: {scene.narration[:60]}")
        tts(scene.narration, dst)
        wav_paths.append(dst)

    # concat all wavs into one narration track
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        for path in wav_paths:
            f.write(f"file '{path}'\n")
        wav_list = f.name

    narration_wav = "narration.wav"
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", wav_list,
        narration_wav,
    ], check=True, capture_output=True)
    os.unlink(wav_list)

# mix narration onto the video
subprocess.run([
    "ffmpeg", "-y",
    "-i", OUTPUT,
    "-i", narration_wav,
    "-map", "0:v",
    "-map", "1:a",
    "-c:v", "copy",
    "-c:a", "aac",
    "-shortest",
    "final_with_narration.mp4",
], check=True, capture_output=True)

# ── 5. Transcribe narrated video and burn captions ───────────────────────────────

print("Transcribing narration...")
transcription = transcribe_video("final_with_narration.mp4")

captions = [
    Caption(text=seg.text, start=seg.start, end=seg.end)
    for seg in transcription.segments
]

caption_ass = build_caption_ass(captions)
burn_captions_from_ass("final_with_narration.mp4", 'final_captioned.mp4', caption_ass)

# ── 6. Generate background music and mix in ──────────────────────────────────────

# get video duration via ffprobe
probe = subprocess.run([
    "ffprobe", "-v", "error",
    "-show_entries", "format=duration",
    "-of", "default=noprint_wrappers=1:nokey=1",
    "final_captioned.mp4",
], capture_output=True, text=True, check=True)
duration = float(probe.stdout.strip())

print(f"Generating {duration:.1f}s of background music...")
generate_music('Upbeat motivation background music', duration=duration, dst="bg_music.wav")

# mix: narration at full volume, music at -8dB
subprocess.run([
    "ffmpeg", "-y",
    "-i", "final_captioned.mp4",
    "-i", "bg_music.wav",
    "-filter_complex", "[1:a]volume=-8dB[music];[0:a][music]amix=inputs=2:duration=first[aout]",
    "-map", "0:v",
    "-map", "[aout]",
    "-c:v", "copy",
    "-c:a", "aac",
    "final_output.mp4",
], check=True, capture_output=True)

print("\nDone → final_output.mp4")
