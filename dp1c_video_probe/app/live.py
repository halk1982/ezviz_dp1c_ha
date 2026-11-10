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

from pyezvizapi.exceptions import EzvizAuthVerificationCode
from pyezvizapi.cloud_stream import open_cloud_stream
from media import BoundedDecryptor, remux_stream

from probe import DiagnosticClient, authenticate, cached_camera_key, failure_hint, persist_session

END = None


class StreamStartRejected(ValueError):
    """The VTM service returned a nonzero stream-start result."""


def kill_group(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def produce(options_path=Path("/data/options.json"), auth_path=Path("/data/live_auth.json"),
            output=None, open_stream=open_cloud_stream, client_factory=DiagnosticClient):
    """Binary stdout only. Credentials are read from a private file, not argv."""
    os.umask(0o077)
    logging.disable(logging.CRITICAL)
    stage = "configuration"
    api = None

    def emit(message):
        print(message, file=sys.stderr, flush=True)

    try:
        options = json.loads(options_path.read_text())
        auth = json.loads(auth_path.read_text())
        api = client_factory(options["account"], options["password"], token=auth["token"], timeout=15)
        stage = "cloud_metadata"
        emit("LIVE_STAGE: cloud_metadata")
        stream = open_stream(api, options["serial"], timeout=15)
        stage = "cloud_connect"
        emit("LIVE_STAGE: cloud_connect")
        with stream:
            stage = "stream_start"
            emit("LIVE_STAGE: stream_start")
            info = stream.start()
            result = getattr(info, "result", None)
            if type(result) is int and abs(result) <= 999999:
                emit(f"LIVE_CLOUD: stream_result={result}")
                if result != 0:
                    raise StreamStartRejected()
            # Save any token refreshed during cloud negotiation for the next view.
            persist_session(api, auth_path.parent / "session.json", auth["identity"])
            auth["token"] = api.export_token()
            temp = auth_path.with_suffix(".tmp")
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as saved:
                json.dump(auth, saved)
            os.replace(temp, auth_path)
            stage = "live_remux"
            remux_stream(stream, output if output is not None else sys.stdout.buffer,
                         key=auth["key"], max_seconds=options.get("max_view_seconds", 300), emit=emit,
                         video_mode=options.get("video_mode", "copy"),
                         decrypt_mode=options.get("decrypt_mode", "auto"))
        return 0
    except Exception as error:
        emit(f"LIVE_FAIL: stage={stage} error_class={type(error).__name__}")
        if api is not None and api.last_codes and not isinstance(error, StreamStartRejected):
            emit("LIVE_API_CODES: operation=" + api.last_operation + " " + json.dumps(api.last_codes))
        if isinstance(error, StreamStartRejected):
            return 21
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
        reason = "source_eof"
        cooldown_seconds = 60
        byte_count = 0
        try:
            while True:
                now = time.monotonic()
                with self.lock:
                    if self.closed:
                        reason = "server_shutdown"
                        break
                    if not self.viewers and now - self.last_detach >= self.idle_seconds:
                        reason = "no_viewers"
                        break
                if now - started >= self.max_seconds:
                    reason = "view_duration_limit"
                    break
                if now - last_data > self.startup_seconds:
                    failed = True
                    reason = "media_timeout"
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
                        elif result == 21:
                            reason = "cloud_start_rejected"
                            cooldown_seconds = 120
                            print("LIVE: cloud start rejected; pausing new attempts for 120 seconds", flush=True)
                    except subprocess.TimeoutExpired:
                        pass
                    break
                byte_count += len(chunk)
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
            reason = "relay_error"
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
                    self.cooldown = time.monotonic() + cooldown_seconds
            print(f"LIVE_STOP: reason={reason} ts_bytes={byte_count} exit_code={process.returncode}", flush=True)

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
                print("LIVE_HTTP: authenticated viewer attached", flush=True)
                first = channel.get(timeout=50)
                if first is END:
                    self.send_error(503, "Cloud stream unavailable; check add-on log")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "video/mp2t")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                print("LIVE_HTTP: delivering_mpegts", flush=True)
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
            except queue.Empty:
                print("LIVE_HTTP: media_wait_timeout", flush=True)
            except OSError:
                print("LIVE_HTTP: viewer_disconnected", flush=True)
            finally:
                if channel is not None:
                    hub.unsubscribe(channel)
                self.close_connection = True

    return Handler


def validate_options(options):
    if options.get("decrypt_mode", "auto") not in {"auto", "encrypted-header", "h264-clear-header", "hevc"}:
        raise ValueError("Unsupported decrypt_mode")
    if options.get("video_mode", "copy") not in {"copy", "compatible"}:
        raise ValueError("video_mode must be copy or compatible")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", options.get("stream_username", "")):
        raise ValueError("Set a URL-safe stream_username")
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", options.get("stream_password", "")):
        raise ValueError("Set a separate 16-128 character URL-safe stream_password")
    if not 30 <= options.get("max_view_seconds", 300) <= 900:
        raise ValueError("max_view_seconds must be 30-900")


def run(options):
    os.umask(0o077)
    logging.disable(logging.CRITICAL)
    print("LIVE: version=0.2.2", flush=True)
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
