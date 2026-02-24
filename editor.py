"""
Four-agent video editing pipeline
────────────────────────────────────
  Agent 0 — Style Analyst    (gemini-3.1-pro):   reference video → StyleGuide
  Agent 1 — Scripter         (gemini-3.1-pro):   query + clips + StyleGuide → Script
  Agent 2 — TTS              (gemini-2.5-flash):  each narration segment → per-segment WAVs
                                                  measured durations passed to editor as hard constraints
  Agent 3 — Editor           (gemini-3.1-pro):   Script + clips + exact durations + StyleGuide → EditPlan
  Agent 4 — Caption Planner  (gemini-3.1-pro):   final video + segment ranges → CaptionPlan (positions)
  Agent 5 — Transcriber      (gemini-2.5-pro):   final video audio → word-level Transcript
            then captions are built + burned using the plan + transcript

Sync guarantee: every scene's clip_end = clip_start + exact narration duration for that segment.
No guessing. Video and narration are always frame-locked.

Usage
─────
  python editor.py "<query>" [--ref reference.mp4] [clip1.mp4 clip2.mp4 ...]

  --ref is optional. When supplied the Style Analyst watches it first and the
  resulting StyleGuide is passed to the Scripter and Editor so they match its
  pacing, energy, and color style.

  If no clips are given, every *.mp4 in the current directory is used.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import random
import shutil
import subprocess
import tempfile
import urllib.request
import wave

from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel

load_dotenv()

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

STYLE_MODEL    = os.getenv("STYLE_MODEL",    "gemini-3.1-pro-preview")
SCRIPTER_MODEL = os.getenv("SCRIPTER_MODEL", "gemini-3.1-pro-preview")
TTS_MODEL      = os.getenv("TTS_MODEL",      "gemini-2.5-flash-preview-tts")
EDITOR_MODEL   = os.getenv("EDITOR_MODEL",   "gemini-3.1-pro-preview")
CAPTION_MODEL  = os.getenv("CAPTION_MODEL",  "gemini-2.5-pro")
TTS_VOICE      = os.getenv("TTS_VOICE",      "Kore")
CLONE_TTS_URL  = os.getenv("CLONE_TTS_URL",  "http://localhost:8765/generate")

# ── Cost tracking ───────────────────────────────────────────────────────────────
_MODEL_COSTS: dict[str, dict] = {
    "gemini-3.1-pro-preview": {
        "input_per_1m":        2.00,
        "input_per_1m_long":   4.00,
        "output_per_1m":      12.00,
        "output_per_1m_long": 18.00,
        "threshold":         200_000,
    },
    "gemini-2.5-flash-preview-05-20": {
        "input_per_1m":  0.15,
        "output_per_1m": 0.60,
    },
    "gemini-2.5-flash-preview-tts": {
        "input_per_1m":   0.50,
        "output_per_1m": 10.00,
    },
    "gemini-2.5-pro": {
        "input_per_1m":        1.25,
        "input_per_1m_long":   2.50,
        "output_per_1m":      10.00,
        "output_per_1m_long": 15.00,
        "threshold":         200_000,
    },
}

_cost_log: list[dict] = []


def _log_cost(fn_name: str, model: str, response) -> float:
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

    _cost_log.append({"fn": fn_name, "model": model, "in_tok": in_tok, "out_tok": out_tok, "cost": total})
    print(
        f"  [cost/{fn_name}] {in_tok:,} in + {out_tok:,} out tokens  ({tier})"
        f" → in=${in_cost:.6f}  out=${out_cost:.6f}  total=${total:.6f}"
    )
    return total


W             = int(os.getenv("VIDEO_WIDTH",  "576"))
H             = int(os.getenv("VIDEO_HEIGHT", "1024"))
OUTPUT_VIDEO  = os.getenv("OUTPUT_VIDEO",  "final_edit.mp4")
NARRATION_WAV = os.getenv("NARRATION_WAV", "narration.wav")

# ── Per-scene visual effects (appended to the -vf chain in render_scene) ───────
EFFECTS: dict[str, str] = {
    "none":       "",
    "color_warm": "colorbalance=rs=0.1:gs=0.05:bs=-0.15",
    "color_cool": "colorbalance=rs=-0.1:gs=0.0:bs=0.15",
    "vignette":   "vignette=PI/4",
    "film_grain": "noise=alls=12:allf=t+u",
}

# ── Caption constants ───────────────────────────────────────────────────────────
WORDS_PER_CAP    = int(os.getenv("WORDS_PER_CAP",    "4"))
ALT_COLOR_CHANCE = float(os.getenv("ALT_COLOR_CHANCE", "0.35"))
FONTS_DIR        = os.path.dirname(os.path.abspath(__file__))


# ── Schemas ────────────────────────────────────────────────────────────────────

class StyleGuide(BaseModel):
    avg_clip_duration: float  # estimated seconds per cut in the reference
    energy:            str    # "high" | "medium" | "low"
    color_style:       str    # "warm" | "cool" | "neutral"
    mood:              str    # one-line atmosphere (e.g. "gritty and intense")
    pacing_note:       str    # freeform observation about rhythm and cut style

class ScriptBeat(BaseModel):
    index:             int
    narration:         str
    visual_direction:  str
    mood:              str

class Script(BaseModel):
    topic:   str
    hook:    str
    beats:   list[ScriptBeat]
    cta:     str

class Scene(BaseModel):
    segment_index: int
    clip_key:      str
    clip_start:    str
    note:          str
    effect:        str = "none"

class EditPlan(BaseModel):
    scenes: list[Scene]

class Word(BaseModel):
    text:     str
    start:    float
    end:      float
    emphasis: bool
    font: str

class SegmentCaption(BaseModel):
    segment_index: int
    x:      int    # horizontal anchor in PlayRes space (0-1080), 540 = centered
    y:      int    # vertical anchor in PlayRes space (0-1920)
    anchor: int    # ASS \an value — 2 = bottom-center (text grows up from y), 8 = top-center (text grows down from y)
    scale:  float  # font scale multiplier: 1.0 = normal, 0.75 = smaller for busy frames
    primary_color: str     #  r"\c&HFFFFFF&" something like this
    alt_color: str #  r"\c&HFFFFFF&" something like this

class CaptionAnalysis(BaseModel):
    """Single-call result: word-level transcription + precise per-segment caption placement."""
    words:    list[Word]
    segments: list[SegmentCaption]


# ── Agent 0: Style Analyst ─────────────────────────────────────────────────────

def analyze_style(reference_path: str) -> StyleGuide:
    print(f"[Style Analyst] Watching reference: {os.path.basename(reference_path)}")

    video_part = types.Part(
        inline_data=types.Blob(data=open(reference_path, "rb").read(), mime_type="video/mp4")
    )
    prompt = types.Part(text="""You are a video editor analyzing a reference edit.

