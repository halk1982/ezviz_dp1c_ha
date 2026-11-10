"""Clear-video regression: passthrough succeeds, unnecessary decryption corrupts.

All media and keys are synthetic. No camera/cloud requests are made.
"""
import io
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace

repo = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(repo / 'tests'), str(repo / 'dp1c_video_probe/app')]
from encrypted_media_smoke import KEY, PACK, PSM, make_fixture, pes
from media import remux_stream, RemuxFailed
from pyezvizapi.stream import decrypt_hikvision_ps_video

class ClearStream:
    def __init__(self, packets):
        self.packets = packets
        self.closed = False
    def iter_packets(self, **kwargs):
        for data in self.packets:
            if self.closed:
                return
            for offset in range(0, len(data), 997):
                yield SimpleNamespace(body=data[offset:offset+997], encrypted=False)
            time.sleep(0.1)
    def close(self):
        self.closed = True

with tempfile.TemporaryDirectory() as temporary:
    directory = Path(temporary)
    make_fixture(directory)
    encrypted = [bytes.fromhex(frame) for frame in json.loads((directory / 'frames.json').read_text())]
    packets = [decrypt_hikvision_ps_video(PACK + PSM + pes(frame, i * 9000), KEY, nalu_header_size=0)
               for i, frame in enumerate(encrypted)]
    for video_mode in ('copy', 'compatible'):
        reports = []
        remux_stream(ClearStream(packets), io.BytesIO(), key=None, max_seconds=3,
                     emit=reports.append, video_mode=video_mode, decrypt_mode='auto')
        check = next(json.loads(line.split(': ', 1)[1]) for line in reports if line.startswith('LIVE_CHECK:'))
        assert check['status'] == 'pass', check
        assert check['timestamp_status'] == 'valid'
        assert not any(line.startswith('LIVE_DECRYPT_CHECK:') for line in reports)
        print(f'PASS: clear H.264, mode={video_mode}, five decoded frames and valid timestamps')
    reports = []
    try:
        remux_stream(ClearStream(packets), io.BytesIO(), key=KEY, max_seconds=3,
                     emit=reports.append, video_mode='compatible', decrypt_mode='auto')
    except (RemuxFailed, BrokenPipeError):
        pass
    flags = [line for line in reports if line.startswith('LIVE_REMUX:') or line.startswith('LIVE_MEDIA:')]
    assert any('invalid_media' in line or 'missing_pps' in line or 'codec_parameters_missing' in line for line in flags), flags
    print('REPRODUCED: applying decryption to clear video causes source codec/decode errors')
