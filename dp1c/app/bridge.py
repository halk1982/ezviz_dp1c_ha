"""Read EZVIZ cloud messages and publish bounded, credential-free MQTT payloads."""
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import signal
import threading
import time
from urllib.parse import urlsplit

import paho.mqtt.client as mqtt
import requests
from pyezvizapi.client import EzvizClient
from pyezvizapi.exceptions import EzvizAuthTokenExpired, EzvizAuthVerificationCode
from pyezvizapi.utils import decrypt_image

from events import EventTracker, classify, normalize

LOG = logging.getLogger("dp1c")


def save_token(path, token):
    temp = path.with_suffix(".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(token, f)
    os.replace(temp, path)


def load_options(path):
    options = json.loads(path.read_text())
    for key in ("account", "password", "serial", "mqtt_host"):
        if not options.get(key):
            raise ValueError(f"Set {key} in the add-on configuration")
    if not re.fullmatch(r"[a-zA-Z0-9-]+\.ezvizlife\.com", options["api_host"]):
        raise ValueError("api_host must be an EZVIZ API hostname")
    if set(options["motion_codes"]) & set(options["doorbell_codes"]):
        raise ValueError("Motion and doorbell codes must not overlap")
    if not 30 <= options["poll_seconds"] <= 3600:
        raise ValueError("poll_seconds must be between 30 and 3600")
    return options


def fetch_messages(api, serial):
    result = []
    cursor = ""
    for _ in range(5):
        response = api.get_device_messages_list(serials=serial, date="", limit=50, end_time=cursor)
        rows = response.get("message", response.get("messages"))
        if not isinstance(rows, list):
            raise ValueError("Unexpected cloud message response")
        result.extend(row for row in rows if isinstance(row, dict))
        if not response.get("hasNext"):
            return result
        next_cursor = str(rows[-1].get("msgId", "")) if rows else ""
        if not next_cursor or next_cursor == cursor:
            raise ValueError("Invalid pagination cursor")
        cursor = next_cursor
    LOG.warning("Message history capped at 250 entries; older events may be omitted")
    return result


def get_picture(url, key, allowed_hosts):
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError("Invalid snapshot URL")
    if not any(host == domain or host.endswith("." + domain) for domain in allowed_hosts if domain):
        raise ValueError("Snapshot host is not in image_hosts")
    # A separate request avoids sending the API sessionId to an image/CDN host.
    with requests.get(url, timeout=20, stream=True, allow_redirects=False) as response:
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError("Snapshot redirects are not followed")
        data = bytearray()
        for chunk in response.iter_content(65536):
            data.extend(chunk)
            if len(data) > 8 * 1024 * 1024:
                raise ValueError("Snapshot exceeds 8 MiB")
    image = bytes(data)
    if b"hikencodepicture" in image:
        if not key:
            raise ValueError("Encrypted snapshot requires image_key")
        image = decrypt_image(image, key)
    if not image.startswith(b"\xff\xd8\xff"):
        raise ValueError("Snapshot is not a JPEG")
    return image


class Bridge:
    def __init__(self, options, data_dir):
        self.options = options
        self.data_dir = data_dir
        self.stop = threading.Event()
        self.connected = threading.Event()
        self.uid = "ezviz_dp1c_" + hashlib.sha256(options["serial"].encode()).hexdigest()[:12]
        self.topic = self.uid
        self.tracker = EventTracker(options["max_event_age"])
        self.last_picture = None
        self.cloud_ok = False
        self.mqtt = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self.uid)
        self.mqtt.username_pw_set(options["mqtt_username"], options["mqtt_password"])
        if options["mqtt_tls"]:
            self.mqtt.tls_set()
        self.mqtt.will_set(self.topic + "/availability", "offline", qos=1, retain=True)
        self.mqtt.on_connect = self.on_connect
        self.mqtt.on_disconnect = self.on_disconnect
        self.mqtt.on_message = self.on_message

    def discovery(self):
        device = {"identifiers": [self.uid], "name": "EZVIZ DP1C", "manufacturer": "EZVIZ", "model": "DP1C (experimental cloud)"}
        for component, suffix, details in (
            ("event", "activity", {"state_topic": self.topic + "/event", "event_types": ["motion", "doorbell", "unknown"]}),
            ("camera", "snapshot", {"topic": self.topic + "/image", "image_encoding": "b64"}),
            ("sensor", "alarm_code", {"state_topic": self.topic + "/code", "entity_category": "diagnostic"}),
        ):
            config = {"name": suffix.replace("_", " ").title(), "unique_id": self.uid + "_" + suffix,
                      "device": device, "availability_topic": self.topic + "/availability", **details}
            self.mqtt.publish(f"homeassistant/{component}/{self.uid}/{suffix}/config", json.dumps(config), qos=1, retain=True)

    def on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            LOG.error("MQTT connection rejected")
            return
        self.discovery()
        client.subscribe("homeassistant/status")
        client.publish(self.topic + "/availability", "online" if self.cloud_ok else "offline", qos=1, retain=True)
        self.connected.set()

    def on_disconnect(self, client, userdata, flags, reason_code, properties):
        self.connected.clear()

    def on_message(self, client, userdata, message):
        if message.topic == "homeassistant/status" and message.payload == b"online":
            self.discovery()

    def publish(self, suffix, payload, retain=False):
        result = self.mqtt.publish(self.topic + "/" + suffix, payload, qos=1, retain=retain)
        result.wait_for_publish(timeout=10)
        if not result.is_published():
            raise ConnectionError("MQTT publish timed out")

    def poll(self, api):
        events = {e.id: e for row in fetch_messages(api, self.options["serial"])
                  if (e := normalize(row, self.options["serial"])) is not None}
        events = list(events.values())
        for event in self.tracker.pending(events, time.time()):
            kind = classify(event.code, self.options["motion_codes"], self.options["doorbell_codes"])
            self.publish("event", json.dumps({"event_type": kind, "code": event.code, "timestamp": event.timestamp}))
            self.tracker.commit(event)
            LOG.info("New cloud event: category=%s code=%s", kind, re.sub(r"[^a-zA-Z0-9_-]", "?", event.code)[:32])
        self.tracker.prune(events)
        if events:
            latest = max(events, key=lambda e: e.timestamp)
            self.publish("code", latest.code, retain=True)
            pictures = [e for e in events if e.picture]
            if pictures:
                picture = max(pictures, key=lambda e: e.timestamp)
                if picture.picture != self.last_picture:
                    try:
                        import base64
                        image = get_picture(picture.picture, self.options["image_key"], self.options["image_hosts"])
                        self.publish("image", base64.b64encode(image), retain=True)
                        self.last_picture = picture.picture
                    except Exception as error:
                        # Exception text may contain signed URLs or private API payloads.
                        LOG.warning("Snapshot unavailable (%s); check image_key/image_hosts", type(error).__name__)

    def run(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        token_path = self.data_dir / "token.json"
        identity = hashlib.sha256((self.options["account"] + "@" + self.options["api_host"]).encode()).hexdigest()
        saved = json.loads(token_path.read_text()) if token_path.exists() else {}
        token = saved.get("token") if saved.get("identity") == identity else None
        api = EzvizClient(self.options["account"], self.options["password"], url=self.options["api_host"], token=token, timeout=20)
        self.mqtt.connect_async(self.options["mqtt_host"], self.options["mqtt_port"], keepalive=60)
        self.mqtt.loop_start()
        delay = self.options["poll_seconds"]
        authenticated = False
        try:
            while not self.stop.is_set():
                if not self.connected.wait(timeout=2):
                    continue
                try:
                    if not authenticated:
                        code = self.options["verification_code"]
                        api.login(sms_code=int(code) if code else None)
                        authenticated = True
                    self.poll(api)
                    save_token(token_path, {"identity": identity, "token": api.export_token()})
                    self.cloud_ok = True
                    self.publish("availability", "online", retain=True)
                    delay = self.options["poll_seconds"]
                except (EzvizAuthVerificationCode, EzvizAuthTokenExpired):
                    LOG.error("Authentication requires attention; update credentials/verification_code locally and restart")
                    break
                except Exception as error:
                    self.cloud_ok = False
                    self.mqtt.publish(self.topic + "/availability", "offline", qos=1, retain=True)
                    LOG.warning("Cloud/MQTT cycle failed (%s); retrying with backoff", type(error).__name__)
                    delay = min(delay * 2, 900)
                self.stop.wait(delay)
        finally:
            if self.connected.is_set():
                self.publish("availability", "offline", retain=True)
            self.mqtt.disconnect()
            self.mqtt.loop_stop()
            # Do not logout/revoke the saved cloud session on process shutdown.


def main():
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    LOG.setLevel(logging.INFO)
    # Upstream exception/debug payloads can contain session data and image URLs.
    logging.getLogger("pyezvizapi").setLevel(logging.CRITICAL + 1)
    os.umask(0o077)
    try:
        bridge = Bridge(load_options(Path(os.environ.get("DP1C_OPTIONS", "/data/options.json"))), Path(os.environ.get("DP1C_DATA", "/data")))
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: bridge.stop.set())
        bridge.run()
    except Exception as error:
        LOG.error("Startup stopped (%s); check configuration", type(error).__name__)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
