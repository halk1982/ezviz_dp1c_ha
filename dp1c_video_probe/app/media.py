"""Incremental encrypted MPEG-PS to MPEG-TS with bounded, content-free diagnostics."""
from contextlib import suppress
import os
import select
import subprocess
import threading
import time

from Crypto.Cipher import AES
from checks import LocalChecks, error_flags
from pyezvizapi.__main__ import _BufferedStreamPayloadDecryptor
from pyezvizapi.stream import (
    _mpeg_ps_complete_packet_ranges,
    _pes_payload_start,
    detect_hikvision_ps_video_nalu_header_size,
    mpeg_ps_decryptable_prefix_length,
)


class MediaBufferLimit(ValueError):
    pass


class TransportEncryptionUnsupported(ValueError):
    pass


class RemuxFailed(ValueError):
    pass


class BoundedDecryptor(_BufferedStreamPayloadDecryptor):
    """Release complete NAL runs without waiting for an audio PES or stream end.

    Keep PES continuations together. An additional safe boundary is the start of
    a video PES whose payload starts a fresh, plausibly encrypted Annex B NAL.
    Only the prefix before that packet is decrypted, so a NAL/AES block split
    across packets remains intact. The pinned library still performs decryption.
    """

    def __init__(self, key, *, codec):
        super().__init__(key, codec=codec)
        material = key.encode() if isinstance(key, str) else key
        self._cipher = AES.new(material.ljust(16, b"\0")[:16], AES.MODE_ECB)
        self.peak_pending = 0

    def _fresh_nal(self, payload):
        marker = 4 if payload.startswith(b"\0\0\0\1") else 3 if payload.startswith(b"\0\0\1") else 0
        if not marker:
            return False
        body = payload[marker:]
        if self._nalu_header_size in (None, 1) and body:
            if (body[0] & 0x80) == 0 and 1 <= (body[0] & 0x1F) <= 23:
                return True
        if self._nalu_header_size in (None, 2) and len(body) >= 2:
            if (body[0] & 0x80) == 0 and (body[1] & 7) != 0 and ((body[0] >> 1) & 0x3F) <= 40:
                return True
        # Short, unencrypted H.264 access-unit delimiters are also boundaries.
        if body[:2] == b"\x09\xf0":
            return True
        if len(body) < 16:
            return False
        header = self._cipher.decrypt(body[:16])
        h264 = (header[0] & 0x80) == 0 and 1 <= (header[0] & 0x1F) <= 23
        hevc = (header[0] & 0x80) == 0 and (header[1] & 7) != 0 and ((header[0] >> 1) & 0x3F) <= 40
        return bool(h264 or hevc)

    def __call__(self, data):
        if len(self._buffer) + len(data) > 8 * 1024 * 1024:
            raise MediaBufferLimit("Media decryption buffer limit exceeded")
        self._buffer.extend(data)
        self.peak_pending = max(self.peak_pending, len(self._buffer))
        packets = _mpeg_ps_complete_packet_ranges(self._buffer)
        if self._nalu_header_size is None:
            # A short clear AUD alone is not evidence that later VCL headers
            # are also clear. Inspect a representative prefix before fixing
            # the mode for the session. All bytes remain buffered, not dropped.
            video_packets = sum(0xE0 <= packet.stream_id <= 0xEF for packet in packets)
            if video_packets < 8 and len(self._buffer) < 16384:
                return b""
            detected = detect_hikvision_ps_video_nalu_header_size(bytes(self._buffer), self._key, default=None)
            if detected is None:
                return b""
            self._nalu_header_size = detected
        complete_end = mpeg_ps_decryptable_prefix_length(self._buffer)
        for packet in packets:
            if packet.start <= complete_end or not 0xE0 <= packet.stream_id <= 0xEF:
                continue
            start = _pes_payload_start(self._buffer, packet.start)
            if start is not None and self._fresh_nal(bytes(self._buffer[start:packet.end])):
                complete_end = packet.start
        if not complete_end:
            return b""
        chunk = bytes(self._buffer[:complete_end])
        del self._buffer[:complete_end]
        return self._decrypt_chunk(chunk)


def ffmpeg_command(video_mode):
    if video_mode not in {"copy", "compatible"}:
        raise ValueError("Unsupported video mode")
    encoding = ["-c:v", "copy"] if video_mode == "copy" else [
        "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
        "-profile:v", "baseline", "-pix_fmt", "yuv420p", "-vf", "setpts=PTS-STARTPTS",
        "-r", "10", "-fps_mode", "cfr", "-g", "20", "-keyint_min", "20",
        "-bf", "0", "-sc_threshold", "0", "-x264-params", "repeat-headers=1",
    ]
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-analyzeduration", "500000", "-probesize", "65536", "-fflags", "+genpts",
        "-f", "mpeg", "-i", "pipe:0", "-map", "0:v:0", *encoding, "-an",
        "-mpegts_flags", "resend_headers", "-muxdelay", "0", "-muxpreload", "0",
        "-flush_packets", "1", "-f", "mpegts", "pipe:1",
    ]


