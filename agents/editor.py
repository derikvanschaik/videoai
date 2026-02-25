"""
Editor agent — single prompt → structured EditPlan.
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

CLIPS = {
    "reference_video" :  "/Users/projectcoordinator/Desktop/reference.mov",
    "0": "/Users/projectcoordinator/Desktop/neymar.mov",
}


# ── Agent ──────────────────────────────────────────────────────────────────────

parts: list[types.Part] = []

for key, path in CLIPS.items():
    parts.append(types.Part(text=f"CLIP {key} ({os.path.basename(path)}):"))
    parts.append(types.Part(
        inline_data=types.Blob(data=open(path, "rb").read(), mime_type="video/mov")
    ))

print(parts)

parts.append(types.Part(text=f"""You are a professional video editor.

Watch the video and create an edit plan
so that the clip '0' follows the editing style as the video: 'reference_video'.
of the clip, the script must use actual events occurring in the video. 


You should aim to have some effects to make this visually interesting 
and go viral on a platform like Youtube Shorts or TikTok

For each scene return:
- clip_key   — which clip (one of: {sorted(CLIPS.keys())})
- clip_start — best timestamp in MM:SS
- duration   — how many seconds this scene runs
- effect     — An effect to have (sped up, filter, grainy, blurry, etc... or none)
- index      - order to display it in the final edit
- transition - Transition effect
- caption    - caption to add if necessary. 

Keep total runtime under 90 seconds.
"""))

response = client.models.generate_content(
    model=MODEL,
    contents=types.Content(parts=parts),
)

print(response.text)
