import base64
import http.client
import queue
import sys
import threading
import time
from http.server import ThreadingHTTPServer

from Crypto.Cipher import AES
import pytest

from live import BoundedDecryptor, END, Hub, handler_for, validate_options


def test_incremental_decrypt_matches_split_pes():
    key = "test-only-key"
    plaintext = b"\x65" + bytes(range(1, 64))
    cipher = AES.new(key.encode().ljust(16, b"\0"), AES.MODE_ECB)
    payload = b"\x00\x00\x00\x01" + cipher.encrypt(plaintext)
    header = b"\x00\x00\x01\xe0" + (len(payload) + 3).to_bytes(2, "big") + b"\x80\x00\x00"
    boundary = b"\x00\x00\x01\xbe\x00\x04" + b"\0" * 4
    encrypted = header + payload + boundary
    decryptor = BoundedDecryptor(key, codec="encrypted-header")
    decoded = b"".join(decryptor(encrypted[i:i+7]) for i in range(0, len(encrypted), 7)) + decryptor.flush()
    assert decoded == header + b"\x00\x00\x00\x01" + plaintext + boundary


def test_decrypt_buffer_is_bounded():
    with pytest.raises(ValueError):
        BoundedDecryptor("test", codec="encrypted-header")(b"x" * (8 * 1024 * 1024 + 1))


@pytest.mark.parametrize("password", ["", "short", "not-url-safe/password", "contains spaces 123456"])
def test_stream_password_validation(password):
    with pytest.raises(ValueError):
        validate_options({"stream_username": "dp1c", "stream_password": password})


def test_hub_starts_on_demand_shares_and_stops():
    command = [sys.executable, "-u", "-c", "import time; time.sleep(0.1); print('one'); time.sleep(30)"]
    hub = Hub(command, idle_seconds=0.1, max_seconds=5)
    assert hub.process is None
    a = hub.subscribe()
    process = hub.process
    b = hub.subscribe()
    assert hub.process is process
    assert a.get(timeout=5) == b"one\n"
    assert b.get(timeout=5) == b"one\n"
    hub.unsubscribe(a)
    hub.unsubscribe(b)
    hub.thread.join(timeout=3)
    assert process.poll() is not None
    assert hub.process is None
    hub.close()


def test_hub_max_viewers_and_finish_full_queue():
    hub = Hub([sys.executable, "-c", "import time; time.sleep(30)"], max_seconds=5)
    try:
        viewers = [hub.subscribe() for _ in range(4)]
        with pytest.raises(RuntimeError):
            hub.subscribe()
        full = queue.Queue(maxsize=1)
        full.put(b"data")
        hub.finish(full)
        assert full.get_nowait() is END
    finally:
        hub.close()


def test_authentication_and_route_do_not_start_cloud():
    hub = Hub(["must-not-run"])
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(hub, "viewer", "long_password_1234"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        connection.request("GET", "/live.ts")
        response = connection.getresponse()
        assert response.status == 401
        response.read()
        connection.close()
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        header = "Basic " + base64.b64encode(b"viewer:long_password_1234").decode()
        connection.request("GET", "/wrong", headers={"Authorization": header})
        assert connection.getresponse().status == 404
        assert hub.process is None
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        hub.close()


def test_auth_failure_does_not_request_codes_repeatedly():
    hub = Hub([sys.executable, "-c", "raise SystemExit(20)"])
    channel = hub.subscribe()
    assert channel.get(timeout=5) is END
    hub.thread.join(timeout=3)
    assert hub.auth_blocked
    hub.cooldown = 0
    with pytest.raises(RuntimeError):
        hub.subscribe()
    hub.close()
