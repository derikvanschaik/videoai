import argparse
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

parser = argparse.ArgumentParser(description="AI video pipeline")
parser.add_argument("query", help="Prompt describing the desired video")
parser.add_argument(
    "videos", nargs="+",
    help="Video files or a single directory containing video files",
)
args = parser.parse_args()

QUERY = args.query
# write filenames for each step to clean up after
CONCAT = "concat.mp4"
NARRARATION = "narration.wav"
FINAL_NARRARATION = "final_with_narration.mp4"
FINAL_CAPTION = "final_with_caption.mp4"
FINAL_OUTPUT = "final_out.mp4"
LYRIA_FILE = "bg_music.wav"

# Resolve video list: accept either a directory or explicit file paths
if len(args.videos) == 1 and os.path.isdir(args.videos[0]):
    video_dir = args.videos[0]
    clips = sorted(
        os.path.join(video_dir, f)
        for f in os.listdir(video_dir)
        if f.lower().endswith(".mp4")
    )
else:
    clips = args.videos


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
        CONCAT
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

    subprocess.run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", wav_list,
        NARRARATION
    ], check=True, capture_output=True)
    os.unlink(wav_list)

# mix narration onto the video
subprocess.run([
    "ffmpeg", "-y",
    "-i", "concat.mp4",
    "-i", NARRARATION,
    "-map", "0:v",
    "-map", "1:a",
    "-c:v", "copy",
    "-c:a", "aac",
    "-shortest",
    FINAL_NARRARATION
], check=True, capture_output=True)

# ── 5. Transcribe narrated video and burn captions ───────────────────────────────

print("Transcribing narration...")
transcription = transcribe_video(FINAL_NARRARATION)

captions = [
    Caption(text=seg.text, start=seg.start, end=seg.end)
    for seg in transcription.segments
]

caption_ass = build_caption_ass(captions)
burn_captions_from_ass(FINAL_NARRARATION, FINAL_CAPTION, caption_ass)

# ── 6. Generate background music and mix in ──────────────────────────────────────

# get video duration via ffprobe
probe = subprocess.run([
    "ffprobe", "-v", "error",
    "-show_entries", "format=duration",
    "-of", "default=noprint_wrappers=1:nokey=1",
    FINAL_CAPTION,
], capture_output=True, text=True, check=True)
duration = float(probe.stdout.strip())

print(f"Generating {duration:.1f}s of background music...")
print(f"Music style: {plan.music_prompt}")
generate_music(plan.music_prompt, duration=duration, dst=LYRIA_FILE)

# mix: narration at full volume, music at -8dB
subprocess.run([
    "ffmpeg", "-y",
    "-i", FINAL_CAPTION,
    "-i", LYRIA_FILE,
    "-filter_complex", "[1:a]volume=-8dB[music];[0:a][music]amix=inputs=2:duration=first[aout]",
    "-map", "0:v",
    "-map", "[aout]",
    "-c:v", "copy",
    "-c:a", "aac",
    FINAL_OUTPUT,
], check=True, capture_output=True)

print(f"\nDone → {FINAL_OUTPUT}")

# CLEAN UP FILES
os.remove(CONCAT)
os.remove(NARRARATION)
os.remove(FINAL_NARRARATION)
os.remove(FINAL_CAPTION)
os.remove(LYRIA_FILE)

