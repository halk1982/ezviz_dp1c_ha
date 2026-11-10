"""Check decrypted output with PyAV and fragmented MP4 options used by HA.

This is a targeted compatibility check, not a full Home Assistant installation.
Run using the development venv with av==19.0.0, as in current HA dev requirements.
"""
import io
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dp1c_video_probe/app"))
import av
from encrypted_media_smoke import FakeStream, KEY, make_fixture
from media import remux_stream

with tempfile.TemporaryDirectory() as temporary:
    directory = Path(temporary)
    for header_size in (0, 1):
        source_dir = directory / str(header_size)
        source_dir.mkdir()
        make_fixture(source_dir, header_size=header_size)
        for video_mode in ("copy", "compatible"):
            data = io.BytesIO()
            reports = []
            remux_stream(FakeStream(source_dir), data, key=KEY, max_seconds=3, emit=reports.append, video_mode=video_mode, decrypt_mode="auto")
            report = next(json.loads(item.split(": ", 1)[1]) for item in reports if item.startswith("LIVE_CHECK: "))
            assert report["status"] == "pass", report
            if video_mode == "compatible":
                assert report["profile"] in {"Baseline", "Constrained Baseline"}
            assert report["missing_dts"] == 0 and report["non_increasing_dts"] == 0
            comparison = next(json.loads(item.split(": ", 1)[1]) for item in reports if item.startswith("LIVE_DECRYPT_CHECK:"))
            assert comparison["header_bytes"] == header_size
            assert comparison["status"] == ("equal" if header_size == 0 else "different")
            container = av.open(io.BytesIO(data.getvalue()), format="mpegts")
            video = container.streams.video[0]
            assert video.codec_context.width == 320 and video.codec_context.height == 240
            output = io.BytesIO()
            target = av.open(output, mode="w", format="mp4", container_options={
                "movflags": "empty_moov+default_base_moof+frag_discont+negative_cts_offsets+skip_trailer+delay_moov",
                "avoid_negative_ts": "make_non_negative", "fragment_index": "1",
                "video_track_timescale": str(int(1 / video.time_base)), "frag_duration": "900000",
            })
            mapped = target.add_stream_from_template(video)
            last_dts = None
            packet_count = 0
            keyframes = 0
            for packet in container.demux(video):
                if not packet.size:
                    continue
                assert packet.dts is not None and packet.pts is not None
                assert last_dts is None or packet.dts > last_dts
                last_dts = packet.dts
                keyframes += bool(packet.is_keyframe)
                packet.stream = mapped
                target.mux(packet)
                packet_count += 1
            target.close()
            container.close()
            assert packet_count >= 5 and keyframes > 0
            result = av.open(io.BytesIO(output.getvalue()))
            decoded = 0
            for frame in result.decode(video=0):
                decoded += 1
                if decoded == 5:
                    break
            result.close()
            assert decoded == 5
            print(f"PASS: header_bytes={header_size}, mode={video_mode}; PyAV accepts timestamps, remuxes to fragmented MP4, and decodes five frames")
