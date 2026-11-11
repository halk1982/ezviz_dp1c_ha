"""Bounded JPEG extraction from the existing shared live producer."""
import os
import queue
import select
import subprocess
import threading
import time


class SnapshotUnavailable(RuntimeError):
    pass


class Snapshots:
    """One capture at a time; cache only in RAM, never return an expired image."""
    def __init__(self, hub, cache_seconds=30, timeout=25, retry_seconds=10):
        self.hub = hub
        self.cache_seconds = cache_seconds
        self.timeout = timeout
        self.retry_seconds = retry_seconds
        self.condition = threading.Condition()
        self.capturing = False
        self.image = None
        self.expires = 0.0
        self.retry_at = 0.0

    def get(self):
        deadline = time.monotonic() + self.timeout + 2
        with self.condition:
            while self.capturing:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SnapshotUnavailable()
                self.condition.wait(timeout=remaining)
            now = time.monotonic()
            if self.image is not None and now < self.expires:
                return self.image
            if now < self.retry_at:
                raise SnapshotUnavailable()
            self.capturing = True
        try:
            image = self._capture()
        except Exception:
            with self.condition:
                self.retry_at = time.monotonic() + self.retry_seconds
            raise SnapshotUnavailable() from None
        else:
            with self.condition:
                self.image = image
                self.expires = time.monotonic() + self.cache_seconds
            return image
        finally:
            with self.condition:
                self.capturing = False
                self.condition.notify_all()

    def _capture(self):
        # The hub enforces cloud cooldowns and reuses an existing producer. A
        # snapshot occupies one of its four bounded consumer slots temporarily.
        channel = self.hub.subscribe()
        process = writer = None
        stopped = threading.Event()
        deadline = time.monotonic() + self.timeout
        max_input = 8 * 1024 * 1024
        max_image = 2 * 1024 * 1024
        try:
            process = subprocess.Popen([
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-analyzeduration", "500000", "-probesize", "65536",
                "-f", "mpegts", "-i", "pipe:0", "-map", "0:v:0",
                "-frames:v", "1", "-vf", "scale=w='min(1280,iw)':h=-2",
                "-threads", "1", "-c:v", "mjpeg", "-q:v", "3",
                "-f", "image2pipe", "pipe:1",
            ], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

            def feed():
                count = 0
                try:
                    while not stopped.is_set():
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        try:
                            chunk = channel.get(timeout=min(remaining, 0.25))
                        except queue.Empty:
                            continue
                        if chunk is None:
                            break
                        count += len(chunk)
                        if count > max_input:
                            break
                        process.stdin.write(chunk)
                        process.stdin.flush()
                except (OSError, ValueError):
                    pass
                finally:
                    try:
                        process.stdin.close()
                    except OSError:
                        pass

            writer = threading.Thread(target=feed, daemon=True)
            writer.start()
            image = bytearray()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SnapshotUnavailable()
                readable, _, _ = select.select([process.stdout], [], [], min(remaining, 0.25))
                if not readable:
                    continue
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    break
                image.extend(chunk)
                if len(image) > max_image:
                    raise SnapshotUnavailable()
            result = process.wait(timeout=max(0.01, deadline - time.monotonic()))
            if result or not image.startswith(b"\xff\xd8") or not image.endswith(b"\xff\xd9"):
                raise SnapshotUnavailable()
            return bytes(image)
        finally:
            stopped.set()
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait()
                if writer is not None:
                    writer.join(timeout=1)
                else:
                    process.stdin.close()
                process.stdout.close()
            self.hub.unsubscribe(channel)
