import base64
from concurrent.futures import ThreadPoolExecutor
import http.client
from http.server import ThreadingHTTPServer
import threading
from unittest.mock import Mock

import pytest

from live import Hub, handler_for
from snapshot import Snapshots, SnapshotUnavailable


def test_concurrent_requests_share_capture_and_cache():
    snapshots = Snapshots(Mock())
    started = threading.Event()
    finish = threading.Event()

    def capture():
        started.set()
        assert finish.wait(timeout=3)
        return b"synthetic-jpeg"

    snapshots._capture = Mock(side_effect=capture)
    with ThreadPoolExecutor(max_workers=4) as pool:
        first = pool.submit(snapshots.get)
        assert started.wait(timeout=3)
        others = [pool.submit(snapshots.get) for _ in range(3)]
        finish.set()
        assert [task.result(timeout=3) for task in [first, *others]] == [b"synthetic-jpeg"] * 4
    assert snapshots.get() == b"synthetic-jpeg"
    snapshots._capture.assert_called_once()


def test_expired_image_is_refreshed_and_never_masks_failure():
    snapshots = Snapshots(Mock())
    snapshots._capture = Mock(side_effect=[b"old-image", b"new-image", ValueError("PRIVATE")])
    assert snapshots.get() == b"old-image"
    snapshots.expires = 0
    assert snapshots.get() == b"new-image"
    snapshots.expires = 0
    with pytest.raises(SnapshotUnavailable) as failure:
        snapshots.get()
    assert "PRIVATE" not in str(failure.value)
    with pytest.raises(SnapshotUnavailable):
        snapshots.get()
    assert snapshots._capture.call_count == 3  # Retry cooldown prevents a new capture.
    assert not snapshots.capturing


def test_capture_failure_releases_waiters_without_another_attempt():
    snapshots = Snapshots(Mock())
    snapshots._capture = Mock(side_effect=ValueError("PRIVATE"))
    with ThreadPoolExecutor(max_workers=4) as pool:
        tasks = [pool.submit(snapshots.get) for _ in range(4)]
        for task in tasks:
            with pytest.raises(SnapshotUnavailable):
                task.result(timeout=3)
    snapshots._capture.assert_called_once()


@pytest.fixture
def snapshot_http():
    hub = Hub(["must-not-run"])
    snapshots = Mock()
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(hub, "viewer", "long_password_1234", snapshots))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield hub, snapshots, server.server_address
    server.shutdown()
    server.server_close()
    hub.close()


@pytest.mark.parametrize("authorization", [None, "Basic wrong"])
def test_unauthenticated_snapshot_never_starts_capture(snapshot_http, authorization):
    hub, snapshots, address = snapshot_http
    connection = http.client.HTTPConnection(*address, timeout=3)
    try:
        connection.request("GET", "/snapshot.jpg", headers={"Authorization": authorization} if authorization else {})
        response = connection.getresponse()
        assert response.status == 401
        assert response.getheader("WWW-Authenticate") == 'Basic realm="DP1C"'
        assert b"stream_password" in response.read()
        snapshots.get.assert_not_called()
        assert hub.process is None
    finally:
        connection.close()


def test_snapshot_is_jpeg_with_stream_credentials_and_no_browser_cache(snapshot_http):
    hub, snapshots, address = snapshot_http
    snapshots.get.return_value = b"\xff\xd8synthetic\xff\xd9"
    connection = http.client.HTTPConnection(*address, timeout=3)
    try:
        header = "Basic " + base64.b64encode(b"viewer:long_password_1234").decode()
        connection.request("GET", "/snapshot.jpg", headers={"Authorization": header})
        response = connection.getresponse()
        assert response.status == 200
        assert response.getheader("Content-Type") == "image/jpeg"
        assert response.getheader("Cache-Control") == "no-store"
        assert int(response.getheader("Content-Length")) == len(snapshots.get.return_value)
        assert response.read() == snapshots.get.return_value
    finally:
        connection.close()


def test_snapshot_failure_returns_503_without_private_diagnostics(snapshot_http):
    hub, snapshots, address = snapshot_http
    snapshots.get.side_effect = SnapshotUnavailable("PRIVATE")
    connection = http.client.HTTPConnection(*address, timeout=3)
    try:
        header = "Basic " + base64.b64encode(b"viewer:long_password_1234").decode()
        connection.request("GET", "/snapshot.jpg", headers={"Authorization": header})
        response = connection.getresponse()
        assert response.status == 503
        assert b"PRIVATE" not in response.read()
    finally:
        connection.close()
