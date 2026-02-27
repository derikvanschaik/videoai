import os
import subprocess
import tempfile
import json

from agents.editor import create_edit_plan
from agents.transcriber import transcribe_video
from tools.clip import clip
from tools.tts import tts
from tools.cap import build_caption_ass, burn_captions_from_ass, Caption

ROOT_PATH = '/Users/projectcoordinator/Desktop/videoai/videos'
OUTPUT    = 'test.mp4'
QUERY     = '5 simple beginner programming projects'

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
burn_captions_from_ass("final_with_narration.mp4", 'final_output.mp4', caption_ass)

print("\nDone → final_output.mp4")
