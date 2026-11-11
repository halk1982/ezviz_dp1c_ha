from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import bootstrap


@pytest.mark.parametrize("failure", [ImportError("PRIVATE"), AttributeError("PRIVATE")])
def test_missing_streaming_import_stops_before_account_access(monkeypatch, capsys, failure):
    probe = SimpleNamespace(main=Mock())
    monkeypatch.setattr(bootstrap.importlib, "import_module", Mock(side_effect=[probe, failure]))
    assert bootstrap.main() == 1
    probe.main.assert_not_called()
    output = capsys.readouterr().out
    assert "stage=startup_check" in output
    assert "PRIVATE" not in output


def test_missing_ffprobe_stops_before_account_access(monkeypatch, capsys):
    probe = SimpleNamespace(main=Mock())
    monkeypatch.setattr(bootstrap.importlib, "import_module", Mock(return_value=probe))
    monkeypatch.setattr(bootstrap.subprocess, "run", Mock(side_effect=[Mock(), FileNotFoundError("PRIVATE")]))
    assert bootstrap.main() == 1
    probe.main.assert_not_called()
    assert "PRIVATE" not in capsys.readouterr().out


def test_valid_runtime_dispatches_to_the_selected_mode(monkeypatch):
    probe = SimpleNamespace(main=Mock(return_value=7))
    monkeypatch.setattr(bootstrap.importlib, "import_module", Mock(return_value=probe))
    monkeypatch.setattr(bootstrap.subprocess, "run", Mock())
    assert bootstrap.main() == 7
    probe.main.assert_called_once_with()