Watch this video carefully and extract its editing style. Focus on:

1. PACING — count the number of cuts. Divide the total duration by the number of cuts
   to estimate avg_clip_duration in seconds. Fast edits = 1-3s, medium = 3-6s, slow = 6s+.

2. ENERGY — overall kinetic energy of the edit: "high", "medium", or "low".

3. COLOR STYLE — the dominant color grade: "warm" (golden/orange tones), "cool" (blue/teal tones),
   or "neutral" (balanced, no strong grade).

4. MOOD — one short phrase describing the atmosphere (e.g. "gritty and determined",
   "peaceful and reflective", "explosive and hype").

5. PACING NOTE — one or two sentences about the rhythm: does it hold on shots longer for
   emotion, cut rapidly on beats, use any notable patterns?

Be precise about avg_clip_duration — it will directly control how long each scene in the
new video is.""")

    response = client.models.generate_content(
        model=STYLE_MODEL,
        contents=types.Content(parts=[video_part, prompt]),
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=StyleGuide,
        ),
    )
    _log_cost("analyze_style", STYLE_MODEL, response)
    return StyleGuide.model_validate_json(response.text)


# ── Agent 1: Scripter ──────────────────────────────────────────────────────────

def write_script(query: str, clips: dict[str, str], style_guide: StyleGuide | None = None, lang: str | None = None) -> Script:
    print(f"[Scripter] Watching {len(clips)} clip(s) and drafting script for: '{query}'")

    def load(path: str) -> types.Part:
        return types.Part(
            inline_data=types.Blob(data=open(path, "rb").read(), mime_type="video/mp4")
        )

    parts: list[types.Part] = []
    for key, path in clips.items():
        parts.append(types.Part(text=f"CLIP {key} ({os.path.basename(path)}):"))
        parts.append(load(path))

    style_block = ""
    if style_guide:
        beat_guidance = (
            "Keep beats short and punchy — 1 or 2 sentences max."
            if style_guide.avg_clip_duration < 3
            else "Let beats breathe — 2 to 3 sentences each."
            if style_guide.avg_clip_duration > 5
            else "Medium-length beats — 2 sentences each."
        )
        beat_count = (
            "6–8 beats" if style_guide.avg_clip_duration < 3
            else "4–5 beats" if style_guide.avg_clip_duration > 5
            else "5–7 beats"
        )
        style_block = f"""
