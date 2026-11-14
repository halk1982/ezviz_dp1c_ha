# Project state

Last updated: 2026-11-14. Read this first when resuming in a new chat. Update it
after meaningful changes or new camera-test results; keep detailed history in the
changelog and investigation record.

Repository: https://github.com/halk1982/ezviz-dp1c-ha. The previous
`halk1982/ezviz_dp1c_ha` URL redirects to this renamed repository.

## Purpose and stack

Integrate the EZVIZ DP1C door viewer with Home Assistant OS, primarily through the
Cloud Video add-on and Generic Camera. Python 3.12+, `pyezvizapi==1.0.5.0`, FFmpeg/
FFprobe, Python's HTTP server, Docker/Supervisor; pytest and PyAV for development.
An earlier MQTT bridge uses Paho MQTT. Original project code is MIT licensed.

## Completed and current status

- **0.2.4 is pushed to GitHub `main`** (application commit `782366f`). It is an
  experimental add-on version; no stable GitHub Release was created in this work.
- The owner confirmed normal live video with `mode: live`, `decrypt_video: false`,
  `video_mode: compatible`. Unnecessary decryption caused the earlier grey picture.
  Alarm-image encryption does not establish that live video needs decryption.
- `/live.ts` provides authenticated, on-demand video. `/snapshot.jpg` adds fresh
  JPEG captures with a **30-second memory cache**, sharing the same cloud producer
  and credentials. Images are not saved to disk by the snapshot endpoint.
- Startup dependency checks, an HTTP 401 hint, clearer public documentation and
  the MIT license are implemented. Advanced options and the add-on slug are retained.
- **Physical-camera/HomeKit validation of 0.2.4 is still pending.** Installation
  and snapshot success have not been reported by the owner in this conversation.
- Keep the existing RenierM26 HACS integration for working motion/alarm images.
  The separate MQTT Cloud Bridge remains an unvalidated prototype.
- Audio/intercom is not implemented. Sleep/wake behavior, sustained viewing,
  battery impact and aarch64 builds remain unverified. Keep Preload stream disabled;
  thumbnail polling can repeatedly start live video after the cache expires.

## File map

| File or directory | Responsibility |
|---|---|
| [README.md](README.md) | Public overview, installation and feature status |
| [dp1c_video_probe/config.yaml](dp1c_video_probe/config.yaml) | Add-on version, options, schema and port 8558 |
| [dp1c_video_probe/Dockerfile](dp1c_video_probe/Dockerfile) | Runtime image and startup entry point |
| [dp1c_video_probe/app/bootstrap.py](dp1c_video_probe/app/bootstrap.py) | Startup import and FFmpeg/FFprobe checks |
| [dp1c_video_probe/app/probe.py](dp1c_video_probe/app/probe.py) | Mode selection, authentication/key caches, diagnostic capture |
| [dp1c_video_probe/app/live.py](dp1c_video_probe/app/live.py) | Shared producer lifecycle, HTTP routes and authentication |
| [dp1c_video_probe/app/snapshot.py](dp1c_video_probe/app/snapshot.py) | Bounded JPEG extraction, concurrent captures and short cache |
| [dp1c_video_probe/app/media.py](dp1c_video_probe/app/media.py) / [checks.py](dp1c_video_probe/app/checks.py) | Decryption/remuxing and bounded output diagnostics |
| [dp1c_video_probe/DOCS.md](dp1c_video_probe/DOCS.md) / [CHANGELOG.md](dp1c_video_probe/CHANGELOG.md) | User setup, HomeKit test steps and version history |
| [dp1c/app/](dp1c/app/) / [docs/legacy-bridge.md](docs/legacy-bridge.md) | Earlier MQTT event/alarm-image prototype |
| [tests/](tests/) | Unit tests and synthetic media/HTTP/MQTT smoke checks |
| [docs/investigation.md](docs/investigation.md) | Detailed evidence, prior diagnoses and validation limits |

## Validation and development

At application commit `782366f`: **64 tests passed**. The amd64 container passed
real JPEG decoding/cache/sharing/timeout checks, FFprobe output checks, live HTTP
streaming and encrypted-media regression checks. These used synthetic media and
do not establish physical DP1C or HomeKit compatibility.

Use the existing checkout and `.venv`; useful commands from the repository root:

```sh
.venv/bin/python -m pytest -q
.venv/bin/python tests/snapshot_smoke.py
```

The smoke check requires FFmpeg/FFprobe. See README for installation and container
checks. Keep credentials, token files, signed URLs and footage out of Git and chat.

## Immediate next task

Validate **0.2.4 on the owner's DP1C and existing HomeKit doorbell setup** before
starting intercom work:

1. Confirm the installed version and live picture. Set Generic Camera's still URL
   to `http://HOME_ASSISTANT_LAN_IP:8558/snapshot.jpg`, keeping Basic authentication.
2. Check the HA still preview, Apple Home thumbnail and doorbell notification image.
   Check freshness after more than 30 seconds; Apple Home can cache its own thumbnail.
3. Confirm idle producer shutdown and assess latency, sleep/wake and battery use.
4. Record observed results here, diagnose failures, then decide on a prerelease or
   stable release. Do not label physical checks passed without owner/runtime evidence.
