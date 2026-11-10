"""Exercise the actual decoder in the built image, without cloud credentials."""
from pathlib import Path
import subprocess
import tempfile
from probe import validate_clip

with tempfile.TemporaryDirectory() as directory:
    clip = Path(directory) / "test.ts"
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=320x240:r=10",
        "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-f", "mpegts", str(clip)
    ], check=True, capture_output=True, timeout=20)
    result = validate_clip(clip)
    assert result == {"codec": "h264", "width": 320, "height": 240, "decoded_frames": 5}, result
    clip.write_bytes(b"not video")
    try:
        validate_clip(clip)
    except (ValueError, subprocess.CalledProcessError):
        pass
    else:
        raise AssertionError("invalid video accepted")
print("PASS: real FFmpeg/FFprobe decode five frames and reject invalid media")
