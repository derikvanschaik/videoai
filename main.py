from google import genai
from google.genai import types
from dotenv import load_dotenv
from pydantic import BaseModel
import os
import subprocess
import tempfile
import random

load_dotenv()

GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

WORDS_PER_CAP = 4
STAGGER_S     = 0.09
YELLOW_CHANCE = 0.35
FONTS_DIR     = os.path.dirname(os.path.abspath(__file__))  # dir containing Manrope.ttf

WHITE  = r"\c&HFFFFFF&"
YELLOW = r"\c&H00FFFF&"

client = genai.Client(api_key=GEMINI_API_KEY)


# ── Models ────────────────────────────────────────────────────────────────────

class Word(BaseModel):
    text:     str
    start:    float
    end:      float
    emphasis: bool

class Transcript(BaseModel):
    words: list[Word]


# ── Steps ─────────────────────────────────────────────────────────────────────

def transcribe(video_bytes: bytes) -> Transcript:
    response = client.models.generate_content(
        model="gemini-2.5-pro",
        contents=types.Content(parts=[
            types.Part(inline_data=types.Blob(data=video_bytes, mime_type="video/mp4")),
            types.Part(text=(
                "Transcribe every spoken word in this video with accurate timestamps. "
                "For each word set 'emphasis' to true if it carries meaning or impact "
                "(nouns, verbs, adjectives, names, numbers, key phrases), "
                "or false if it is a filler or function word (a, the, is, and, to, of, I, it, etc.)."
            )),
        ]),
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=Transcript,
        ),
    )
    return Transcript.model_validate_json(response.text)


def to_ass_time(s: float) -> str:
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    s = s % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def build_lines(chunk: list[Word]) -> list[str]:
    lines, sm_buf = [], []
    for w in chunk:
        if not w.emphasis:
            sm_buf.append(w.text.upper())
        else:
            if sm_buf:
                lines.append(r"{\fnManrope\fs44\b0\fsp10\c&HFFFFFF&}" + "  ".join(sm_buf))
                sm_buf = []
            color = YELLOW if random.random() < YELLOW_CHANCE else WHITE
            lines.append(rf"{{\fnManrope\fs160\b1\fsp0{color}}}" + w.text.lower())
    if sm_buf:
        lines.append(r"{\fnManrope\fs44\b0\fsp10\c&HFFFFFF&}" + "  ".join(sm_buf))
    return lines or [r"{\fnManrope\fs160\b1\c&HFFFFFF&}" + chunk[0].text.lower()]


def build_ass(transcript: Transcript) -> str:
    header = """\
[Script Info]
ScriptType: v4.00+
WrapStyle: 0
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Manrope,160,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,0,0,5,40,40,40,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    words  = transcript.words
    for i in range(0, len(words), WORDS_PER_CAP):
        chunk       = words[i : i + WORDS_PER_CAP]
        chunk_start = chunk[0].start
        chunk_end   = chunk[-1].end
        lines       = build_lines(chunk)
        for j in range(len(lines)):
            t0   = chunk_start + j * STAGGER_S
            t1   = chunk_start + (j + 1) * STAGGER_S if j < len(lines) - 1 else chunk_end
            text = r"\N".join(lines[: j + 1])
            events.append(f"Dialogue: 0,{to_ass_time(t0)},{to_ass_time(t1)},Default,,0,0,0,,{text}")
    return header + "\n".join(events) + "\n"


def add_captions(video_bytes: bytes) -> bytes:
    """
    Takes raw video bytes, returns captioned video bytes.
    No permanent files written to disk.
    """
    print("Transcribing...")
    transcript = transcribe(video_bytes)
    print(f"  {len(transcript.words)} words")

    ass_content = build_ass(transcript)

    # ASS filter requires a file path — use a self-cleaning temp file
    with tempfile.NamedTemporaryFile(suffix=".ass", delete=True, mode="w") as ass_tmp:
        ass_tmp.write(ass_content)
        ass_tmp.flush()

        print("Rendering...")
        result = subprocess.run([
            "ffmpeg", "-y",
            "-i", "pipe:0",
            "-vf", f"ass={ass_tmp.name}:fontsdir={FONTS_DIR}",
            "-c:v", "libx264",
            "-c:a", "aac",
            "-movflags", "frag_keyframe+empty_moov",  # fragmented MP4 — pipeable
            "-f", "mp4",
            "pipe:1",
        ], input=video_bytes, capture_output=True, check=True)

    return result.stdout


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    INPUT_VIDEO  = "3.mp4"
    OUTPUT_VIDEO = "clip_edited.mp4"

    video_bytes = open(INPUT_VIDEO, "rb").read()
    output      = add_captions(video_bytes)

    with open(OUTPUT_VIDEO, "wb") as f:
        f.write(output)

    print(f"Done: {OUTPUT_VIDEO}")
