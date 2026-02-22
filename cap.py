import os
import subprocess
from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel

load_dotenv()

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

_MODEL_COSTS = {
    "gemini-3.1-pro-preview": {
        "input_per_1m":        2.00,
        "input_per_1m_long":   4.00,
        "output_per_1m":      12.00,
        "output_per_1m_long": 18.00,
        "threshold":         200_000,
    },
}

def log_cost(fn_name: str, model: str, response) -> float:
    usage   = getattr(response, "usage_metadata", None)
    in_tok  = getattr(usage, "prompt_token_count",     0) or 0
    out_tok = getattr(usage, "candidates_token_count", 0) or 0

    pricing   = _MODEL_COSTS.get(model, {})
    threshold = pricing.get("threshold", 0)
    if threshold and in_tok > threshold:
        in_rate  = pricing.get("input_per_1m_long",  pricing.get("input_per_1m",  0))
        out_rate = pricing.get("output_per_1m_long", pricing.get("output_per_1m", 0))
        tier     = ">200k tier"
    else:
        in_rate  = pricing.get("input_per_1m",  0)
        out_rate = pricing.get("output_per_1m", 0)
        tier     = "≤200k tier"

    in_cost  = in_tok  / 1_000_000 * in_rate
    out_cost = out_tok / 1_000_000 * out_rate
    total    = in_cost + out_cost

    print(f"[cost/{fn_name}] {in_tok:,} in + {out_tok:,} out ({tier}) → ${total:.6f}")
    return total


class CaptionWord(BaseModel):
    text:   str
    font:   str
    size:   int
    x:      int
    y:      int
    anchor: int
    color:  str

class CaptionPlan(BaseModel):
    words: list[CaptionWord]


def ask_gemini(video: str, words: list[str], ref_video: str | None = None) -> CaptionPlan:
    parts = []

    if ref_video:
        parts.append(types.Part(text="REFERENCE IMAGE (match this caption style):"))
        parts.append(types.Part(
            inline_data=types.Blob(data=open(ref_video, "rb").read(), mime_type="image/png")
        ))

    parts.append(types.Part(text="TARGET VIDEO (place captions on this):"))
    parts.append(types.Part(
        inline_data=types.Blob(data=open(video, "rb").read(), mime_type="video/mp4")
    ))

    parts.append(types.Part(text=f"""
You are a motion graphics artist placing captions on a video.

{"Study the reference image's caption style — font choices, sizes, positioning, color — and match it on the target video." if ref_video else ""}

Words to place: {words}

The coordinate space is 1080x1920. For each word decide:
- font: choose from Manrope, Georgia, Impact, Arial, Helvetica
- size: 80-200 depending on emphasis
- x/y: position — keep x between 100-980 and y between 100-1820
- anchor: numpad position (2=bottom-center, 5=center, 8=top-center)
- color: ASS format e.g. \\c&HFFFFFF& for white

"""))

    model = "gemini-3.1-pro-preview"
    response = client.models.generate_content(
        model=model,
        contents=types.Content(parts=parts),
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=CaptionPlan,
        ),
    )
    log_cost("ask_gemini", model, response)
    return CaptionPlan.model_validate_json(response.text)


def build_caption(plan: CaptionPlan) -> str:
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
    events = []
    for w in plan.words:
        text = rf"{{\an{w.anchor}\pos({w.x},{w.y})\fn{w.font}\fs{w.size}\b1{w.color}}}" + w.text
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


# typing issue here lol
words = [word for word in "Here is how I built my startup at 16 with no help from my parents".split(" ") ]

plan  = ask_gemini('./videos/clip1.mp4', words, ref_video='ref.png')
ass   = build_caption(plan)
burn_captions('./videos/clip1.mp4', './caption-debug.mp4', ass)