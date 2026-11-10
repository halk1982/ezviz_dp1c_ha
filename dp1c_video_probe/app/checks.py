"""Bounded local media checks. Only allowlisted results leave this module."""
import json
import math
import subprocess
import threading
import time

from pyezvizapi.stream import decrypt_hikvision_ps_video


def error_flags(stderr):
    known = {b"non-existing pps": "missing_pps",
             b"sps_id": "sps_error",
             b"decode_slice_header error": "slice_header_error",
             b"error while decoding mb": "macroblock_decode_error",
             b"reference picture missing": "missing_reference",
             b"concealing": "concealed_decode_errors",
             b"cabac decode": "cabac_decode_error",
             b"invalid data found": "invalid_media",
             b"no frame": "no_decoded_frame",
             b"non monotonically increasing dts": "non_monotonic_timestamps"}
    return sorted(label for text, label in known.items() if text in stderr.lower())


def inspect_ts(data):
    """Probe a private in-memory sample, then require five actual decoded frames."""
    if len(data) < 188 * 3:
        return {"status": "sample_too_short"}
    result = subprocess.run([
        "ffprobe", "-v", "error", "-f", "mpegts", "-i", "pipe:0", "-select_streams", "v:0",
        "-read_intervals", "%+#80", "-show_streams", "-show_packets", "-show_entries",
        "stream=codec_name,width,height,profile,pix_fmt:packet=pts,dts,dts_time,flags", "-of", "json",
    ], input=data, capture_output=True, timeout=10)
    report = {"status": "probe_error", "probe_exit_code": result.returncode, "visual_quality": "not_assessed"}
    if result.returncode:
        report["flags"] = error_flags(result.stderr)
        return report
    info = json.loads(result.stdout)
    video = next(iter(info.get("streams", [])), {})
    for name in ("width", "height"):
        value = video.get(name)
        if type(value) is int and 0 <= value <= 16384:
            report[name] = value
    report["codec"] = video.get("codec_name") if video.get("codec_name") in {"h264", "hevc", "mpeg2video"} else "other"
    report["pixel_format"] = video.get("pix_fmt") if video.get("pix_fmt") in {"yuv420p", "yuvj420p", "yuv422p", "yuv444p", "yuv420p10le", "yuv422p10le", "yuv444p10le"} else "other"
    report["profile"] = video.get("profile") if video.get("profile") in {"Baseline", "Constrained Baseline", "Main", "High", "High 10", "High 4:2:2", "High 4:4:4 Predictive"} else "other"
    packets = info.get("packets", [])[:80]
    report.update(packet_count=len(packets), missing_dts=0, non_increasing_dts=0,
                  keyframes=0, max_dts_gap_seconds=0.0)
    last_dts = last_time = None
    for packet in packets:
        dts = packet.get("dts")
        if type(dts) is not int:
            report["missing_dts"] += 1
        else:
            if last_dts is not None and dts <= last_dts:
                report["non_increasing_dts"] += 1
            last_dts = dts
        if "K" in packet.get("flags", ""):
            report["keyframes"] += 1
        try:
            value = float(packet["dts_time"])
            if not math.isfinite(value):
                continue
        except (KeyError, TypeError, ValueError):
            continue
        if last_time is not None:
            report["max_dts_gap_seconds"] = min(1000000.0, max(report["max_dts_gap_seconds"], abs(value - last_time)))
        last_time = value
    report["max_dts_gap_seconds"] = round(report["max_dts_gap_seconds"], 3)
    anomalous_time = bool(report["missing_dts"] or report["non_increasing_dts"]
                          or report["max_dts_gap_seconds"] > 30)
    report["timestamp_status"] = "anomalous" if anomalous_time else "valid" if packets else "unknown"
    decoded = subprocess.run([
        "ffmpeg", "-v", "error", "-xerror", "-f", "mpegts", "-i", "pipe:0",
        "-map", "0:v:0", "-frames:v", "5", "-f", "framemd5", "-",
    ], input=data, capture_output=True, timeout=10)
    frames = sum(1 for line in decoded.stdout.splitlines() if line and not line.startswith(b"#"))
    report.update(decoded_frames=min(frames, 5), decode_exit_code=decoded.returncode,
                  flags=error_flags(decoded.stderr))
    report["status"] = ("decode_error" if decoded.returncode else "sample_too_short" if frames < 5
                        else "timestamp_error" if anomalous_time else "no_keyframe" if not report["keyframes"]
                        else "pass")
    return report


class LocalChecks:
    """Once per producer: bounded samples, no files or media uploaded/logged."""
    def __init__(self, key, emit, window_seconds=5, max_bytes=2 * 1024 * 1024):
        self.key = key
        self.emit = emit
        self.window_seconds = window_seconds
        self.max_bytes = max_bytes
        self.raw = bytearray()
        self.clear = bytearray()
        self.ts = bytearray()
        self.decrypt_started = None
        self.ts_started = None
        self.decrypt_done = False
        self.header_size = 0
        self.ts_done = False
        self.threads = []

    def _launch(self, label, function):
        def run():
            try:
                result = function()
            except Exception as error:
                result = {"status": "check_error", "error_class": type(error).__name__}
            self.emit(label + ": " + json.dumps(result, sort_keys=True))
        thread = threading.Thread(target=run, daemon=True)
        self.threads.append(thread)
        thread.start()

    def decrypted(self, raw, clear, header_size=None):
        if not self.key or self.decrypt_done:
            return
        if header_size in (0, 1, 2):
            self.header_size = header_size
        self.decrypt_started = self.decrypt_started or time.monotonic()
        if len(self.raw) + len(raw) > self.max_bytes:
            self._compare()
            return
        self.raw.extend(raw)
        self.clear.extend(clear)
        if self.clear and time.monotonic() - self.decrypt_started >= self.window_seconds:
            self._compare()

    def _compare(self):
        if self.decrypt_done:
            return
        self.decrypt_done = True
        emitted = bytes(self.clear)
        source = bytes(self.raw[:len(emitted)])
        self.raw.clear()
        self.clear.clear()
        if not emitted:
            return

        def compare():
            bounded = decrypt_hikvision_ps_video(source, self.key, nalu_header_size=self.header_size)
            return {"status": "equal" if bounded == emitted else "different",
                    "compared_bytes": len(emitted), "header_bytes": self.header_size}
        self._launch("LIVE_DECRYPT_CHECK", compare)

    def output(self, data):
        if self.ts_done:
            return
        self.ts_started = self.ts_started or time.monotonic()
        remaining = self.max_bytes - len(self.ts)
        self.ts.extend(data[:remaining])
        if len(self.ts) >= self.max_bytes or time.monotonic() - self.ts_started >= self.window_seconds:
            self._check()

    def _check(self):
        if self.ts_done:
            return
        self.ts_done = True
        sample = bytes(self.ts)
        self.ts.clear()
        self._launch("LIVE_CHECK", lambda: inspect_ts(sample))

    def finish(self):
        self._compare()
        self._check()
        deadline = time.monotonic() + 12
        for thread in self.threads:
            thread.join(timeout=max(0, deadline - time.monotonic()))
