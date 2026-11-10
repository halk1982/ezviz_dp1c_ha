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


def test_video_only_stream_emits_before_close():
    key = "test-only-key"
    cipher = AES.new(key.encode().ljust(16, b"\0"), AES.MODE_ECB)

    def packet(nal):
        payload = b"\x00\x00\x00\x01" + cipher.encrypt(nal)
        return b"\x00\x00\x01\xe0" + (len(payload) + 3).to_bytes(2, "big") + b"\x80\x00\x00" + payload

    nal1 = b"\x65" + bytes(range(1, 64))
    nal2 = b"\x41" + bytes(range(1, 64))
    decryptor = BoundedDecryptor(key, codec="encrypted-header")
    first = packet(nal1)
    second = packet(nal2)
    output = decryptor(first) + decryptor(second)
    assert output, "video-only stream was buffered until close instead of producing live output"
    complete = output + decryptor.flush()
    assert complete == first[:9] + b"\x00\x00\x00\x01" + nal1 + second[:9] + b"\x00\x00\x00\x01" + nal2


def test_video_nal_continuation_across_pes_is_preserved():
    key = "test-only-key"
    cipher = AES.new(key.encode().ljust(16, b"\0"), AES.MODE_ECB)
    nal = b"\x65" + bytes(range(1, 128))
    next_nal = b"\x41" + bytes(range(1, 64))

    def pes(payload):
        return b"\0\0\1\xe0" + (len(payload) + 3).to_bytes(2, "big") + b"\x80\x00\0" + payload

    encrypted = b"\0\0\0\1" + cipher.encrypt(nal)
    pieces = [encrypted[:17], encrypted[17:51], encrypted[51:]]
    expected_pieces = [b"\0\0\0\1" + nal[:13], nal[13:47], nal[47:]]
    last = pes(b"\0\0\0\1" + cipher.encrypt(next_nal))
    encoded = b"".join(pes(piece) for piece in pieces) + last
    expected = b"".join(pes(piece) for piece in expected_pieces) + pes(b"\0\0\0\1" + next_nal)
    decryptor = BoundedDecryptor(key, codec="encrypted-header")
    decoded = b"".join(decryptor(encoded[i:i+11]) for i in range(0, len(encoded), 11)) + decryptor.flush()
    assert decoded == expected


def test_producer_error_reports_stage_without_private_payload(tmp_path, capsys):
    import json
    from types import SimpleNamespace
    from live import produce

    options = tmp_path / "options.json"
    auth = tmp_path / "live_auth.json"
    options.write_text(json.dumps({"account": "fake", "password": "PRIVATE_PASSWORD", "serial": "PRIVATE_SERIAL"}))
    auth.write_text(json.dumps({"token": {"session_id": "PRIVATE_SESSION"}, "key": "PRIVATE_KEY"}))
    client = SimpleNamespace(last_codes={}, last_operation="none")

    def failed_open(*args, **kwargs):
        raise ValueError("PRIVATE_SERVER_RESPONSE")

    assert produce(options, auth, open_stream=failed_open, client_factory=lambda *args, **kwargs: client) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "LIVE_FAIL: stage=cloud_metadata error_class=ValueError" in captured.err
    assert "PRIVATE" not in captured.err


def test_nonzero_cloud_result_never_reaches_decoder(tmp_path, capsys, monkeypatch):
    import io
    import json
    from types import SimpleNamespace
    from unittest.mock import Mock
    from live import produce

    options = tmp_path / "options.json"
    auth = tmp_path / "live_auth.json"
    options.write_text(json.dumps({"account": "fake", "password": "fake", "serial": "fake"}))
    auth.write_text(json.dumps({"token": {}, "key": "fake", "identity": "fake"}))
    api = SimpleNamespace(last_codes={"meta_code": "200"}, last_operation="other")
    stream = Mock()
    stream.__enter__ = Mock(return_value=stream)
    stream.__exit__ = Mock(return_value=False)
    stream.start.return_value = SimpleNamespace(result=5405)
    decoder = Mock()
    monkeypatch.setattr("live.remux_stream", decoder)
    assert produce(options, auth, output=io.BytesIO(), open_stream=lambda *a, **k: stream,
                   client_factory=lambda *a, **k: api) == 21
    decoder.assert_not_called()
    captured = capsys.readouterr()
    assert "stream_result=5405" in captured.err
    assert "stage=stream_start error_class=StreamStartRejected" in captured.err
    assert "LIVE_API_CODES" not in captured.err
    assert captured.out == ""


def test_cloud_rejection_cooldown_prevents_immediate_reconnect():
    hub = Hub([sys.executable, "-c", "raise SystemExit(21)"])
    channel = hub.subscribe()
    assert channel.get(timeout=5) is END
    hub.thread.join(timeout=3)
    assert hub.cooldown - time.monotonic() > 110
    assert not hub.auth_blocked
    with pytest.raises(RuntimeError):
        hub.subscribe()
    hub.close()


def test_auto_decryption_preserves_clear_h264_header():
    key = "test-only-key"
    cipher = AES.new(key.encode().ljust(16, b"\0"), AES.MODE_ECB)
    plain1 = b"\x65" + bytes(range(1, 65))
    plain2 = b"\x41" + bytes(range(1, 65))

    def packet(nal, encrypted):
        payload = b"\0\0\0\1" + (nal[:1] + cipher.encrypt(nal[1:]) if encrypted else nal)
        return b"\0\0\1\xe0" + (len(payload) + 3).to_bytes(2, "big") + b"\x80\0\0" + payload

    decryptor = BoundedDecryptor(key, codec="auto")
    output = decryptor(packet(plain1, True)) + decryptor(packet(plain2, True)) + decryptor.flush()
    assert decryptor._nalu_header_size == 1
    assert output == packet(plain1, False) + packet(plain2, False)


def test_auto_does_not_lock_on_a_short_clear_aud():
    decryptor = BoundedDecryptor("test-only-key", codec="auto")
    aud = b"\0\0\1\xe0\0\x09\x80\0\0\0\0\0\1\x09\xf0"
    assert decryptor(aud) == b""
    assert decryptor._nalu_header_size is None
