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
