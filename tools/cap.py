import os
import subprocess
from typing import NamedTuple, List, Callable


class Caption(NamedTuple):
    text:  str
    start: str  # MM:SS
    end:   str  # MM:SS


# ── Timestamp helpers ─────────────────────────────────────────────────────────

def mm_ss_to_ass(ts: str) -> str:
    """Convert MM:SS to ASS timestamp format H:MM:SS.cc"""
    return _seconds_to_ass(_to_seconds(ts))

def _to_seconds(ts: str) -> float:
    m, s = ts.strip().split(":")
    return int(m) * 60 + float(s)

def _seconds_to_ass(sec: float) -> str:
    h   = int(sec // 3600)
    m   = int((sec % 3600) // 60)
    s   = sec % 60
    return f"{h}:{m:02d}:{s:05.2f}"


# ── Effects ───────────────────────────────────────────────────────────────────
# Each effect takes a Caption and returns a list of ASS Dialogue lines.
# Add new effects here — no other code needs to change.

EffectFn = Callable[[Caption], List[str]]


def plain(caption: Caption) -> List[str]:
    """One line, whole text, no frills."""
    t0 = _seconds_to_ass(_to_seconds(caption.start))
    t1 = _seconds_to_ass(_to_seconds(caption.end))
    return [f"Dialogue: 0,{t0},{t1},Default,,0,0,0,,{caption.text}"]


def word_highlight(caption: Caption) -> List[str]:
    """Karaoke-style: all words visible, current word fills to yellow as it's spoken."""
    words = caption.text.split()
    if not words:
        return plain(caption)

    t0 = _seconds_to_ass(_to_seconds(caption.start))
    t1 = _seconds_to_ass(_to_seconds(caption.end))

    dur_cs  = int((_to_seconds(caption.end) - _to_seconds(caption.start)) * 100)
    per_cs  = max(1, dur_cs // len(words))

    # \kf = fill-style karaoke (sweeps colour left-to-right over the word)
    tagged = "".join(f"{{\\kf{per_cs}}}{w} " for w in words).rstrip()
    return [f"Dialogue: 0,{t0},{t1},Default,,0,0,0,,{tagged}"]


def word_reveal(caption: Caption) -> List[str]:
    """Each word pops in as its own caption, evenly spaced across start→end."""
    words = caption.text.split()
    if not words:
        return plain(caption)

    start  = _to_seconds(caption.start)
    end    = _to_seconds(caption.end)
    step   = (end - start) / len(words)

    lines = []
    for i, word in enumerate(words):
        t0 = _seconds_to_ass(start + i * step)
        t1 = _seconds_to_ass(start + (i + 1) * step)
        lines.append(f"Dialogue: 0,{t0},{t1},Default,,0,0,0,,{word}")
    return lines


# ── Builder ───────────────────────────────────────────────────────────────────

def build_caption_ass(captions: List[Caption], effect: EffectFn = plain) -> str:
    header = """\
[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Alignment, BorderStyle, Outline, Shadow
Style: Default,Manrope,90,&H00FFFFFF,&H0000FFFF,&H00000000,&H00000000,1,5,1,4,0

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    for caption in captions:
        events.extend(effect(caption))

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