STYLE REFERENCE (match this editing style):
- Energy: {style_guide.energy}
- Mood: {style_guide.mood}
- Avg clip duration: {style_guide.avg_clip_duration:.1f}s → aim for {beat_count}
- Pacing: {style_guide.pacing_note}
- Tone guidance: {beat_guidance}

Write the script to match this energy and mood exactly.
"""

    parts.append(types.Part(text=f"""You are a creative director for short-form video content (think TikTok / Reels).

You have just watched the clips above. Use what you actually saw in the footage to ground your script.

Topic / query: {query}
{style_block}
Write a tight, engaging video script. Keep total runtime under 90 seconds.

Requirements:
- hook: single punchy spoken sentence to open cold
- beats: 4–8 distinct moments. Each beat has narration (spoken words), visual_direction (specific moment you saw in the clips), and mood.
- cta: memorable closing spoken line

IMPORTANT: narration fields will be read aloud by a TTS voice. Write natural spoken sentences only — no brackets, no directions, just what the narrator says.

visual_direction must reference specific things you actually saw in the clips (clip number, what was happening)."""))
    
    if lang is not None:
        parts.append(types.Part(text=f"\n Please generate this in the following language: {lang}"))

    response = client.models.generate_content(
        model=SCRIPTER_MODEL,
        contents=types.Content(parts=parts),
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=Script,
        ),
    )
    _log_cost("write_script", SCRIPTER_MODEL, response)
    return Script.model_validate_json(response.text)


# ── Agent 2: TTS (per segment) ─────────────────────────────────────────────────

def _tts_one(text: str, out_path: str, lang=None) -> float:
    """Synthesise a single text segment via the local voice-clone server, return duration in seconds."""
    payload_data = {"text": text}
    if lang:
        payload_data["lang_code"] = lang
    payload = json.dumps(payload_data).encode()
    req     = urllib.request.Request(
        CLONE_TTS_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read())

    server_path = result["file_path"]
    shutil.copy2(server_path, out_path)

    with wave.open(out_path, "rb") as wav:
        duration = wav.getnframes() / wav.getframerate()

    return duration


def _concat_wavs(paths: list[str], out_path: str):
    with wave.open(out_path, "wb") as out_wav:
        for i, p in enumerate(paths):
            with wave.open(p, "rb") as src:
                if i == 0:
                    out_wav.setparams(src.getparams())
                out_wav.writeframes(src.readframes(src.getnframes()))


def generate_narration(script: Script, lang=None) -> tuple[str, dict[int, float]]:
    """
    Generate TTS for every segment (hook + beats + cta) individually.

    Returns:
      narration_wav  — path to the full concatenated WAV
      durations      — {segment_index: duration_seconds}
    """
    segments: list[tuple[int, str, str]] = []
    segments.append((0, "hook", script.hook))
    for b in script.beats:
        segments.append((b.index, f"beat {b.index}", b.narration))
    cta_idx = max(idx for idx, _, _ in segments) + 1
    segments.append((cta_idx, "cta", script.cta))

    wav_paths: list[str]        = []
    durations: dict[int, float] = {}

    with tempfile.TemporaryDirectory() as tmp:
        for seg_idx, label, text in segments:
            seg_path = os.path.join(tmp, f"seg_{seg_idx:03d}.wav")
            print(f"[TTS] Segment {label}: \"{text[:60]}{'…' if len(text)>60 else ''}\"")
            dur = _tts_one(text, seg_path, lang=lang)
            durations[seg_idx] = dur
            wav_paths.append(seg_path)
            print(f"       → {dur:.2f}s")

        _concat_wavs(wav_paths, NARRATION_WAV)

    total = sum(durations.values())
    print(f"[TTS] Full narration: {total:.2f}s → {NARRATION_WAV}")
    return NARRATION_WAV, durations


# ── Agent 3: Editor ────────────────────────────────────────────────────────────

def plan_edit(
    script: Script,
    clips: dict[str, str],
    durations: dict[int, float],
    style_guide: StyleGuide | None = None,
) -> EditPlan:
    print(f"[Editor] Planning edit with exact narration timings...")

    def load(path: str) -> types.Part:
        return types.Part(
            inline_data=types.Blob(data=open(path, "rb").read(), mime_type="video/mp4")
        )

    cta_idx = max(durations.keys())
    seg_lines = [f"  seg 0 (hook)  → {durations[0]:.2f}s  \"{script.hook}\""]
    for b in script.beats:
        seg_lines.append(
            f"  seg {b.index} (beat) → {durations[b.index]:.2f}s  \"{b.narration}\"\n"
            f"          visual: {b.visual_direction}"
        )
    seg_lines.append(f"  seg {cta_idx} (cta)   → {durations[cta_idx]:.2f}s  \"{script.cta}\"")

    script_block = f"""SCRIPT: {script.topic}

