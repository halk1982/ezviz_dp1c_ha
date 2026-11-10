# Investigation record

Scope: user's DP1C, normal EZVIZ cloud access, snapshots/motion/doorbell events,
Home Assistant OS. User confirmed camera/account ownership. No account credentials
were supplied or used. No device configuration was changed.

## Observations

1. A GET to `https://ieu.ezvizlife.com/user/userAction!goToMyShipin7.action`
   was blocked by this cloud environment's proxy: CONNECT 403. No page content,
   JavaScript, authenticated browser requests or legacy player were inspected.
   The Internet Explorer requirement is user-reported, not independently verified.
2. Current Home Assistant `dev` source was fetched from GitHub:
   - https://github.com/home-assistant/core/blob/dev/homeassistant/components/ezviz/manifest.json
   - https://github.com/home-assistant/core/blob/dev/homeassistant/components/ezviz/image.py
   - https://github.com/home-assistant/core/blob/dev/homeassistant/components/ezviz/binary_sensor.py
   Its manifest pins `pyezvizapi==1.0.5.0`. The image platform reads `last_alarm_pic`
   and can decrypt it; the binary sensor exposes `Motion_Trigger`. These source
   references are mutable and do not establish what the user's installed HA version has.
3. PyPI `pyezvizapi` 1.0.5.0 source archive was downloaded and its published SHA-256
   verified before extraction. Package `client.py` defines `get_device_messages_list`
   with serial filtering, pagination via `msgId`, and `message`/`messages` response lists.
   `camera.py` normalizes `time`, `pic`, `ext.pics`, `ext.alarmType`, and `subType`.
   `utils.decrypt_image` supports encrypted alarm pictures.
   Source project: https://github.com/RenierM26/pyEzvizApi
4. The REA plugin exposed guidance only, not callable REA analysis providers. Work
   used shell tools, HTTPS downloads, source inspection and local tests. No REA
   runtime capture or authenticated cloud trace was performed.

## Inference

The requested alarm/event features may be accessible without the old web player or
RTSP. An MQTT add-on can bridge the library's cloud messages into Home Assistant.
This is an interoperability prototype based on existing source, not a completed
reverse engineering of the legacy website.

## Unknowns requiring camera validation

- Whether this DP1C/account/firmware publishes compatible cloud messages.
- Whether button presses and motion have distinct and stable codes.
- Whether alarm pictures are provided and the device image key decrypts them.
- Authentication challenges, region routing, CDN hosts, cloud latency and rate limits.
- Supervisor installation and aarch64 image behavior.

To inspect the legacy site here, environment Internet access would need to allow
`ieu.ezvizlife.com`. Direct account API testing would additionally need its actual
API host and verified image hosts. No network configuration changes were saved:
configuration draft reads returned an error. The initial validation route is the
user's Home Assistant, with credentials retained there.

## Validation completed in this environment

- `python -m pytest -q`: **12 passed** (synthetic message normalization, serial
  isolation, startup suppression, event age/deduplication, explicit code mapping,
  pagination, image URL/content checks, token permissions and image retry).
- `pip check`: no broken requirements.
- `docker build`: amd64 image built successfully with TLS verification enabled.
- `sh tests/run_mqtt_smoke.sh`: passed against a real Mosquitto broker on an isolated
  Docker network. Verified three discovery payloads, no startup event replay, one
  classified event despite repeat polling, and base64 snapshot transport. Cloud
  responses and image bytes were synthetic; this does not validate EZVIZ access or
  Home Assistant's entity rendering. Test containers/network were removed.

## Live-video follow-up

The user reports that RenierM26's HACS integration 0.1.0.61 already supplies motion
and the last-motion image, while the official phone app plays live video. We have
not independently inspected that exact HACS version. The focus is now cloud video.

The pinned library's README documents experimental `stream dump` and `stream proxy`
commands. Its `EzvizClient.save_clip(source="cloud")` calls VTM cloud capture helpers
and can decrypt media using the authenticated camera key. This is source evidence
of an implementation path, not proof of DP1C support.

Added the separate **Cloud Video Probe** add-on: a bounded ten-second capture,
FFprobe stream check, actual FFmpeg decode of up to five frames, and a two-minute
whole-process timeout. No public HTTP stream endpoint is exposed. User credentials
and captured media remain on the Home Assistant host.

Validation: **18 unit tests passed**. The amd64 probe image built with FFmpeg and
pinned Python dependencies. A network-disabled container generated a synthetic H.264
clip; the actual decoder accepted five frames and rejected an invalid-media file.
Cloud image builds used temporary BuildKit CA mounts for this environment's HTTPS
proxy; published Dockerfiles use standard RUN instructions for Supervisor builder
compatibility, without requiring BuildKit secrets. Runtime source and dependencies
are identical. Supervisor and physical-camera tests remain unrun.

## Verification-code correction (0.1.1)

The user's first probe run logged successful login followed by
`cloud_capture / EzvizAuthVerificationCode`. After an emailed code was entered in
`verification_code`, the next run logged `login / PyEzvizError`. These logs contain
no server response code and cannot establish the exact reason for the second failure.

Source inspection identified a probe bug: `verification_code` was passed only to
`login(sms_code=...)`. The library's cloud decrypt path separately calls
`get_cam_key(serial, smscode=...)`, which raises `EzvizAuthVerificationCode` on result
code 20002. Version 0.1.1 separates these inputs, fetches the key at an explicit
`camera_key` stage, and supplies that key to capture. Successful login sessions are
stored privately for the next attempt. No real user credentials were used here.

