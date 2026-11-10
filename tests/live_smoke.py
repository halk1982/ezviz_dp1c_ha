"""Real HTTP and FFmpeg test with synthetic media; no cloud account required."""
from pathlib import Path
import http.client
import subprocess
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer

from live import Hub, handler_for

with tempfile.TemporaryDirectory() as directory:
    clip = Path(directory) / "fixture.ts"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x240:r=10", "-t", "3", "-c:v", "libx264", "-g", "10", "-f", "mpegts", str(clip)], check=True, capture_output=True, timeout=20)
    hub = Hub(["ffmpeg", "-v", "error", "-re", "-stream_loop", "-1", "-i", str(clip), "-c", "copy", "-f", "mpegts", "pipe:1"], idle_seconds=0.2, max_seconds=30)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(hub, "test", "synthetic_password_only"))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        connection.request("GET", "/live.ts")
        assert connection.getresponse().status == 401
        connection.close()
        assert hub.process is None
        port = server.server_address[1]
        url = f"http://test:synthetic_password_only@127.0.0.1:{port}/live.ts"
        command = ["ffmpeg", "-v", "error", "-rw_timeout", "10000000", "-i", url, "-frames:v", "5", "-f", "framemd5", "-"]
        viewers = [subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
        for viewer in viewers:
            stdout, stderr = viewer.communicate(timeout=25)
            assert viewer.returncode == 0, "HTTP video decoder failed"
            frames = [line for line in stdout.splitlines() if line and not line.startswith(b"#")]
            assert len(frames) == 5, "expected five decoded frames"
        deadline = time.monotonic() + 8
        while hub.process is not None and time.monotonic() < deadline:
            time.sleep(0.1)
        assert hub.process is None, "producer did not stop after disconnect"
        print("PASS: unauthorized request blocked; two HTTP viewers decode five frames; cloud producer stops when idle")
    finally:
        server.shutdown()
        server.server_close()
        hub.close()