SEGMENTS WITH EXACT NARRATION DURATIONS
(the video clip for each segment MUST be exactly this long)
{chr(10).join(seg_lines)}
"""

    parts: list[types.Part] = [types.Part(text=script_block)]
    for key, path in clips.items():
        parts.append(types.Part(text=f"CLIP {key} ({os.path.basename(path)}):"))
        parts.append(load(path))

    dur_list = "\n".join(
        f"  segment_index={idx}  exact_duration={dur:.2f}s"
        for idx, dur in sorted(durations.items())
    )

    style_block = ""
    if style_guide:
        preferred_effect = (
            "color_warm" if style_guide.color_style == "warm"
            else "color_cool" if style_guide.color_style == "cool"
            else "none"
        )
        style_block = f"""
STYLE REFERENCE (match this editing style):
- Energy: {style_guide.energy}
- Mood: {style_guide.mood}
- Color style: {style_guide.color_style} → lean toward "{preferred_effect}" effects
- Pacing: {style_guide.pacing_note}

Let this guide your effect choices and which visual moments you pick.
"""

    parts.append(types.Part(text=f"""
You are a professional video editor.

You have a script with {len(durations)} segments, each with an EXACT narration duration (above).
You have {len(clips)} raw video clip(s) to cut from.
{style_block}
Your job: for each segment, pick the best clip, the best clip_start timestamp, and the best visual effect.

CRITICAL RULES:
- Return exactly one Scene per segment — segment_index values: {sorted(durations.keys())}
- clip_key must be one of: {sorted(clips.keys())}
- clip_start in MM:SS format — pick a visually interesting moment that matches the visual_direction
- Do NOT pick clip_end — it is computed automatically as clip_start + exact_duration
- Make sure clip_start + exact_duration does not exceed the clip's length
- The note field explains why this clip moment fits the narration

AVAILABLE EFFECTS — set the `effect` field on each Scene:
  "none"        straight cut — use when raw footage speaks for itself
  "color_warm"  golden warm grade — triumphant, outdoor, hopeful, sunny moments
  "color_cool"  cool cinematic grade — moody, intense, focused, serious moments
  "vignette"    dark-edge vignette — adds drama and weight, good for emotional peaks
  "film_grain"  analog film texture — gritty, raw, documentary feel; pairs well with color_cool

