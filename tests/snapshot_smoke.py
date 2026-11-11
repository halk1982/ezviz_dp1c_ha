"""Real shared producer -> authenticated JPEG -> decoder; synthetic media only."""
import base64
from concurrent.futures import ThreadPoolExecutor
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dp1c_video_probe/app"))
from live import Hub, handler_for
from snapshot import Snapshots, SnapshotUnavailable


with tempfile.TemporaryDirectory() as temporary:
    clip = Path(temporary) / "fixture.ts"
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x240:r=10",
        "-t", "3", "-c:v", "libx264", "-g", "10", "-bf", "0", "-f", "mpegts", str(clip),
    ], capture_output=True, check=True, timeout=20)
    command = [sys.executable, "-c", "import os,sys,time; time.sleep(0.6); os.execvp(sys.argv[1], sys.argv[1:])",
               "ffmpeg", "-v", "error", "-re", "-stream_loop", "-1", "-i", str(clip),
               "-c", "copy", "-f", "mpegts", "pipe:1"]
    hub = Hub(command, idle_seconds=0.2, max_seconds=30)
    snapshots = Snapshots(hub)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(hub, "test", "synthetic_password_only", snapshots))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def request():
        connection = http.client.HTTPConnection(*server.server_address, timeout=30)
        try:
            header = "Basic " + base64.b64encode(b"test:synthetic_password_only").decode()
            connection.request("GET", "/snapshot.jpg", headers={"Authorization": header})
            response = connection.getresponse()
            image = response.read()
            assert response.status == 200, response.status
            assert response.getheader("Content-Type") == "image/jpeg"
            return image
        finally:
            connection.close()

    try:
        assert hub.process is None
        with ThreadPoolExecutor(max_workers=2) as pool:
            images = list(pool.map(lambda _: request(), range(2)))
        assert images[0] == images[1], "simultaneous requests did not share a capture"
        report = subprocess.run([
            "ffprobe", "-v", "error", "-show_entries", "stream=codec_name,width,height", "-of", "json", "pipe:0",
        ], input=images[0], capture_output=True, check=True, timeout=10)
        video = json.loads(report.stdout)["streams"][0]
        assert video == {"codec_name": "mjpeg", "width": 320, "height": 240}, video
        decoded = subprocess.run([
            "ffmpeg", "-v", "error", "-xerror", "-i", "pipe:0", "-frames:v", "1", "-f", "framemd5", "-",
        ], input=images[0], capture_output=True, check=True, timeout=10)
        assert sum(bool(line) and not line.startswith(b"#") for line in decoded.stdout.splitlines()) == 1
        deadline = time.monotonic() + 5
        while hub.process is not None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert hub.process is None, "snapshot kept the cloud producer alive"
        assert request() == images[0]
        assert hub.process is None, "cached thumbnail reopened the cloud producer"
        print("PASS: concurrent authenticated requests share a decodable JPEG; cached request leaves camera idle")

        viewer = hub.subscribe()
        producer = hub.process
        try:
            snapshots.expires = 0
            assert request().startswith(b"\xff\xd8")
            assert hub.process is producer, "snapshot opened a second cloud producer"
            assert len(hub.viewers) == 1, "snapshot subscription was not released"
            print("PASS: fresh snapshot reuses an active live producer and releases its consumer slot")
        finally:
            hub.unsubscribe(viewer)
    finally:
        server.shutdown()
        server.server_close()
        hub.close()

    # A cold/stalled stream must time out and release its process/subscription.
    silent = Hub([sys.executable, "-c", "import time; time.sleep(30)"], idle_seconds=0.1)
    try:
        try:
            Snapshots(silent, timeout=0.3).get()
        except SnapshotUnavailable:
            pass
        else:
            raise AssertionError("stalled source accepted as an image")
        assert not silent.viewers
    finally:
        silent.close()
    assert silent.process is None
    print("PASS: stalled snapshot fails within deadline and cleans up its producer")
