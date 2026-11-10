"""Real broker transport test, with synthetic cloud responses and image bytes."""
import base64
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from unittest.mock import Mock

sys.path.insert(0, "/app")
import bridge
import paho.mqtt.client as mqtt

messages = []
ready = threading.Event()
subscriber = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
subscriber.on_connect = lambda client, *_: client.subscribe("#", qos=1)
subscriber.on_subscribe = lambda *_: ready.set()
subscriber.on_message = lambda client, userdata, message: messages.append((message.topic, message.payload))
subscriber.connect("broker", 1883)
subscriber.loop_start()
assert ready.wait(10), "subscriber failed to connect"
options = {"serial": "test-device", "max_event_age": 180, "mqtt_username": "", "mqtt_password": "", "mqtt_tls": False, "motion_codes": ["motion-test"], "doorbell_codes": ["bell-test"], "image_key": "", "image_hosts": ["ezvizlife.com"]}
instance = bridge.Bridge(options, Path(tempfile.mkdtemp()))
instance.mqtt.connect("broker", 1883)
instance.mqtt.loop_start()
try:
    assert instance.connected.wait(10), "bridge failed to connect"
    now = time.time()
    old = {"deviceSerial": "test-device", "msgId": "old", "time": now - 60, "subType": "motion-test"}
    api = Mock()
    api.get_device_messages_list.return_value = {"message": [old]}
    instance.poll(api)
    assert not any(topic.endswith("/event") for topic, _ in messages), "startup replayed history"
    new = {"deviceSerial": "test-device", "msgId": "new", "time": now, "subType": "bell-test", "pic": "https://ezvizlife.com/test"}
    api.get_device_messages_list.return_value = {"message": [new, old]}
    bridge.get_picture = lambda *_: b"\xff\xd8\xffsynthetic-image"
    instance.poll(api)
    instance.poll(api)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if any(topic.endswith("/image") for topic, _ in messages):
            break
        time.sleep(0.05)
    events = [json.loads(data) for topic, data in messages if topic.endswith("/event")]
    assert len(events) == 1 and events[0]["event_type"] == "doorbell", events
    configs = [json.loads(data) for topic, data in messages if topic.endswith("/config")]
    assert len(configs) == 3, configs
    assert any(base64.b64decode(data) == b"\xff\xd8\xffsynthetic-image" for topic, data in messages if topic.endswith("/image"))
    print("PASS: real MQTT discovery, startup suppression, event delivery, deduplication, snapshot transport")
finally:
    instance.mqtt.disconnect()
    instance.mqtt.loop_stop()
    subscriber.disconnect()
    subscriber.loop_stop()