Exact durations to satisfy:
{dur_list}

Think about pacing, visual storytelling, and emotional arc. Choose the single best moment and effect per segment.
"""))

    response = client.models.generate_content(
        model=EDITOR_MODEL,
        contents=types.Content(parts=parts),
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=EditPlan,
        ),
    )
    _log_cost("plan_edit", EDITOR_MODEL, response)
    return EditPlan.model_validate_json(response.text)


# ── Render helpers ─────────────────────────────────────────────────────────────

def mm_ss_to_seconds(ts: str) -> float:
    parts = ts.strip().split(":")
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    return float(parts[0])


def seconds_to_mm_ss(s: float) -> str:
    m = int(s) // 60
    sec = s - m * 60
    return f"{m:02d}:{sec:06.3f}"


def render_scene(
    scene: Scene,
    clips: dict[str, str],
    durations: dict[int, float],
    tmp_dir: str,
) -> str:
    out       = os.path.join(tmp_dir, f"scene_{scene.segment_index:03d}.mp4")
    clip_path = clips[scene.clip_key]
    duration  = durations[scene.segment_index]
    clip_end  = seconds_to_mm_ss(mm_ss_to_seconds(scene.clip_start) + duration)

    scale_filter = (
        f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
        f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2"
    )
    effect_filter = EFFECTS.get(scene.effect or "none", "")
    vf = f"{scale_filter},{effect_filter}" if effect_filter else scale_filter

    subprocess.run([
        "ffmpeg", "-y",
        "-ss", scene.clip_start,
        "-to", clip_end,
        "-i", clip_path,
        "-vf", vf,
        "-an",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-r", "30",
        "-avoid_negative_ts", "make_zero",
        out,
    ], check=True, capture_output=True)

    return out


def concat_and_narrate(scene_paths: list[str], narration_wav: str, output: str):
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        for path in scene_paths:
            f.write(f"file '{path}'\n")
        concat_list = f.name

    silent = output.replace(".mp4", "_silent.mp4")
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", concat_list,
        "-c", "copy",
        silent,
    ], check=True, capture_output=True)
    os.unlink(concat_list)

    subprocess.run([
        "ffmpeg", "-y",
        "-i", silent,
        "-i", narration_wav,
        "-map", "0:v",
        "-map", "1:a",
        "-c:v", "copy",
        "-c:a", "aac",
        "-shortest",
        output,
    ], check=True, capture_output=True)
    os.remove(silent)


# ── Caption pipeline ───────────────────────────────────────────────────────────

def analyze_captions(
    video_path: str,
    scenes: list[Scene],
    durations: dict[int, float],
) -> CaptionAnalysis:
    """
    Single API call: transcribe the narration AND decide precise caption placement
    per segment. One video upload, one response, half the cost.
    """
    print("[Captions] Transcribing + planning caption positions...")

    cumulative = 0.0
    seg_info: list[str] = []
    for s in sorted(scenes, key=lambda x: x.segment_index):
        dur = durations[s.segment_index]
        seg_info.append(
            f"  segment {s.segment_index}: {cumulative:.2f}s – {cumulative + dur:.2f}s"
        )
        cumulative += dur

    video_bytes = open(video_path, "rb").read()
    response = client.models.generate_content(
        model=CAPTION_MODEL,
        contents=types.Content(parts=[
            types.Part(inline_data=types.Blob(data=video_bytes, mime_type="video/mp4")),
            types.Part(text=f"""You are doing two things at once for this video:

━━ TASK 1 — TRANSCRIPTION ━━
Transcribe every spoken word with accurate timestamps.
For each word, set emphasis=true if it carries meaning (nouns, verbs, adjectives, names,
numbers, key phrases) or emphasis=false for filler/function words (a, the, is, and, to, of, etc.)
Choose a font for the word: Our default font is Manrope, but mix it up with other fonts native to ffmpeg.

━━ TASK 2 — CAPTION PLACEMENT ━━
The video has {len(scenes)} segments at these time ranges:
{chr(10).join(seg_info)}

