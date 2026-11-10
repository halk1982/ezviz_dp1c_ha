"""Bounded local compatibility test. Never print API payloads or credential values."""
import json
import logging
import os
from pathlib import Path
import re
import signal
import subprocess
import sys

from pyezvizapi.client import EzvizClient


def validate_clip(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name,width,height", "-of", "json", str(path)],
        capture_output=True, timeout=20, check=True,
    )
    streams = json.loads(result.stdout).get("streams", [])
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    if not video or not video.get("width") or not video.get("height"):
        raise ValueError("No video track")
    decoded = subprocess.run(
        ["ffmpeg", "-v", "error", "-xerror", "-i", str(path), "-map", "0:v:0", "-frames:v", "5", "-f", "framemd5", "-"],
        capture_output=True, timeout=20, check=True,
    )
    frames = [line for line in decoded.stdout.splitlines() if line and not line.startswith(b"#")]
    if not frames:
        raise ValueError("No decoded video frames")
    return {"codec": video.get("codec_name"), "width": video["width"], "height": video["height"], "decoded_frames": len(frames)}


def worker():
    os.umask(0o077)
    logging.disable(logging.CRITICAL)
    stage = "configuration"
    clip = Path("/media/ezviz_dp1c_probe/live-test.ts")
    try:
        options = json.loads(Path("/data/options.json").read_text())
        if not all(options.get(key) for key in ("account", "password", "serial")):
            raise ValueError("Missing configuration")
        if not re.fullmatch(r"[a-zA-Z0-9-]+\.ezvizlife\.com", options["api_host"]):
            raise ValueError("Invalid API hostname")
        clip.parent.mkdir(parents=True, exist_ok=True)
        clip.unlink(missing_ok=True)
        stage = "login"
        print("STAGE: login", flush=True)
        api = EzvizClient(options["account"], options["password"], url=options["api_host"], timeout=15)
        code = options.get("verification_code")
        api.login(sms_code=int(code) if code else None)
        stage = "cloud_capture"
        print("STAGE: cloud_capture (up to 10 seconds of media)", flush=True)
        api.save_clip(
            options["serial"], clip, source="cloud", output_format="mpegts",
            duration_seconds=10, max_packets=2000, timeout=15,
            decrypt_video=options.get("decrypt_video", True),
            media_key=options.get("camera_key") or None,
        )
        stage = "video_decode"
        print("STAGE: video_decode", flush=True)
        summary = validate_clip(clip)
        print("PASS: " + json.dumps(summary), flush=True)
        print("Private test clip: /media/ezviz_dp1c_probe/live-test.ts", flush=True)
        return 0
    except Exception as error:
        # Exception bodies, URLs and serials are intentionally excluded.
        print(f"FAIL: stage={stage} error_class={type(error).__name__}", flush=True)
        print("Keep any captured video private; share only the stage and error class.", flush=True)
        return 1


def main():
    # Bound API startup, media capture and decoder subprocesses together. Kill the
    # entire worker process group on timeout or Supervisor stop (including FFmpeg).
    process = subprocess.Popen([sys.executable, __file__, "--worker"], start_new_session=True)

    def terminate(*_):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, terminate)
    try:
        return process.wait(timeout=120)
    except subprocess.TimeoutExpired:
        terminate()
        process.wait()
        print("FAIL: stage=overall_timeout error_class=TimeoutExpired", flush=True)
        return 1
    finally:
        terminate()


if __name__ == "__main__":
    raise SystemExit(worker() if "--worker" in sys.argv else main())
