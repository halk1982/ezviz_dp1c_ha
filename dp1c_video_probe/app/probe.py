"""Bounded local compatibility test. Never print API payloads or credential values."""
import hashlib
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


def authenticate(options, token_path):
    """Reuse the login session when completing a separate camera-key challenge."""
    identity = hashlib.sha256((options["account"] + "@" + options["api_host"]).encode()).hexdigest()
    saved = json.loads(token_path.read_text()) if token_path.exists() else {}
    token = saved.get("token") if saved.get("identity") == identity else None
    login_code = options.get("verification_code")
    # An explicitly supplied login code starts a fresh login challenge response.
    if login_code:
        token = None
    api = EzvizClient(options["account"], options["password"], url=options["api_host"], timeout=15, token=token)
    if not token:
        api.login(sms_code=int(login_code) if login_code else None)
    else:
        print("INFO: reusing saved login session", flush=True)
    persist_session(api, token_path, identity)
    return api, identity


def persist_session(api, path, identity):
    temp = path.with_suffix(".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump({"identity": identity, "token": api.export_token()}, stream)
    os.replace(temp, path)


def camera_key(api, options):
    if not options.get("decrypt_video", True):
        return None
    if options.get("camera_key"):
        return options["camera_key"]
    return api.get_cam_key(options["serial"], smscode=options.get("camera_verification_code") or None)


def failure_hint(stage, error):
    if type(error).__name__ == "EzvizAuthVerificationCode":
        if stage == "camera_key":
            return "Enter the new email code in camera_verification_code; leave verification_code empty. Or supply your known device encryption key in camera_key."
        if stage == "login":
            return "Enter the account-login code in verification_code. This is separate from camera_verification_code."
    known = {
        "The MFA code is invalid, please try again.": "The login verification code was rejected. Clear verification_code if the code was requested at camera_key stage.",
        "Incorrect Username.": "Check the account name locally.",
        "Incorrect Password.": "Check the account password locally.",
        "The user is locked.": "The account is locked; stop retries and resolve this through EZVIZ.",
    }
    return known.get(str(error), "Share only the FAIL line; do not share credentials or raw API responses.")


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
        token_path = Path("/data/session.json")
        api, identity = authenticate(options, token_path)
        stage = "camera_key"
        print("STAGE: camera_key", flush=True)
        key = camera_key(api, options)
        persist_session(api, token_path, identity)
        stage = "cloud_capture"
        print("STAGE: cloud_capture (up to 10 seconds of media)", flush=True)
        api.save_clip(
            options["serial"], clip, source="cloud", output_format="mpegts",
            duration_seconds=10, max_packets=2000, timeout=15,
            decrypt_video=options.get("decrypt_video", True),
            media_key=key,
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
        clip.unlink(missing_ok=True)
        print("HINT: " + failure_hint(stage, error), flush=True)
        print("No validated clip was created. No camera entity is created by this probe.", flush=True)
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
