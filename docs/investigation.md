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
