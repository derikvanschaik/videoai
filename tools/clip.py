import subprocess


def clip(src: str, start: str, end: str, dst: str) -> str:
    """Cut src from start to end (MM:SS) and write to dst.

    Re-encodes instead of stream-copying (-c copy) because concatenation
    requires all clips to share the same codec, frame rate, pixel format,
    and clean keyframe boundaries. Without this, ffmpeg concat produces
    glitches, freezes, or audio drift at every cut.

      libx264   — consistent video codec across all clips
      yuv420p   — standard pixel format (required for compatibility)
      -r 30     — locked frame rate so concat timestamps line up exactly
      aac/44100 — consistent audio codec and sample rate
    """
    subprocess.run([
        "ffmpeg", "-y",
        "-ss", start,
        "-to", end,
        "-i", src,
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-r", "30",
        "-c:a", "aac",
        "-ar", "44100",
        dst,
    ], check=True, capture_output=True)
    return dst
