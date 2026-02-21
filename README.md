# Generate trendy narrated videos (GRWM, day in the life) with AI — fully automated.

<img src="example.png" width="300" />

Give it some clips and a prompt like *"GRWM as a 9-5 software engineer"* and it writes the script, clones your voice, edits the footage, and burns styled captions. You end up with a post-ready video without touching a timeline.

## How it works

1. **Voice cloning** — captures your voice from a sample so narration sounds like you
2. **Script generation** — an AI agent writes narration based on your clips and the vibe you want
3. **AI editing** — cuts and arranges your clips to match the narration timing
4. **Auto captions** — transcribes the narration and places styled captions that match the aesthetic

## Usage

```bash
python3 editor.py "grwm as an indie software engineer working remote" --ref ref.mp4 videos/*.mp4
```

## Examples

- [Example 1](https://github.com/dslogs/videoai/issues/1)
- [Example 2](https://github.com/dslogs/videoai/issues/2)
