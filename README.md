# EZVIZ DP1C for Home Assistant

Experimental Home Assistant OS add-ons for the EZVIZ DP1C door viewer. The main
**Cloud Video** add-on provides on-demand cloud live video and JPEG still images
for Home Assistant's **Generic Camera** integration.

This is a community project, not an official EZVIZ or Home Assistant product.

For development handoffs and the immediate next task, read [PROJECT_STATE.md](PROJECT_STATE.md).

## What works, and what is next?

| Feature | Status |
|---|---|
| Live video, clear passthrough + compatible encoding | Normal picture confirmed by the owner on their DP1C since 0.2.3 |
| JPEG still-image endpoint | Added in 0.2.4; synthetic tests pass, physical-camera and HomeKit checks pending |
| Motion and last alarm image | Keep your existing RenierM26 HACS integration |
| Audio / intercom | Not implemented |
| Wake from sleep, long-term playback, battery impact | Need physical-camera testing |
| Architectures | amd64 tested; aarch64 declared, not validated here |

See the [changelog](dp1c_video_probe/CHANGELOG.md) and
[investigation record](docs/investigation.md) for evidence and limitations.

## Install and get a picture

1. In Home Assistant OS, open **Settings → Add-ons → Add-on Store → ⋮ → Repositories**
   and add `https://github.com/halk1982/ezviz_dp1c_ha`.
2. Install **EZVIZ DP1C Cloud Video**. Existing **Cloud Video Probe** installations
   use the same slug and can update without replacing their configuration.
3. Enter your EZVIZ account, password, camera serial and API region locally.
   Choose a separate stream password, then use:

   ```yaml
   mode: live
   decrypt_video: false
   video_mode: compatible
   ```

4. Follow the [Generic Camera setup guide](dp1c_video_probe/DOCS.md), including
   Basic authentication. In 0.2.4+, use these URLs with your HA machine's LAN IP:

   | Generic Camera field | URL |
   |---|---|
   | Stream source | `http://HOME_ASSISTANT_LAN_IP:8558/live.ts` |
   | Still image URL | `http://HOME_ASSISTANT_LAN_IP:8558/snapshot.jpg` |

   On **0.2.3**, leave Still image URL blank; it has no JPEG endpoint.

**Upgrading from 0.2.2 or earlier:** explicitly set `decrypt_video: false` and
restart. Existing options are preserved. The tested feed was already clear;
unnecessary decryption caused the earlier grey pictures. Encrypted alarm images
do not imply that live video needs decryption.

Keep **Preload stream disabled** for this battery camera. JPEG requests cache a
frame for 30 seconds, then can briefly start live video again. Frequent HomeKit
thumbnail polling can therefore use more battery. Read the
[HomeKit test steps](dp1c_video_probe/DOCS.md#still-images-and-homekit-024).
Keep credentials and footage private; use the HTTP endpoints on your trusted LAN.

## Which add-on should I use?

- **Cloud Video** is the current focus. `live` mode serves video/stills; `probe`
  mode makes a short diagnostic capture and stops. Advanced decryption settings
  remain available for feeds that actually need them.
- **Cloud Bridge** is an earlier MQTT alarm/event prototype, separate from Cloud
  Video. It mostly overlaps the existing HACS integration and has not been
  physically validated. It is not needed for live video or `/snapshot.jpg`.
  Its [configuration and limitations](docs/legacy-bridge.md) remain available.

## Versions and releases

The add-on store reads `version` from the repository's add-on `config.yaml`; an
installable version does not require a GitHub Release. The 0.2.3 version fixes the
confirmed grey-picture issue. The 0.2.4 changes add still images for testing.

Test the Generic Camera still image, Apple Home thumbnail/doorbell notification,
idle shutdown and battery behavior before promoting snapshot support as stable.
Use a GitHub **prerelease** for an explicitly experimental testing milestone; a
stable release should describe what was physically verified and its known limits.
Do not treat an add-on version number or passing synthetic tests as evidence of
full HomeKit support.

## License and contributions

Original project code is licensed under [MIT](LICENSE). Third-party dependencies
retain their own licenses. Bug reports should include the add-on/HA version and
redacted diagnostic lines, never credentials, signed URLs, raw API responses or footage.

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

The clear-video regression demonstrates the confirmed failure mechanism:

```sh
.venv/bin/python tests/passthrough_smoke.py
```

It verifies clear H.264 in both output modes and reproduces corruption when
unnecessary decryption is applied. All media is synthetic.

JPEG/HTTP smoke test (no EZVIZ credentials required):

```sh
.venv/bin/python tests/snapshot_smoke.py
```

It tests real JPEG decoding, concurrent requests, the short cache, sharing with an
active producer, and timeout cleanup. The same script can run in the video image
with the tests directory mounted at `/test` and `PYTHONPATH=/app`.
