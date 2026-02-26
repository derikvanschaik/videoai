import os
import subprocess
import tempfile

from agents.editor import create_edit_plan
from tools.clip import clip

ROOT_PATH = '/Users/projectcoordinator/Desktop/videoai/videos'
OUTPUT    = 'test.mp4'
QUERY     = '5 simple beginner programming projects'

clips = [os.path.join(ROOT_PATH, f'clip{i}.mp4') for i in range(1, 9)]


def mm_ss_to_sec(ts: str) -> float:
    m, s = ts.strip().split(":")
    return int(m) * 60 + float(s)

def sec_to_mm_ss(s: float) -> str:
    m = int(s) // 60
    return f"{m:02d}:{s - m * 60:06.3f}"


# ── 1. Get edit plan ─────────────────────────────────────────────────────────────

plan = create_edit_plan(QUERY, clips)

# ── 2. Cut each scene ────────────────────────────────────────────────────────────

with tempfile.TemporaryDirectory() as tmp:
    scene_paths = []

    for scene in sorted(plan.scenes, key=lambda s: s.index):
        src   = clips[int(scene.clip_key)]
        start = scene.clip_start
        end   = sec_to_mm_ss(mm_ss_to_sec(start) + scene.duration)
        dst   = os.path.join(tmp, f"scene_{scene.index:03d}.mp4")

        print(f"[{scene.index}] {os.path.basename(src)}  {start} → {end}  ({scene.duration:.1f}s)")
        clip(src, start, end, dst)
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

print(f"\nDone → {OUTPUT}")
