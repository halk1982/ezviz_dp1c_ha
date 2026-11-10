import json
from unittest.mock import Mock

from Crypto.Cipher import AES

from checks import LocalChecks, error_flags, inspect_ts
from media import BoundedDecryptor


def test_ffmpeg_errors_become_fixed_categories():
    private = b"non-existing PPS 0 referenced PRIVATE_KEY http://PRIVATE_URL"
    assert error_flags(private) == ["missing_pps"]
    assert error_flags(b"PRIVATE unrelated text") == []


def test_inspect_only_reports_allowlisted_metadata(monkeypatch):
    info = {"streams": [{"codec_name": "h264", "width": 1088, "height": 720,
                         "profile": "High", "pix_fmt": "yuv420p", "tags": {"name": "PRIVATE"}}],
            "packets": [{"pts": 10, "dts": 10, "dts_time": "1.0", "flags": "K_"},
                        {"pts": 20, "dts": 20, "dts_time": "1.1", "flags": "__"}]}
    runner = Mock(side_effect=[Mock(returncode=0, stdout=json.dumps(info).encode(), stderr=b"PRIVATE"),
                               Mock(returncode=0, stdout=b"#header\n" + b"frame\n" * 5, stderr=b"PRIVATE")])
    monkeypatch.setattr("checks.subprocess.run", runner)
    report = inspect_ts(b"x" * 188 * 4)
    assert report["status"] == "pass"
    assert report["decoded_frames"] == 5
    assert report["keyframes"] == 1
    assert report["max_dts_gap_seconds"] == 0.1
    assert "PRIVATE" not in json.dumps(report)


def test_timestamp_problems_and_failed_decode_are_distinct(monkeypatch):
    info = {"streams": [{"codec_name": "h264"}], "packets": [
        {"dts": 2, "dts_time": "0.0"}, {"dts": 2, "dts_time": "0.0"}, {},
        {"dts": 100, "dts_time": "70"}]}
    monkeypatch.setattr("checks.subprocess.run", Mock(side_effect=[
        Mock(returncode=0, stdout=json.dumps(info).encode(), stderr=b""),
        Mock(returncode=1, stdout=b"", stderr=b"non-existing PPS 0 referenced PRIVATE")]))
    report = inspect_ts(b"x" * 188 * 4)
    assert report["missing_dts"] == 1
    assert report["non_increasing_dts"] == 1
    assert report["max_dts_gap_seconds"] == 70
    assert report["status"] == "decode_error"
    assert report["flags"] == ["missing_pps"]
    assert "PRIVATE" not in json.dumps(report)


def test_decrypt_comparison_matches_bounded_capture():
    key = "synthetic-only"
    nal = b"\x65" + bytes(range(1, 64))
    cipher = AES.new(key.encode().ljust(16, b"\0"), AES.MODE_ECB)
    payload = b"\0\0\0\1" + cipher.encrypt(nal)
    encoded = b"\0\0\1\xe0" + (len(payload) + 3).to_bytes(2, "big") + b"\x80\0\0" + payload
    clear = BoundedDecryptor(key, codec="encrypted-header")
    output = clear(encoded) + clear.flush()
    messages = []
    checks = LocalChecks(key, messages.append, window_seconds=0)
    checks.decrypted(encoded, output)
    checks.finish()
    report = next(message for message in messages if message.startswith("LIVE_DECRYPT_CHECK:"))
    assert json.loads(report.split(": ", 1)[1])["status"] == "equal"
    assert key not in report


def test_decrypt_comparison_detects_different_result():
    messages = []
    checks = LocalChecks("test", messages.append, window_seconds=0)
    checks.decrypted(b"abcd", b"ABCD")
    checks.finish()
    report = next(message for message in messages if message.startswith("LIVE_DECRYPT_CHECK:"))
    assert json.loads(report.split(": ", 1)[1])["status"] == "different"


def test_sample_bound_and_one_check(monkeypatch):
    monkeypatch.setattr("checks.inspect_ts", lambda data: {"status": "pass", "sample_bytes": len(data)})
    messages = []
    checks = LocalChecks(None, messages.append, window_seconds=999, max_bytes=100)
    checks.output(b"x" * 200)
    checks.output(b"x" * 200)
    checks.finish()
    assert len(messages) == 1
    assert json.loads(messages[0].split(": ", 1)[1])["sample_bytes"] == 100


def test_decoded_frames_do_not_hide_invalid_timestamps(monkeypatch):
    info = {"streams": [{"codec_name": "h264"}], "packets": [
        {"dts": 1, "dts_time": "0", "flags": "K_"},
        {"dts": 1, "dts_time": "0", "flags": "__"}]}
    monkeypatch.setattr("checks.subprocess.run", Mock(side_effect=[
        Mock(returncode=0, stdout=json.dumps(info).encode(), stderr=b""),
        Mock(returncode=0, stdout=b"frame\n" * 5, stderr=b"")]))
    report = inspect_ts(b"x" * 188 * 4)
    assert report["decoded_frames"] == 5
    assert report["status"] == "timestamp_error"
    assert report["timestamp_status"] == "anomalous"
    assert report["visual_quality"] == "not_assessed"
