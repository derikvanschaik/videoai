import subprocess
import os


def build_caption():
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
    lines = [
        ("Hello",   "Manrope", 160, 540, 800,  2),
        ("World",   "Georgia", 120, 300, 1100, 8),
        ("Whatsup", "Impact",  200, 700, 600,  5),
    ]
    events = []

    for l, font, size, x, y, anchor in lines:
        text = rf"{{\an{anchor}\pos({x},{y})\fn{font}\fs{size}\b1\c&HFFFFFF&}}" + l
        events.append(f"Dialogue: 0,0:00:00.00,0:00:06.00,Default,,0,0,0,,{text}")

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


ass = build_caption()
burn_captions('./videos/clip1.mp4', './caption-debug.mp4', ass)