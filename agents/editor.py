"""
Editor agent — clips + query → EditPlan.
"""
from __future__ import annotations

import os

from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel

load_dotenv()

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
MODEL  = os.getenv("EDITOR_MODEL", "gemini-3.1-pro-preview")


# ── Schema ───────────────────────────────────────────────────────────────────────

class Scene(BaseModel):
    index:      int
    clip_key:   str
    clip_start: str    # MM:SS
    duration:   float  # seconds
    narration:  str    # spoken words for this scene

class EditPlan(BaseModel):
    scenes: list[Scene]


# ── Agent ────────────────────────────────────────────────────────────────────────

def create_edit_plan(query: str, clip_paths: list[str]) -> EditPlan:
    parts: list[types.Part] = []

    for i, path in enumerate(clip_paths):
        ext  = os.path.splitext(path)[1].lower()
        mime = "video/quicktime" if ext == ".mov" else "video/mp4"
        parts.append(types.Part(text=f"CLIP {i} ({os.path.basename(path)}):"))
        parts.append(types.Part(inline_data=types.Blob(data=open(path, "rb").read(), mime_type=mime)))

    parts.append(types.Part(text=f"""You are a video editor making a short-form viral video (TikTok / Reels).

General Video Direction/Topic: {query}
Clips: {list(range(len(clip_paths)))}

Watch the clips and return an edit plan. For each scene pick:
- clip_key   — which clip (index as string)
- clip_start — best timestamp MM:SS
- duration   — how long to hold the shot (seconds)
- narration  — what the voiceover says over this scene

Keep total runtime under 90 seconds.
"""))

    response = client.models.generate_content(
        model=MODEL,
        contents=types.Content(parts=parts),
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=EditPlan,
        ),
    )

    return EditPlan.model_validate_json(response.text)


# example usage
if __name__ == "__main__":
    ROOT_PATH = '/Users/projectcoordinator/Desktop/videoai/videos'
    clips = []
    for i in range(1, 9):
        clips.append(ROOT_PATH + '/clip' + str(i) + '.mp4')

    edit_plan = create_edit_plan('5 simple beginner programming projects', clips)
    print(edit_plan)