The caption coordinate space is PlayResX=1080, PlayResY=1920.
Many clips are letterboxed landscape footage padded into a 9:16 frame — the actual video
content is in the CENTER BAND (roughly y=400 to y=1500 in PlayRes space). The top and bottom
regions may be black bars.

Your goal: place captions INSIDE the visual content area, overlaid on the footage — NOT in the
black bars. This is the viral TikTok / Reels style where text sits on top of the image.

For each segment, look carefully at the frame content and find EMPTY SPACE — sky, wall, floor,
background — where captions can sit without covering the subject's face or hands.

Fields to return per segment:

  x       — horizontal center (0–1080).
               540 = centered (default).
               Move left (e.g. 280) if the right side is occupied by a face/subject.
               Move right (e.g. 800) if the left side is occupied.

  y       — vertical anchor WITHIN the content area (aim for y=500–1450).
               Avoid black bar zones (y < 350 or y > 1550) unless content truly fills those areas.

  anchor  — 2 = bottom-center (text grows UP from y) — use for lower placements
             8 = top-center (text grows DOWN from y) — use for upper placements

  scale   — 1.0 = normal. Use 0.75 if the frame is very busy and captions would crowd the shot.

  primary_color   - The main caption color. Format: \c&HBBGGRR& where BB, GG, RR are two-digit hex values
                    for blue, green, and red channels respectively. Choose a color visible against the scene.

  alt_color       - A second accent color for emphasis words. Same format: \c&HBBGGRR&.
                    Should contrast or complement primary_color.

            

PLACEMENT STRATEGY (think like a motion graphics artist):
- Subject standing center-frame         → captions at top center (anchor=8, y=480)
- Subject in lower frame (walking etc.) → captions in upper third (anchor=8, y=420)
- Subject sitting / desk shot           → captions above their head (anchor=2, y=700–900)
- Wide landscape / nature shot          → captions lower-center on the image (anchor=2, y=1400)
- Subject fills most of frame           → captions in whichever corner has most empty space

NEVER place all segments at the same position — vary it meaningfully per scene.

Return one SegmentCaption per segment and the full word list.
"""),
        ]),
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=CaptionAnalysis,
        ),
    )
    _log_cost("analyze_captions", CAPTION_MODEL, response)
    result = CaptionAnalysis.model_validate_json(response.text)
    print(f"  {len(result.words)} words transcribed")
    for sc in sorted(result.segments, key=lambda s: s.segment_index):
        print(f"  seg {sc.segment_index}: pos=({sc.x},{sc.y}) anchor=\\an{sc.anchor} scale={sc.scale} primary={sc.primary_color!r} alt={sc.alt_color!r}")
    return result


def _to_ass_time(s: float) -> str:
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    s = s % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _build_lines(chunk: list[Word], primary_color: str, alt_color: str) -> list[tuple[str, float]]:
    lines, sm_buf, sm_start = [], [], None
    for w in chunk:
        if not w.emphasis:
            if sm_start is None:
                sm_start = w.start
            sm_buf.append(w.text.upper())
        else:
            if sm_buf:
                lines.append((rf"{{\fn{w.font}\fs44\b0\fsp10{primary_color}}}" + "  ".join(sm_buf), sm_start))
                sm_buf, sm_start = [], None
            color = alt_color if random.random() < ALT_COLOR_CHANCE else primary_color
            lines.append((rf"{{\fn{w.font}\fs160\b1\fsp0{color}}}" + w.text.lower(), w.start))
    if sm_buf:
        lines.append((rf"{{\fn{w.font}\fs44\b0\fsp10{primary_color}}}" + "  ".join(sm_buf), sm_start))
    if not lines:
        lines = [(rf"{{\fn{w.font}\fs160\b1{primary_color}}}" + chunk[0].text.lower(), chunk[0].start)]
    return lines


def build_ass(
    analysis: CaptionAnalysis,
    durations: dict[int, float],
) -> str:
    # Map segment_index → SegmentCaption for fast lookup
    seg_captions: dict[int, SegmentCaption] = {
        sc.segment_index: sc for sc in analysis.segments
    }

    # Compute per-segment time boundaries so we know which segment each word falls in
    cumulative = 0.0
    seg_boundaries: list[tuple[float, float, int]] = []
    for idx in sorted(durations.keys()):
        seg_boundaries.append((cumulative, cumulative + durations[idx], idx))
        cumulative += durations[idx]

    def word_seg(word_start: float) -> int:
        for start, end, idx in seg_boundaries:
            if start <= word_start < end:
                return idx
        return seg_boundaries[-1][2]

    header = """\