Regression validation: 22 unit tests passed, including separate code routing,
leading-zero preservation for camera-access codes, session reuse, and private token
file permissions. Physical-device verification still requires the user's next run.

## Camera-key challenge follow-up (0.1.2)

User testing of 0.1.1 reached `camera_key / EzvizAuthVerificationCode`, then
`camera_key / PyEzvizError` with a submitted email code and a reused session.
The generic error does not identify EZVIZ's response code or establish why the
request was rejected. Previous versions also did not explicitly request the
operation-specific challenge. The pinned client's `get_2fa_check_code` documents
`DEVICE_ENCRYPTION` and `DEVICE_AUTH_CODE` as separate operations from login's
`TERMINAL_BIND`.

Version 0.1.2 requests `DEVICE_ENCRYPTION` only when camera-key retrieval requires
verification and no camera code has been supplied. It preserves the updated session
and adds numeric-only, allowlisted API status diagnostics. Response messages,
identifiers, email addresses, key material and tokens are excluded. This addresses
a missing request step; it is not a confirmed diagnosis of the user's server error.
Regression suite: 25 tests passed, including challenge request selection, no new
challenge during code submission, leading-zero preservation and diagnostic redaction.

## Successful physical probe and live-mode implementation (0.2.0)

The user supplied a new probe log showing successful login/session reuse, camera
key retrieval, cloud capture and video decode, followed by:
`PASS: {"codec": "h264", "width": 1088, "height": 720, "decoded_frames": 5}`.
This is user-reported runtime evidence of a successful short capture on their DP1C;
we did not receive or inspect the footage. It does not establish sustained streaming.

Version 0.2.0 adds a live mode to the same add-on. The implementation uses the pinned
library's `open_cloud_stream`, `_BufferedStreamPayloadDecryptor` and stream remux
helper. The encrypted-header setting matches the successful capture's default
`nalu_header_size=0`. It exposes a Basic-authenticated MPEG-TS HTTP endpoint, shares
one producer, limits viewers and buffers, stops idle producers and blocks automatic
retries after a verification challenge. Camera keys are cached privately.

Home Assistant's current Generic Camera source was inspected: `stream_source()`
adds configured username/password to the source URL, and `use_stream_for_stills`
is enabled when the still-image URL is empty. Sources:
https://github.com/home-assistant/core/blob/dev/homeassistant/components/generic/camera.py
https://github.com/home-assistant/core/blob/dev/homeassistant/components/generic/config_flow.py
These are source observations, not validation in the user's HA version.

Local validation includes AES-decrypted synthetic NALs split across seven-byte
chunks, credential rejection before producer start, shared producer lifecycle,
viewer limits, bounded memory, authentication-retry blocking, and key-cache isolation.
An actual HTTP/FFmpeg container test decoded five frames for each of two simultaneous
viewers of synthetic video, rejected an unauthenticated request, and confirmed the
producer stopped after disconnection. No EZVIZ credentials were used locally.
Actual continuous cloud decryption, startup latency, wake behavior, battery use and
Home Assistant playback still require the user's next live-mode test.

Release validation: 36 unit tests passed. After the final lifecycle adjustment,
all 10 live-mode tests passed again and the real HTTP/FFmpeg smoke test passed
against the rebuilt amd64 image. No physical-camera live-mode run was performed here.

## Live-preview stall investigation (0.2.1)

The user reported successful live-server startup followed by two generic producer
stop messages, while Generic Camera displayed "Stream never started". A comparison
immediately after opening and closing the official app's live view still failed.
The user confirmed that the device-encryption email code was entered in the correct
`camera_verification_code` field. Neither sleep nor incorrect code placement was
established as the cause.

A deterministic regression test failed on 0.2.0: two encrypted video PES packets with
fresh NALs and no audio/non-video PES produced zero output until flush. The upstream
buffer helper retains a trailing video PES run until a non-video packet or EOF;
metadata/pack headers do not end the run. This is a confirmed software defect in the
chosen live pipeline, independent of the physical camera's state. It is a candidate
explanation for the user's symptom, not yet verified against their actual packets.

The fix adds conservative boundaries at complete video PES packets whose payloads
start a fresh Annex B NAL, with encrypted-header plausibility checks. Continuations
and split AES blocks remain buffered together; the pinned library still performs
media decryption. The 8 MiB cap remains. FFmpeg startup buffering and output reads
are reduced. Safe diagnostics now identify connection phases, byte counters,
remux result categories, exit codes and stop reasons. No raw responses or media are
logged, and no speculative wake/sleep device API calls were added.

A new network-disabled container regression generates H.264, encrypts synthetic NAL
prefixes, wraps video-only MPEG-PS with timestamps, splits it into cloud-like packet
bodies, and exercises the actual live producer, authenticated HTTP endpoint and a
real FFmpeg decoder. It checks five frames arrive before the source ends. The
synthetic fixture uses block-padded short NALs to make the pinned library's encrypted
header detector unambiguous, verifies equality with bounded whole-capture decryption,
and does not represent an observed DP1C packet trace. That test passed. Unit tests
also cover cross-PES continuations, video-only emission before EOF, and redaction of
private error payloads. Physical sustained playback and automatic wake-up remain
unverified until the user tests the updated version.

Release validation: all 39 unit tests passed. The rebuilt amd64 image passed the
full encrypted-media producer/HTTP/FFmpeg test with five decoded frames delivered
before the synthetic source ended. The physical DP1C's behavior on 0.2.1 remains
pending. The existing Generic Camera settings and private caches are preserved.
