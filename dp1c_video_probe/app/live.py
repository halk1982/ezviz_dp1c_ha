"""Authenticated on-demand MPEG-TS bridge; one producer shared by viewers."""
import base64
import hmac
import json
import logging
import os
from pathlib import Path
import queue
import re
import select
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pyezvizapi.client import EzvizClient
from pyezvizapi.exceptions import EzvizAuthVerificationCode
from pyezvizapi.cloud_stream import open_cloud_stream
from pyezvizapi.__main__ import _BufferedStreamPayloadDecryptor, _copy_stream_payloads_to_mpegts

from probe import authenticate, cached_camera_key, failure_hint, persist_session

END = None


def kill_group(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


class BoundedDecryptor(_BufferedStreamPayloadDecryptor):
    """Bound buffering while preserving upstream cross-packet NAL handling."""
    def __call__(self, data):
        if len(self._buffer) + len(data) > 8 * 1024 * 1024:
            raise ValueError("Media decryption buffer limit exceeded")
        return super().__call__(data)


def produce():
    """Binary stdout only. Credentials are read from a private file, not argv."""
    os.umask(0o077)
    logging.disable(logging.CRITICAL)
    stage = "cloud_open"
    try:
        options = json.loads(Path("/data/options.json").read_text())
        auth = json.loads(Path("/data/live_auth.json").read_text())
        api = EzvizClient(options["account"], options["password"], token=auth["token"], timeout=15)
        with open_cloud_stream(api, options["serial"], timeout=15) as stream:
            stream.start()
            # Save any token refreshed during cloud negotiation for the next view.
            persist_session(api, Path("/data/session.json"), auth["identity"])
            auth["token"] = api.export_token()
            temp = Path("/data/live_auth.tmp")
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as output:
                json.dump(auth, output)
            os.replace(temp, "/data/live_auth.json")
            stage = "live_remux"
            transform = BoundedDecryptor(auth["key"], codec="encrypted-header") if auth["key"] else None
            # Match the successful probe's nalu_header_size=0. Audio is omitted in
            # this first live version; no video re-encoding is performed.
            process = subprocess.Popen([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "mpeg", "-i", "pipe:0",
                "-map", "0:v:0", "-c:v", "copy", "-an", "-mpegts_flags", "resend_headers",
                "-flush_packets", "1", "-f", "mpegts", "pipe:1"
            ], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            _copy_stream_payloads_to_mpegts(stream, sys.stdout.buffer, process=process,
                max_packets=None, duration_seconds=options.get("max_view_seconds", 300),
                allow_encrypted=False, transform_payload=transform)
        return 0
    except Exception as error:
        print(f"LIVE_FAIL: stage={stage} error_class={type(error).__name__}", file=sys.stderr, flush=True)
        return 20 if isinstance(error, EzvizAuthVerificationCode) else 1


class Hub:
    """Bound queues; slow viewers are disconnected, never block cloud consumption."""
    def __init__(self, command, idle_seconds=10, max_seconds=300, startup_seconds=45):
        self.command = command
        self.idle_seconds = idle_seconds
        self.max_seconds = max_seconds
        self.startup_seconds = startup_seconds
        self.lock = threading.Lock()
        self.viewers = set()
        self.process = None
        self.thread = None
        self.closed = False
        self.auth_blocked = False
        self.cooldown = 0.0
        self.last_detach = time.monotonic()

    def subscribe(self):
        with self.lock:
            if self.closed or self.auth_blocked or time.monotonic() < self.cooldown or len(self.viewers) >= 4:
                raise RuntimeError("Stream unavailable")
            channel = queue.Queue(maxsize=64)
            self.viewers.add(channel)
            if self.process is None:
                try:
                    self.process = subprocess.Popen(self.command, stdout=subprocess.PIPE, start_new_session=True)
                    self.thread = threading.Thread(target=self._pump, args=(self.process,), daemon=True)
                    self.thread.start()
                except Exception:
                    self.viewers.discard(channel)
                    self.process = None
                    raise
            return channel

    def unsubscribe(self, channel):
        with self.lock:
            self.viewers.discard(channel)
            if not self.viewers:
                self.last_detach = time.monotonic()

    @staticmethod
    def finish(channel):
        # Always make room for END, including when a consumer has stopped reading.
        while True:
            try:
                channel.put_nowait(END)
                return
            except queue.Full:
                try:
                    channel.get_nowait()
                except queue.Empty:
                    pass

    def _pump(self, process):
        started = last_data = time.monotonic()
        failed = False
        try:
            while True:
                now = time.monotonic()
                with self.lock:
                    if self.closed or (not self.viewers and now - self.last_detach >= self.idle_seconds):
                        break
                if now - started >= self.max_seconds:
                    break
                if now - last_data > self.startup_seconds:
                    failed = True
                    print("LIVE_FAIL: stage=media_timeout", flush=True)
                    break
                readable, _, _ = select.select([process.stdout], [], [], 0.5)
                if not readable:
                    continue
                chunk = os.read(process.stdout.fileno(), 188 * 64)
                if not chunk:
                    failed = True
                    try:
                        result = process.wait(timeout=1)
                        if result == 20:
                            with self.lock:
                                self.auth_blocked = True
                            print("LIVE: authentication needs attention; restart after resolving the challenge", flush=True)
                    except subprocess.TimeoutExpired:
                        pass
                    break
                last_data = time.monotonic()
                with self.lock:
                    had_viewers = bool(self.viewers)
                    for channel in list(self.viewers):
                        try:
                            channel.put_nowait(chunk)
                        except queue.Full:
                            self.viewers.discard(channel)
                            self.finish(channel)
                    if had_viewers and not self.viewers:
                        self.last_detach = last_data
        except Exception as error:
            failed = True
            print(f"LIVE_FAIL: stage=relay error_class={type(error).__name__}", flush=True)
        finally:
            kill_group(process)
            process.stdout.close()
            with self.lock:
                for channel in self.viewers:
                    self.finish(channel)
                self.viewers.clear()
                self.process = None
                if failed:
                    self.cooldown = time.monotonic() + 60
            print("LIVE: cloud producer stopped", flush=True)

    def close(self):
        with self.lock:
            self.closed = True
        if self.thread:
            self.thread.join(timeout=5)


def handler_for(hub, username, password):
    expected = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # Never log URLs, Authorization headers or client addresses.

        def do_GET(self):
            self.connection.settimeout(15)
            supplied = self.headers.get("Authorization", "")
            if not hmac.compare_digest(supplied.encode(), expected.encode()):
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="DP1C"')
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if self.path != "/live.ts":
                self.send_error(404)
                return
            channel = None
            try:
                channel = hub.subscribe()
                first = channel.get(timeout=50)
                if first is END:
                    self.send_error(503, "Cloud stream unavailable; check add-on log")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "video/mp2t")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(first)
                self.wfile.flush()
                while True:
                    chunk = channel.get(timeout=50)
                    if chunk is END:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except RuntimeError:
                self.send_error(503, "Stream busy or cooling down")
            except (queue.Empty, OSError):
                pass
            finally:
                if channel is not None:
                    hub.unsubscribe(channel)
                self.close_connection = True

    return Handler


def validate_options(options):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", options.get("stream_username", "")):
        raise ValueError("Set a URL-safe stream_username")
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", options.get("stream_password", "")):
        raise ValueError("Set a separate 16-128 character URL-safe stream_password")
    if not 30 <= options.get("max_view_seconds", 300) <= 900:
        raise ValueError("max_view_seconds must be 30-900")


def run(options):
    os.umask(0o077)
    logging.disable(logging.CRITICAL)
    print("LIVE: version=0.2.0", flush=True)
    stage = "live_configuration"
    auth_path = Path("/data/live_auth.json")
    try:
        validate_options(options)
        if not re.fullmatch(r"[a-zA-Z0-9-]+\.ezvizlife\.com", options["api_host"]):
            raise ValueError("Invalid API host")
        stage = "login"
        api, identity = authenticate(options, Path("/data/session.json"))
        stage = "camera_key"
        try:
            key = cached_camera_key(api, options, Path("/data/camera_key.json"))
            if options.get("decrypt_video", True) and not key:
                raise ValueError("Camera encryption key unavailable")
        finally:
            persist_session(api, Path("/data/session.json"), identity)
        # Private file avoids secrets in command lines or logs. Removed at shutdown.
        fd = os.open(auth_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as output:
            json.dump({"token": api.export_token(), "key": key, "identity": identity}, output)
    except Exception as error:
        print(f"FAIL: stage={stage} error_class={type(error).__name__}", flush=True)
        print("HINT: " + (str(error) if stage == "live_configuration" and isinstance(error, ValueError) else failure_hint(stage, error)), flush=True)
        return 1
    hub = Hub([sys.executable, __file__, "--producer"], max_seconds=options.get("max_view_seconds", 300))
    server = ThreadingHTTPServer(("0.0.0.0", 8558), handler_for(hub, options["stream_username"], options["stream_password"]))
    server.daemon_threads = True
    server.timeout = 1
    stopped = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopped.set())
    print("LIVE: ready on port 8558, path /live.ts (authentication required); camera idle until viewed", flush=True)
    try:
        while not stopped.is_set():
            server.handle_request()
    finally:
        hub.close()
        server.server_close()
        auth_path.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(produce() if "--producer" in sys.argv else 1)
