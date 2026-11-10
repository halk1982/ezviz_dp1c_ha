"""Full encrypted, video-only PS -> live producer -> authenticated HTTP -> decode.

All account fields, AES keys and media are synthetic. No EZVIZ service is contacted.
"""
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace

from Crypto.Cipher import AES
from live import Hub, handler_for, produce
from media import BoundedDecryptor
from pyezvizapi.stream import HIKVISION_NAL_ENCRYPTED_PREFIX_LENGTH, decrypt_hikvision_ps_video

KEY = "synthetic-only"
PACK = bytes.fromhex("000001ba4400040004010001fff8")
PSM = bytes.fromhex("000001bc000ee0ff000000041be0000000000000")


def pts(value):
    return bytes([0x21 | ((value >> 29) & 0x0e), (value >> 22) & 255,
                  1 | ((value >> 14) & 0xfe), (value >> 7) & 255, 1 | ((value << 1) & 0xfe)])


def pes(payload, timestamp):
    header = b"\x80\x80\x05" + pts(timestamp)
    return b"\0\0\1\xe0" + (len(header) + len(payload)).to_bytes(2, "big") + header + payload


def make_fixture(directory, header_size=0):
    raw = directory / "raw.h264"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x240:r=10",
                    "-t", "10", "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
                    "-g", "10", "-bf", "0", "-x264-params", "aud=1:repeat-headers=1",
                    "-f", "h264", str(raw)], check=True, capture_output=True, timeout=20)
    data = raw.read_bytes()
    starts = list(re.finditer(b"\x00\x00(?:\x00)?\x01", data))
    cipher = AES.new(KEY.encode().ljust(16, b"\0"), AES.MODE_ECB)
    frames = []
    frame = bytearray()
    clear = bytearray()
    expected = bytearray()
    encrypted_ps = bytearray()
    for i, start in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(data)
        nal = data[start.end():end]
        if nal[0] & 31 == 9 and frame:
            timestamp = len(frames) * 9000
            frames.append(bytes(frame))
            encrypted_ps.extend(PACK + PSM + pes(bytes(frame), timestamp))
            expected.extend(PACK + PSM + pes(bytes(clear), timestamp))
            frame.clear()
            clear.clear()
        # Use a fully encrypted first block, including short parameter/AUD NALs.
        # Trailing zero bytes are legal Annex B padding. This avoids ambiguity in
        # the pinned library's encrypted-header detector for sub-block clear NALs.
        nal = nal.ljust(16 + header_size, b"\0")
        body = nal[header_size:]
        count = min(len(body), HIKVISION_NAL_ENCRYPTED_PREFIX_LENGTH) // 16 * 16
        frame.extend(start.group() + nal[:header_size] + cipher.encrypt(body[:count]) + body[count:])
        clear.extend(start.group() + nal)
    if frame:
        timestamp = len(frames) * 9000
        frames.append(bytes(frame))
        encrypted_ps.extend(PACK + PSM + pes(bytes(frame), timestamp))
        expected.extend(PACK + PSM + pes(bytes(clear), timestamp))
    # Verify genuine AES decryption and PES preservation before involving FFmpeg.
    decryptor = BoundedDecryptor(KEY, codec="encrypted-header" if header_size == 0 else "h264-clear-header")
    decoded = b"".join(decryptor(encrypted_ps[i:i+997]) for i in range(0, len(encrypted_ps), 997)) + decryptor.flush()
    whole = decrypt_hikvision_ps_video(bytes(encrypted_ps), KEY, nalu_header_size=header_size)
    if header_size == 0:
        assert whole == bytes(expected), "encrypted-header fixture must match bounded capture"
    else:
        # Pinned 1.0.5.0 mishandles a short non-VCL NAL after a >4096-byte VCL
        # run in this fixture. Keep this explicit upstream-defect regression:
        # incremental PES/NAL boundaries must still reproduce the known plaintext.
        assert whole != bytes(expected), "revisit documented upstream clear-header regression"
    if decoded != bytes(expected):
        mismatch = next(i for i, (a, b) in enumerate(zip(decoded, expected)) if a != b)
        raise AssertionError(f"incremental decryption changed the synthetic video at byte {mismatch}; sizes={len(decoded)},{len(expected)}")
    (directory / "frames.json").write_text(json.dumps([frame.hex() for frame in frames]))
    (directory / "options.json").write_text(json.dumps({"account": "synthetic", "password": "fake", "serial": "fake", "max_view_seconds": 30, "decrypt_video": True}))
    (directory / "live_auth.json").write_text(json.dumps({"key": KEY, "identity": "synthetic", "token": {}}))


class FakeStream:
    def __init__(self, directory):
        self.directory = directory
        self.frames = [bytes.fromhex(frame) for frame in json.loads((directory / "frames.json").read_text())]
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def start(self):
        return SimpleNamespace(result=0)

    def close(self):
        self.closed = True

    def iter_packets(self, **kwargs):
        for i, frame in enumerate(self.frames):
            if self.closed:
                break
            packet = PACK + PSM + pes(frame, i * 9000)
            for start in range(0, len(packet), 997):
                yield SimpleNamespace(encrypted=False, body=packet[start:start+997])
            time.sleep(0.1)
        (self.directory / "source_finished").touch()


class FakeClient:
    last_codes = {}

    def __init__(self, *args, **kwargs):
        pass

    def export_token(self):
        return {}


def main():
    if "--producer" in sys.argv:
        directory = Path(sys.argv[-1])
        return produce(directory / "options.json", directory / "live_auth.json",
                       open_stream=lambda *_args, **_kwargs: FakeStream(directory), client_factory=FakeClient)

    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        make_fixture(directory)
        hub = Hub([sys.executable, __file__, "--producer", str(directory)], idle_seconds=0.2, max_seconds=30)
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(hub, "test", "synthetic_password_only"))
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            started = time.monotonic()
            url = f"http://test:synthetic_password_only@127.0.0.1:{port}/live.ts"
            viewer = subprocess.run(["ffmpeg", "-v", "error", "-analyzeduration", "500000", "-probesize", "65536",
                                     "-rw_timeout", "10000000", "-i", url, "-frames:v", "5", "-f", "framemd5", "-"],
                                    capture_output=True, timeout=15)
            assert viewer.returncode == 0, "HTTP decoder could not consume the encrypted video pipeline"
            frames = [line for line in viewer.stdout.splitlines() if line and not line.startswith(b"#")]
            assert len(frames) == 5
            assert not (directory / "source_finished").exists(), "video output arrived only after the source ended"
            assert time.monotonic() - started < 6, "excessive first-frame latency with synthetic real-time media"
            print("PASS: full producer decrypts video-only H.264, delivers five frames through authenticated HTTP before source ends")
        finally:
            server.shutdown()
            server.server_close()
            hub.close()


if __name__ == "__main__":
    raise SystemExit(main())
