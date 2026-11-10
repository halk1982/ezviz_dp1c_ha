"""Normalize only documented pyezvizapi message fields; don't guess alarm meanings."""
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class Event:
    id: str
    code: str
    timestamp: float
    picture: str


def normalize(message, serial):
    if message.get("deviceSerial") != serial or not message.get("msgId"):
        return None
    ext = message.get("ext")
    ext = ext if isinstance(ext, dict) else {}
    try:
        timestamp = float(message["time"])
        if timestamp > 10**11:
            timestamp /= 1000
        datetime.fromtimestamp(timestamp, timezone.utc)
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        return None
    pics = ext.get("pics")
    picture = message.get("pic") or (pics.split(";")[0] if isinstance(pics, str) else "")
    return Event(str(message["msgId"]), str(ext.get("alarmType") or message.get("subType") or "unknown"), timestamp, picture if isinstance(picture, str) else "")


class EventTracker:
    """Suppress history on every process start and bound in-memory deduplication."""
    def __init__(self, max_age):
        self.max_age = max_age
        self.seen = set()
        self.initialized = False

    def pending(self, events, now):
        if not self.initialized:
            self.seen = {e.id for e in events}
            self.initialized = True
            return []
        return [e for e in sorted(events, key=lambda e: e.timestamp)
                if e.id not in self.seen and 0 <= now - e.timestamp <= self.max_age]

    def commit(self, event):
        self.seen.add(event.id)

    def prune(self, events):
        self.seen.intersection_update(e.id for e in events)


def classify(code, motion_codes, doorbell_codes):
    if code in doorbell_codes:
        return "doorbell"
    if code in motion_codes:
        return "motion"
    return "unknown"
