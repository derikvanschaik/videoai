import os
import subprocess
from typing import NamedTuple, List


class Caption(NamedTuple):
    text:  str
    start: str  # MM:SS
    end:   str  # MM:SS


def mm_ss_to_ass(ts: str) -> str:
    """Convert MM:SS to ASS timestamp format H:MM:SS.cc"""
    m, s = ts.strip().split(":")
    total_sec = int(m) * 60 + float(s)
    h   = int(total_sec // 3600)
    m   = int((total_sec % 3600) // 60)
    sec = total_sec % 60
    return f"{h}:{m:02d}:{sec:05.2f}"


def build_caption_ass(captions: List[Caption]) -> str:
    """
    Each caption is displayed as a single line of text centered on screen
    for the duration of start → end. Dead simple, one Dialogue event per caption.

    # TODO: implement word-by-word reveal (the "stacking lines" effect below).
    # The idea: split narration into chunks, emit one Dialogue event per chunk
    # where each event shows all chunks up to and including the current one,
    # creating the effect of words appearing line by line as the narrator speaks.
    # POSITIONS dict and the _build_lines loop (see git history) are the building
    # blocks for this — restore them when ready to implement.
    """
    header = """\
[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, Bold, Alignment
Style: Default,Manrope,60,&H00FFFFFF,1,5

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    for caption in captions:
        t0   = mm_ss_to_ass(caption.start)
        t1   = mm_ss_to_ass(caption.end)
        events.append(f"Dialogue: 0,{t0},{t1},Default,,0,0,0,,{caption.text}")

    return header + "\n".join(events) + "\n"


def burn_captions_from_ass(src: str, dst: str, ass_content: str) -> None:
    ass_tmp_path = src.replace(".mp4", "_captions.ass")

    with open(ass_tmp_path, "w") as f:
        f.write(ass_content)

    try:
        subprocess.run([
            "ffmpeg", "-y",
            "-i", src,
            "-vf", f"ass={ass_tmp_path}:fontsdir={os.path.abspath('./')}",
            "-c:v", "libx264",
            "-c:a", "copy",
            dst,
        ], check=True)
    finally:
        os.unlink(ass_tmp_path)