[Script Info]
ScriptType: v4.00+
WrapStyle: 0
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Manrope,160,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,0,0,2,40,40,60,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    words  = analysis.words
    for i in range(0, len(words), WORDS_PER_CAP):
        chunk       = words[i : i + WORDS_PER_CAP]
        chunk_start = chunk[0].start
        chunk_end   = chunk[-1].end

        seg_idx = word_seg(chunk_start)
        sc      = seg_captions.get(seg_idx)

        # Build the per-event position + scale override tag
        if sc:
            pct     = int(sc.scale * 100)
            pos_tag = rf"\an{sc.anchor}\pos({sc.x},{sc.y})\fscx{pct}\fscy{pct}"
        else:
            pos_tag = r"\an2\pos(540,1820)"  # safe default: bottom-center

        primary_color = sc.primary_color if sc else r"\c&HFFFFFF&"
        alt_color     = sc.alt_color     if sc else r"\c&H00FFFF&"
        lines = _build_lines(chunk, primary_color, alt_color)
        for j in range(len(lines)):
            t0   = lines[j][1]
            t1   = lines[j + 1][1] if j < len(lines) - 1 else chunk_end
            text = "{" + pos_tag + "}" + r"\N".join(line for line, _ in lines[:j + 1])
            events.append(f"Dialogue: 0,{_to_ass_time(t0)},{_to_ass_time(t1)},Default,,0,0,0,,{text}")

    return header + "\n".join(events) + "\n"


