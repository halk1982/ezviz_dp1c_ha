# EZVIZ DP1C Home Assistant experiments

## Start here: live video

Keep your RenierM26 HACS integration for working motion and snapshots. This
repository's **EZVIZ DP1C Cloud Video** add-on provides two modes:

- `probe`: a short private capture and video-decode check, then stop.
- `live`: an experimental authenticated, on-demand MPEG-TS endpoint for Home
  Assistant's **Generic Camera** integration, with one shared cloud connection.

The probe identified **H.264, 1088 × 720** and returned decoded frames, but the
user then reported a mostly grey image with occasional normal video. **Usable
picture quality and sustained playback remain unresolved.** A decoder PASS is not
proof that the image is correct.
It does not use the legacy Internet Explorer player or local RTSP.

In Home Assistant OS, go to **Settings → Add-ons → Add-on Store → ⋮ → Repositories**
and add:

```text
https://github.com/halk1982/ezviz_dp1c_ha
```

Install **EZVIZ DP1C Cloud Video**, or update your existing **Cloud Video Probe**
installation to **0.2.2** (same slug; configuration retained). Follow the
[live-view setup instructions](dp1c_video_probe/DOCS.md) to enable live mode and add
Generic Camera. The add-on does not create a camera entity automatically.
If the Generic Camera browser preview reports media-content errors, version 0.2.2
adds `video_mode: compatible` and local, privacy-preserving decode/timestamp checks.
Compatible mode uses more CPU; the default `copy` mode preserves the camera bitstream.
Credentials stay in your local configuration. Never upload them or footage here.

## Earlier snapshot/event prototype

The **Cloud Bridge** below is a separate prototype. It is not needed for the initial
video test and mostly overlaps your existing HACS integration.


A Home Assistant OS add-on prototype for **cloud alarm snapshots and events** via MQTT.
It does not require local RTSP or the Internet Explorer player. It uses the existing
`pyezvizapi` library, also used by Home Assistant's EZVIZ integration.

**DP1C compatibility has not yet been tested against a real account/camera.**
An installable container and synthetic tests do not establish that the DP1C publishes
usable events or snapshots. Doorbell and motion codes deliberately have no defaults.

First consider the built-in **EZVIZ** integration: its current development source
already supports cloud motion images and motion status. Tell us what it exposes for
your DP1C. This add-on is for validating and extending cloud event support.

## Install on Home Assistant OS

Use the repository URL above for installation. For local testing, copy the `dp1c` directory into
`/addons/ezviz_dp1c_cloud` on your Home Assistant host using Samba/SSH. Reload the
add-on store, then install **EZVIZ DP1C Cloud Bridge** from Local add-ons.

1. Install/start the Mosquitto broker add-on and configure Home Assistant's MQTT integration.
2. Open this add-on's Configuration tab. Enter your EZVIZ account/password, camera
   serial, and MQTT credentials **there, not in chat or Git**.
3. Leave `api_host: apiieu.ezvizlife.com` for an account in that EU region. If your
   account uses a different EZVIZ API region, supply its correct hostname.
4. Leave both code lists empty for the first run. Start the add-on and inspect its log.
5. Home Assistant MQTT discovery should create a DP1C device with **Activity**, **Alarm
   Code**, and **Snapshot** entities. Activity is an event entity: create an automation
   triggered by its state change and inspect its `event_type` attribute.
6. Follow [the local validation procedure](dp1c/DOCS.md). A doorbell press or movement
   may be published as `unknown` until its observed code has been assigned.

## Behavior and limits

- Default polling is 60 seconds, with exponential backoff to 15 minutes on errors.
  Cloud delivery may add latency; this is not a real-time doorbell notification system.
- First successful poll suppresses history but retrieves the latest available alarm
  picture. On restart, events during downtime are deliberately not replayed.
- Only timestamped messages for the configured serial are accepted. Events older
  than `max_event_age` (180 seconds) or dated in the future are not emitted.
- Up to five pages / 250 messages are read per poll. Older messages may be omitted.
- Snapshots are the **last cloud alarm image**, not a fresh image on demand. A sleeping
  camera is not forcibly woken. No device settings or encryption settings are changed.
- Unknown event types stay unknown. Ring and movement may share a code; in that case
  this version cannot distinguish them. Do not assign one code to both lists.
- JPEG image URLs must use HTTPS and an allowed `image_hosts` suffix (default:
  `ezvizlife.com`). Redirects are not followed. An image CDN on another domain needs
  an explicitly verified host added locally. API session headers are not sent to CDNs.
- Encrypted images require the device image verification/encryption key in `image_key`.
  The key is used locally; encryption is not disabled.
- MQTT event messages are not retained. Discovery, latest alarm code and snapshot are
  retained by the broker. Protect MQTT credentials/access and enable MQTT TLS when
  required. Broker retention means the latest image can survive add-on removal.
- MQTT QoS 1 can redeliver messages; this is not an exactly-once alarm system.
- Tokens are stored with owner-only permissions in `/data/token.json`; add-on backups
  can contain credentials. Keep backups private. No credentials are embedded in code.
- Only amd64 and aarch64 are declared; the current environment tests amd64 only.

## Development

Python 3.12+:

```sh
python3 -m venv .venv
.venv/bin/pip install -r dp1c/requirements.txt pytest==8.4.2
.venv/bin/python -m pytest -q

docker build -t ezviz-dp1c:test dp1c
```

For standalone execution, set `DP1C_OPTIONS` to a private JSON file containing the
add-on options and `DP1C_DATA` to a private writable directory, then run
`python dp1c/app/bridge.py`. Never commit these files.

See [investigation evidence](docs/investigation.md) for verified facts and remaining gaps.

For the real MQTT transport smoke test, after building the image:

```sh
docker pull eclipse-mosquitto:2
sh tests/run_mqtt_smoke.sh
```

This test creates an isolated Docker network and broker, uses synthetic cloud
messages, then removes its containers/network. It requires no camera credentials.

Video-probe development check (requires Docker):

```sh
docker build -t ezviz-dp1c-probe:test dp1c_video_probe
docker run --rm --network none -e PYTHONPATH=/app \
  -v "$PWD/tests/probe_smoke.py:/test/probe_smoke.py:ro" \
  ezviz-dp1c-probe:test python /test/probe_smoke.py
```

This generates synthetic video and tests the real decoder; it does not contact EZVIZ.

The encrypted-media regression also exercises the actual producer and decryptor:

```sh
docker run --rm --network none -e PYTHONPATH=/app \
  -v "$PWD/tests/encrypted_media_smoke.py:/test/encrypted_media_smoke.py:ro" \
  ezviz-dp1c-probe:test python /test/encrypted_media_smoke.py
```

It generates and encrypts synthetic video locally. No EZVIZ account is contacted.

For the targeted Home Assistant media-library compatibility check:

```sh
.venv/bin/pip install av==19.0.0
.venv/bin/python tests/pyav_smoke.py
```

This checks both video output modes with PyAV and fragmented MP4 settings from
current Home Assistant source; it does not run Home Assistant or a real camera.
