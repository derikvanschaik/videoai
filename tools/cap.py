import os
import random
import subprocess

# ASS anchor (\an) — numpad layout, 1080x1920 coordinate space:
#   7  8  9   ← top
#   4  5  6   ← middle
#   1  2  3   ← bottom
#
# \an8 = top-center:    \pos(540, 480)  → upper third, lines grow downward  (good default)
# \an2 = bottom-center: \pos(540, 1820) → near bottom,  lines grow upward


# Position presets — (anchor_tag, MarginL, MarginR, MarginV)
# Horizontal: center anchors (\an8/\an2) use L/R as wrap boundaries.
#             side anchors (\an7/\an9) use L or R as distance from that edge.
# Vertical:   \an8/\an7/\an9 → MarginV = distance from TOP
#             \an2/\an1/\an3 → MarginV = distance from BOTTOM
POSITIONS = {
    "top_center":    (r"\an8", 80, 80, 300),
    "upper_center":  (r"\an8", 80, 80, 500),
    "lower_center":  (r"\an2", 80, 80, 300),
    "bottom_center": (r"\an2", 80, 80, 100),
    "top_left":      (r"\an7", 60, 80, 300),
    "top_right":     (r"\an9", 80, 60, 300),
}


def build_caption_ass(lines) -> str:
    """
    lines: List of (words, t0, t1) where t0 and t2 "0:00:00.00", "0:00:01.00"
    """
    header = """\
[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, Bold, Alignment
Style: Default,Manrope,160,&H00FFFFFF,1,2

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    # (word, t0, t1) # "0:00:00.00", "0:00:01.00"
    font = "Manrope"
    font_size = 110
    bold = True
    color = "00FFFFFF"  # white (BGR format, no H prefix)

    text_styles = (
        r"{\fn" + font +
        r"\fs" + str(font_size) +
        r"\b" + ("1" if bold else "0") +
        r"\i0" + # italic 
        r"\c&H" + color + r"&}"
    )
    # Result: {\fnManrope\fs160\b1\i0\c&H00FFFFFF&}

    lines = [
        (l[0], text_styles, l[1], l[2])
        for l in lines
    ]
    events = []

    position = "upper_center"
    an, ml, mr, mv = POSITIONS[position]
    pos_tag = "{" + an + "}"
    margins = f"{ml},{mr},{mv}"

    # This creates that text on different lines effect
    for j in range(len(lines)):
        _, _, t0, t1 = lines[j]
        text = pos_tag + r"\N".join(style + word for word, style, _, _ in lines[:j + 1])
        events.append(f"Dialogue: 0,{t0},{t1},Default,,{margins},,{text}")

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


position = random.choice(list(POSITIONS.keys()))
print(f"Using position: {position}")
ass = build_caption_ass(
    [
        ("10 programming languages", "0:00:00.00", "0:00:01.00"),
        ("you need to know ", "0:00:01.00", "0:00:03.00"),
        ("by 2026", "0:00:03.00", "0:00:05.00"), 
     ]
    )
burn_captions_from_ass('./videos/clip1.mp4', './caption-debug.mp4', ass)