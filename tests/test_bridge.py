import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import bridge
from events import Event, EventTracker, classify, normalize


def test_normalization_and_device_filter():
    row = {"deviceSerial": "test", "msgId": "42", "time": "1700000000000", "subType": "fallback", "ext": {"alarmType": "observed", "pics": "https://image/a;https://image/b"}}
    assert normalize(row, "test") == Event("42", "observed", 1700000000, "https://image/a")
    assert normalize(row, "other") is None
    assert normalize({**row, "time": "invalid"}, "test") is None
    assert normalize({**row, "ext": {"pics": None}}, "test").picture == ""


def test_no_history_replay_dedup_and_age():
    old = Event("old", "x", 90, "")
    new = Event("new", "x", 105, "")
    stale = Event("stale", "x", 1, "")
    future = Event("future", "x", 999, "")
    tracker = EventTracker(30)
    assert tracker.pending([old], 100) == []
    assert tracker.pending([old, new, stale, future], 110) == [new]
    # A publish failure must leave the event pending.
    assert tracker.pending([new], 111) == [new]
    tracker.commit(new)
    assert tracker.pending([new], 112) == []


def test_no_guessed_doorbell_mapping():
    assert classify("123", [], []) == "unknown"
    assert classify("123", [], ["123"]) == "doorbell"
    assert classify("456", ["456"], ["123"]) == "motion"


def test_pagination_and_shape():
    api = Mock()
    api.get_device_messages_list.side_effect = [
        {"message": [{"msgId": "a"}], "hasNext": True},
        {"messages": [{"msgId": "b"}], "hasNext": False}]
    assert len(bridge.fetch_messages(api, "test")) == 2
    assert api.get_device_messages_list.call_args.kwargs["end_time"] == "a"
    api.get_device_messages_list.side_effect = None
    api.get_device_messages_list.return_value = {"unexpected": []}
    with pytest.raises(ValueError):
        bridge.fetch_messages(api, "test")


@pytest.mark.parametrize("url", ["http://a.ezvizlife.com/p", "https://evil.example/p", "https://ezvizlife.com.evil.example/p", "https://user:pass@ezvizlife.com/p", "https://ezvizlife.com:8443/p"])
def test_image_url_rejected_before_request(url, monkeypatch):
    request = Mock()
    monkeypatch.setattr(bridge.requests, "get", request)
    with pytest.raises(ValueError):
        bridge.get_picture(url, "", ["ezvizlife.com"])
    request.assert_not_called()


def test_image_separate_client_and_content_check(monkeypatch):
    response = Mock(status_code=200)
    response.iter_content.return_value = [b"\xff\xd8\xfftest"]
    context = Mock()
    context.__enter__ = Mock(return_value=response)
    context.__exit__ = Mock(return_value=False)
    request = Mock(return_value=context)
    monkeypatch.setattr(bridge.requests, "get", request)
    assert bridge.get_picture("https://a.ezvizlife.com/image", "", ["ezvizlife.com"]).startswith(b"\xff\xd8\xff")
    assert request.call_args.kwargs == {"timeout": 20, "stream": True, "allow_redirects": False}
    response.iter_content.return_value = [b"<html>error</html>"]
    with pytest.raises(ValueError):
        bridge.get_picture("https://a.ezvizlife.com/image", "", ["ezvizlife.com"])


def test_token_permissions(tmp_path):
    path = tmp_path / "token.json"
    bridge.save_token(path, {"session_id": "fake"})
    assert path.stat().st_mode & 0o777 == 0o600
    assert json.loads(path.read_text()) == {"session_id": "fake"}


def test_poll_filters_other_camera_and_retries_image(monkeypatch, tmp_path):
    options = {"serial": "test", "max_event_age": 180, "mqtt_username": "", "mqtt_password": "", "mqtt_tls": False, "motion_codes": [], "doorbell_codes": ["bell"], "image_key": "", "image_hosts": ["ezvizlife.com"]}
    instance = bridge.Bridge(options, tmp_path)
    instance.publish = Mock()
    api = Mock()
    rows = [{"deviceSerial": "test", "msgId": "a", "time": 100, "subType": "bell", "pic": "https://ezvizlife.com/a"}]
    api.get_device_messages_list.return_value = {"message": rows}
    monkeypatch.setattr(bridge.time, "time", lambda: 110)
    picture = Mock(side_effect=[ValueError(), b"\xff\xd8\xfftest"])
    monkeypatch.setattr(bridge, "get_picture", picture)
    instance.poll(api)
    assert not any(c.args[0] == "event" for c in instance.publish.call_args_list)
    rows.extend([{"deviceSerial": "other", "msgId": "b", "time": 105, "subType": "bell"}, {"deviceSerial": "test", "msgId": "c", "time": 106, "subType": "bell"}])
    instance.poll(api)
    events = [json.loads(c.args[1]) for c in instance.publish.call_args_list if c.args[0] == "event"]
    assert events == [{"event_type": "doorbell", "code": "bell", "timestamp": 106}]
    assert picture.call_count == 2
    assert instance.last_picture == "https://ezvizlife.com/a"