def burn_captions(video_path: str, ass_content: str) -> None:
    """Burn ASS captions into video_path in-place."""
    ass_tmp_path = video_path.replace(".mp4", "_captions.ass")
    tmp_out      = video_path.replace(".mp4", "_captioned.mp4")

    with open(ass_tmp_path, "w") as f:
        f.write(ass_content)

    try:
        subprocess.run([
            "ffmpeg", "-y",
            "-i", video_path,
            "-vf", f"ass={ass_tmp_path}:fontsdir={FONTS_DIR}",
            "-c:v", "libx264",
            "-c:a", "copy",
            tmp_out,
        ], check=True, capture_output=True)
        os.replace(tmp_out, video_path)
    finally:
        os.unlink(ass_tmp_path)


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="AI video editor pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Example:\n  python editor.py \"hype montage\" --ref style.mp4 clip1.mp4 clip2.mp4",
    )
    parser.add_argument("query",  help="Topic / creative direction for the video")
    parser.add_argument("--ref",  metavar="REF_VIDEO", help="Reference video to match style")
    parser.add_argument("--lang",  metavar="LANG_CODE", help="Language to create the video in")
    parser.add_argument("clips",  nargs="*", help="Source clip(s) to edit from")
    args = parser.parse_args()

    clip_args = args.clips
    if not clip_args:
        clip_args = sorted(glob.glob("*.mp4"))
        if args.ref:
            clip_args = [c for c in clip_args if c != args.ref]
        if not clip_args:
            print("No .mp4 files found in current directory.")
            raise SystemExit(1)
        print(f"[Main] Auto-discovered {len(clip_args)} clip(s): {clip_args}")

    clips = {str(i): path for i, path in enumerate(clip_args)}

    # ── 0. Style analysis (optional) ──
    style_guide: StyleGuide | None = None
    if args.ref:
        style_guide = analyze_style(args.ref)
        print(f"\n{'─'*50}")
        print(f"STYLE GUIDE  (from {os.path.basename(args.ref)})")
        print(f"  Avg clip duration : {style_guide.avg_clip_duration:.1f}s")
        print(f"  Energy            : {style_guide.energy}")
        print(f"  Color style       : {style_guide.color_style}")
        print(f"  Mood              : {style_guide.mood}")
        print(f"  Pacing            : {style_guide.pacing_note}")
        print(f"{'─'*50}\n")

    # ── 1. Script ──
    script = write_script(args.query, clips, style_guide, lang=args.lang or "en")
    print(f"\n{'─'*50}")
    print(f"SCRIPT: {script.topic}")
    print(f"Hook : {script.hook}")
    for b in script.beats:
        print(f"  Beat {b.index} | {b.mood}")
        print(f"    → {b.narration}")
        print(f"    ↳ {b.visual_direction}")
    print(f"CTA  : {script.cta}")
    print(f"{'─'*50}\n")

    # ── 2. TTS ──
    narration_wav, durations = generate_narration(script, lang=args.lang or "en")

    cta_idx = max(durations.keys())
    print(f"\nSegment durations:")
    print(f"  [0] hook  {durations[0]:.2f}s")
    for b in script.beats:
        print(f"  [{b.index}] beat  {durations[b.index]:.2f}s")
    print(f"  [{cta_idx}] cta   {durations[cta_idx]:.2f}s")
    print(f"  total: {sum(durations.values()):.2f}s\n")

    # ── 3. Edit plan ──
    plan = plan_edit(script, clips, durations, style_guide)

    scenes = sorted(plan.scenes, key=lambda s: s.segment_index)
    print(f"Edit plan — {len(scenes)} scene(s):")
    for s in scenes:
        dur      = durations[s.segment_index]
        clip_end = seconds_to_mm_ss(mm_ss_to_seconds(s.clip_start) + dur)
        print(f"  [seg {s.segment_index}] clip={s.clip_key} ({os.path.basename(clips[s.clip_key])})  "
              f"{s.clip_start} → {clip_end}  ({dur:.2f}s)  fx={s.effect}")
        print(f"           {s.note}")

    # ── 4. Render ──
    print("\nRendering scenes...")
    with tempfile.TemporaryDirectory() as tmp_dir:
        scene_paths = []
        for s in scenes:
            dur      = durations[s.segment_index]
            clip_end = seconds_to_mm_ss(mm_ss_to_seconds(s.clip_start) + dur)
            print(f"  seg {s.segment_index}: clip {s.clip_key}  {s.clip_start} → {clip_end}  ({dur:.2f}s)  fx={s.effect}")
            path = render_scene(s, clips, durations, tmp_dir)
            scene_paths.append(path)

        print("Concatenating + laying narration...")
        concat_and_narrate(scene_paths, narration_wav, OUTPUT_VIDEO)

    print(f"\nDone → {OUTPUT_VIDEO}")

    # ── 5. Captions ──
    print(f"\n{'─'*50}")
    caption_analysis = analyze_captions(OUTPUT_VIDEO, scenes, durations)
    
    print(caption_analysis)

    ass = build_ass(caption_analysis, durations)
    print("[Captions] Burning captions...")
    burn_captions(OUTPUT_VIDEO, ass)
    print(f"Done → {OUTPUT_VIDEO}  (with captions)")

    # ── Cost summary ──
    print(f"\n{'─'*60}")
    print("API COST SUMMARY")
    print(f"{'─'*60}")
    by_fn: dict[str, float] = {}
    for entry in _cost_log:
        by_fn[entry["fn"]] = by_fn.get(entry["fn"], 0) + entry["cost"]
    for fn, cost in by_fn.items():
        entries   = [e for e in _cost_log if e["fn"] == fn]
        calls     = len(entries)
        in_total  = sum(e["in_tok"]  for e in entries)
        out_total = sum(e["out_tok"] for e in entries)
        print(f"  {fn:<22} {calls:>2} call(s)  {in_total:>8,} in + {out_total:>6,} out tokens  = ${cost:.6f}")
    grand_total = sum(e["cost"] for e in _cost_log)
    print(f"{'─'*60}")
    print(f"  {'TOTAL':<22}                                           = ${grand_total:.6f}")
    print(f"{'─'*60}")


if __name__ == "__main__":
    main()
