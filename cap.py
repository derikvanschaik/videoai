import os
import json
import subprocess

# ASS anchor (\an) — numpad layout, 1080x1920 coordinate space:
#   7  8  9   ← top
#   4  5  6   ← middle
#   1  2  3   ← bottom
#
# \an8 = top-center:    \pos(540, 480)  → upper third, lines grow downward  (good default)
# \an2 = bottom-center: \pos(540, 1820) → near bottom,  lines grow upward


def build_caption(text_effect="replace") -> str:
    header = """\
[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, Bold, Alignment
Style: Default,Manrope,160,&H00FFFFFF,1,2

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    # (word, style_tag, t0, t1)
    words = [
        ("GRWM", rf"{{\fnImpact\fs60\b1\i1&H00FFFFFF&}}", "0:00:00.00", "0:00:01.00"),
        ("day",  rf"{{\fnImpact\fs60\b1\i1&H00FFFFFF&}}", "0:00:01.00", "0:00:02.00"),
        ("in",   rf"{{\fnImpact\fs60\b1\i1&H00FFFFFF&}}", "0:00:02.00", "0:00:03.00"),
        ("the",  rf"{{\fnImpact\fs60\b1\i1&H00FFFFFF&}}", "0:00:03.00", "0:00:04.00"),
        ("life", rf"{{\fnImpact\fs60\b1\i1&H00FFFFFF&}}", "0:00:04.00", "0:00:06.00"),
    ]
    events = []

    pos_tag = r"{\an8\pos(540,480)}"

    if text_effect == "all_at_once":

        text = pos_tag + " ".join(style + word for word, style, _, _ in words)
        events.append(f"Dialogue: 0,{words[0][2]},{words[-1][3]},Default,,0,0,0,,{text}")

    elif text_effect == "replace":

        for word, style, t0, t1 in words:
            text = pos_tag + style + word
            events.append(f"Dialogue: 0,{t0},{t1},Default,,0,0,0,,{text}")

    else:  # append
        seperator = ""

        if text_effect == "append_new_line":
            seperator = r"\N"
        
        # (append_same_line)
        else:
            seperator = r" "
            # Notice that we want to anchor left aligned: an7 (and by default need to change the position heres)
            pos_tag = r"{\an7\pos(300,480)}"

        for j in range(len(words)):
            _, _, t0, t1 = words[j]
            text = pos_tag + seperator.join(style + word for word, style, _, _ in words[:j + 1])
            events.append(f"Dialogue: 0,{t0},{t1},Default,,0,0,0,,{text}")

    return header + "\n".join(events) + "\n"


def burn_captions(src: str, dst: str, ass_content: str) -> None:
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


words = "Here is how I built my startup at 16 with no help from my parents".split()
ass   = build_caption(text_effect="append_same_line")
burn_captions('./videos/clip1.mp4', './caption-debug.mp4', ass)