def remux_stream(stream, output, *, key, max_seconds, emit=print, video_mode="copy", decrypt_mode="encrypted-header"):
    """Exercise the real encrypted-media pipeline; do not log media or URLs."""
    emit(f"LIVE_VIDEO: mode={video_mode}")
    process = subprocess.Popen(ffmpeg_command(video_mode), stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    decryptor = BoundedDecryptor(key, codec=decrypt_mode) if key else None
    stats = {"packets": 0, "cloud_bytes": 0, "clear_bytes": 0, "ts_bytes": 0}
    errors = []
    flags = set()
    checks = LocalChecks(key, emit, pipeline_flags=lambda: flags.copy())
    stopped = threading.Event()
    started = time.monotonic()

    def report():
        pending = len(decryptor._buffer) if decryptor else 0
        peak = decryptor.peak_pending if decryptor else 0
        values = " ".join(f"{name}={value}" for name, value in stats.items())
        emit(f"LIVE_MEDIA: {values} pending_bytes={pending} peak_pending_bytes={peak} ffmpeg_flags={','.join(sorted(flags.copy())) or 'none'}")

    def read_errors():
        # Classify fixed FFmpeg messages. Never relay its raw stderr/metadata.
        known = {b"could not find codec parameters": "codec_parameters_missing",
                 b"invalid data found": "invalid_media",
                 b"non monotonically increasing dts": "non_monotonic_timestamps",
                 b"does not contain any stream": "no_video_track"}
        while line := process.stderr.readline(4096):
            flags.update(error_flags(line))
            for text, label in known.items():
                if text in line.lower():
                    flags.add(label)

    def write_input():
        reported_header = False
        try:
            for packet in stream.iter_packets(max_packets=None):
                if stopped.is_set() or time.monotonic() - started >= max_seconds:
                    break
                if packet.encrypted:
                    raise TransportEncryptionUnsupported()
                stats["packets"] += 1
                stats["cloud_bytes"] += len(packet.body)
                if stats["packets"] == 1:
                    emit("LIVE_STAGE: receiving_cloud_media")
                clear = decryptor(packet.body) if decryptor else packet.body
                header = decryptor._nalu_header_size if decryptor else None
                if decryptor and header is not None and not reported_header:
                    emit(f"LIVE_DECRYPT_MODE: mode={decrypt_mode} header_bytes={header}")
                    reported_header = True
                checks.decrypted(packet.body, clear, header_size=header)
                if clear:
                    if not stats["clear_bytes"]:
                        emit("LIVE_STAGE: decrypted_media_ready")
                    stats["clear_bytes"] += len(clear)
                    process.stdin.write(clear)
                    process.stdin.flush()
            if decryptor and not stopped.is_set():
                tail = decryptor.flush()
                checks.decrypted(b"", tail, header_size=decryptor._nalu_header_size)
                stats["clear_bytes"] += len(tail)
                process.stdin.write(tail)
        except Exception as error:
            errors.append(error)
        finally:
            with suppress(OSError):
                process.stdin.close()

    writer = threading.Thread(target=write_input, daemon=True)
    stderr_reader = threading.Thread(target=read_errors, daemon=True)
    writer.start()
    stderr_reader.start()
    last_report = started
    try:
        while True:
            if time.monotonic() - last_report >= 5:
                report()
                last_report = time.monotonic()
            ready, _, _ = select.select([process.stdout], [], [], 0.5)
            if not ready:
                continue
            # read(n) on BufferedReader can wait for a full 64 KiB. Read only
            # the bytes currently available, including the first TS packets.
            chunk = os.read(process.stdout.fileno(), 188 * 64)
            if not chunk:
                break
            if not stats["ts_bytes"]:
                emit("LIVE_STAGE: mpegts_output_ready")
            stats["ts_bytes"] += len(chunk)
            checks.output(chunk)
            output.write(chunk)
            output.flush()
        result = process.wait(timeout=2)
        if errors:
            raise errors[0]
        if result or not stats["ts_bytes"]:
            raise RemuxFailed()
        return stats
    finally:
        stopped.set()
        with suppress(Exception):
            stream.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        writer.join(timeout=2)
        stderr_reader.join(timeout=2)
        checks.finish()
        report()
        emit(f"LIVE_REMUX: exit_code={process.returncode} flags={','.join(sorted(flags)) or 'none'}")
        process.stdout.close()
        process.stderr.close()
