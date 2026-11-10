import importlib.util
import json
from pathlib import Path
import subprocess
from unittest.mock import Mock

import pytest

spec = importlib.util.spec_from_file_location("probe", Path(__file__).resolve().parents[1] / "dp1c_video_probe/app/probe.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def test_probe_requires_actual_decoded_frames(monkeypatch):
    runner = Mock(side_effect=[
        Mock(stdout=json.dumps({"streams": [{"codec_type": "video", "codec_name": "h264", "width": 640, "height": 480}]}).encode()),
        Mock(stdout=b"#header\n0, 0, 0, 1, 100, checksum\n")])
    monkeypatch.setattr(probe.subprocess, "run", runner)
    assert probe.validate_clip(Path("test.ts")) == {"codec": "h264", "width": 640, "height": 480, "decoded_frames": 1}
    assert "-xerror" in runner.call_args.args[0]


@pytest.mark.parametrize("streams", [[], [{"codec_type": "audio"}], [{"codec_type": "video", "width": 0, "height": 0}]])
def test_probe_rejects_missing_video(monkeypatch, streams):
    monkeypatch.setattr(probe.subprocess, "run", Mock(return_value=Mock(stdout=json.dumps({"streams": streams}).encode())))
    with pytest.raises(ValueError):
        probe.validate_clip(Path("test.ts"))


def test_probe_rejects_header_without_frames(monkeypatch):
    runner = Mock(side_effect=[
        Mock(stdout=b'{"streams":[{"codec_type":"video","width":640,"height":480}]}'),
        Mock(stdout=b"# No frames\n")])
    monkeypatch.setattr(probe.subprocess, "run", runner)
    with pytest.raises(ValueError):
        probe.validate_clip(Path("test.ts"))


def test_probe_propagates_decode_failure(monkeypatch):
    runner = Mock(side_effect=[
        Mock(stdout=b'{"streams":[{"codec_type":"video","width":640,"height":480}]}'),
        subprocess.CalledProcessError(1, "ffmpeg")])
    monkeypatch.setattr(probe.subprocess, "run", runner)
    with pytest.raises(subprocess.CalledProcessError):
        probe.validate_clip(Path("test.ts"))


def test_camera_code_sent_only_to_key_request(tmp_path, monkeypatch):
    options = {"account": "test", "password": "fake", "serial": "device", "api_host": "apiieu.ezvizlife.com", "verification_code": "", "camera_verification_code": "001234", "camera_key": "", "decrypt_video": True}
    api = Mock()
    api.export_token.return_value = {"session_id": "fake-session", "rf_session_id": "fake-refresh"}
    factory = Mock(return_value=api)
    monkeypatch.setattr(probe, "EzvizClient", factory)
    session = tmp_path / "session.json"
    client, _ = probe.authenticate(options, session)
    probe.camera_key(client, options)
    api.login.assert_called_once_with(sms_code=None)
    api.get_cam_key.assert_called_once_with("device", smscode="001234")
    assert session.stat().st_mode & 0o777 == 0o600
    api.reset_mock()
    probe.authenticate(options, session)
    api.login.assert_not_called()
    assert factory.call_args.kwargs["token"]["session_id"] == "fake-session"


def test_explicit_camera_key_skips_cloud_key_challenge():
    api = Mock()
    assert probe.camera_key(api, {"camera_key": "fake-key", "decrypt_video": True}) == "fake-key"
    assert probe.camera_key(api, {"decrypt_video": False}) is None
    api.get_cam_key.assert_not_called()


def test_login_code_is_not_forwarded_to_camera_key(tmp_path, monkeypatch):
    api = Mock()
    api.export_token.return_value = {}
    monkeypatch.setattr(probe, "EzvizClient", Mock(return_value=api))
    options = {"account": "test", "password": "fake", "api_host": "apiieu.ezvizlife.com", "verification_code": "123456", "serial": "device"}
    probe.authenticate(options, tmp_path / "session.json")
    probe.camera_key(api, options)
    api.login.assert_called_once_with(sms_code=123456)
    api.get_cam_key.assert_called_once_with("device", smscode=None)


def test_failure_hint_never_echoes_private_error():
    assert "private-token" not in probe.failure_hint("login", Exception("private-token"))
