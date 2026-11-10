# EZVIZ DP1C Cloud Video

Version 0.2.2 adds local media checks and an optional compatibility encoder; version 0.2.0 introduced **live mode** to the same add-on that ran the
initial capture test. A decode PASS does not establish correct picture quality. The slug remains `ezviz_dp1c_video_probe`; update the
existing installation to keep your configuration and private session.

## Live mode: next step after a successful probe

1. Update the existing add-on to **0.2.2**. Keep RenierM26's HACS integration for
   motion and alarm images. MQTT and another EZVIZ integration are not required.
2. In the add-on Configuration set:

   ```yaml
   mode: live
   decrypt_mode: auto
   video_mode: copy
   stream_username: dp1c
   stream_password: "CHOOSE_A_NEW_RANDOM_PASSWORD"
   max_view_seconds: 300
   verification_code: ""
   camera_verification_code: ""
   ```

   Replace the password placeholder with a new random 16–128 character password,
   using letters, digits, `_` or `-`. This protects the local video endpoint; it
   is separate from your EZVIZ password. Keep your existing account, password,
   serial, API host and `decrypt_video: true`. Leave `camera_key` empty unless you
   know your permanent device encryption key.
3. Save and Start. If prompted for camera verification, put the newest
   **[device encryption]** email code in `camera_verification_code`, keep
   `verification_code` empty, then Save and Start again. Upgrading from 0.1.2 may
   require this once: that version did not cache the retrieved camera key.
4. Wait for `LIVE: ready on port 8558, path /live.ts`. In live mode the add-on stays
   running. There is no browser player or Ingress/Open Web UI button in this version.
5. In Home Assistant, open **Settings → Devices & services → Add integration →
   Generic Camera**. Enter:

   | Field | Value |
   |---|---|
   | Stream source | `http://HOME_ASSISTANT_LAN_IP:8558/live.ts` |
   | Still image URL | Leave blank |
   | Authentication | Basic |
   | Username | Your `stream_username`, normally `dp1c` |
   | Password | Your new `stream_password` (not the EZVIZ password) |

   Replace `HOME_ASSISTANT_LAN_IP` with your HA machine's LAN address. Use the
   actual mapped port shown in the add-on Network section if 8558 was changed.
   Home Assistant's current Generic Camera code adds these credentials to the
   stream request and can generate still images from the stream.
6. Finish the camera setup and open its live view. Share whether the preview works,
   approximate startup delay, and only sanitized `LIVE_FAIL`/`FAIL`/`API_CODES`
   lines if it fails. Never post credentials, full URLs containing passwords,
   signed cloud URLs, or captured footage.

The server binds only in the add-on container and exposes the configured host port.
The local endpoint uses HTTP Basic authentication, not TLS: use it on your trusted
LAN and **do not port-forward it to the Internet**. Use Home Assistant's existing
secured access to view the camera remotely.

## Behavior and limits

- The server starts the cloud stream on demand, with a single producer shared by
  up to four consumers. Slow consumers are disconnected instead of blocking others.
- It closes the producer about ten seconds after the last HTTP consumer is detected
  as disconnected. Home Assistant may keep its stream open briefly after a viewer
  closes; the add-on cannot infer whether someone is still watching the screen.
- Keep camera **Preload stream** disabled. A dashboard live card, still-image
  refresh, or recorder can keep/reopen the stream and drain the camera battery.
- A producer is limited to `max_view_seconds` (default 300, range 30–900). Clients may
  reconnect after it ends. This is intended for short views, not continuous recording.
- Output is **video only**. `video_mode: copy` preserves encoded H.264 with minimal
  CPU use. `video_mode: compatible` decodes and re-encodes as 10 fps Baseline H.264,
  with no B-frames, a timeline starting at zero and repeated headers/keyframes.
  Compatible mode uses more CPU. Audio and two-way talk are not implemented.
- The upstream incremental decryptor is used with the same encrypted-header setting
  as the successful probe. Its private helpers are pinned to pyezvizapi 1.0.5.0.
  Buffering is bounded at 8 MiB. Sustained DP1C playback still needs user validation.
- Startup/media timeout is 45 seconds. Stream failures cause a one-minute cooldown.
  An explicit verification-code failure blocks new producers until the add-on is
  restarted, to avoid repeated challenge requests from reconnecting viewers.
- Sessions and the resolved camera key are cached privately in `/data`. These
  files can be included in add-on backups; keep backups private. A changed account,
  region or camera invalidates the key cache. A supplied `camera_key` overrides it.
  If the camera encryption key changes and you cannot supply the new one, stop the
  add-on and remove its private `/data/camera_key.json` before restarting.
- Authentication, decoder and HTTP sharing tests ran locally with synthetic data.
  The user reported a successful physical DP1C probe at 1088×720 H.264. Sustained
  live mode and Home Assistant's resulting camera entity have not yet been tested
  against that physical device. Only the amd64 container was built here.

## Probe mode and verification fields

Set `mode: probe` to run a bounded ten-second capture and actual decode check, then
stop. Successful tests print PASS and save `/media/ezviz_dp1c_probe/live-test.ts`.
Browse Media → My media or use Samba; download/open in VLC if the browser cannot
play MPEG-TS. This mode does not expose `/live.ts` or create a camera entity.
The next probe replaces the old clip. Keep video private and delete it when done.

| Field | Purpose |
|---|---|
| `verification_code` | Account **login** challenge only. Normally empty with a saved session. |
| `camera_verification_code` | Temporary **[device encryption]** email code. |
| `camera_key` | Permanent device encryption key, never an emailed one-time code. |
| `stream_password` | New password you choose for the local HTTP video endpoint. |

A `camera_key / EzvizAuthVerificationCode` failure with no code supplied requests
an operation-specific DEVICE_ENCRYPTION code. Put the new email code in
`camera_verification_code`, keep `verification_code` empty, save and restart
promptly. Preserve leading zeros. After successful access the key is cached, so
reopening a view does not require another camera-code request.

Do not disable account security. If a code is rejected, share the FAIL and numeric
API_CODES lines rather than repeatedly requesting or submitting codes.

## Updating a stalled 0.2.0 live preview

Update the existing add-on to **0.2.2**, restart it with the same settings, wait for
`LIVE: ready`, then retry the Generic Camera preview once. Keep the saved session,
camera key and stream credentials. You do not need to request a new email code just
to update; use the documented verification flow only if an explicit challenge occurs.

Version 0.2.0 could buffer video-only encrypted input until stream end because its
upstream decryptor waited for a non-video PES packet. This differs from probe mode,
which flushes at the end of a bounded clip. The new version also reduces FFmpeg
startup buffering and reads available output without waiting for 64 KiB.

If preview still fails, share the new `LIVE_STAGE`, `LIVE_CLOUD`, `LIVE_MEDIA`,
`LIVE_REMUX`, `LIVE_FAIL` and `LIVE_STOP` lines. These contain stage names, aggregate
byte counts, numeric status/exit codes and fixed diagnostic categories only. Raw
FFmpeg messages, URLs, cloud responses, video bytes, keys and tokens are excluded.
`LIVE_STOP` now identifies normal idle shutdown, duration limits, source end and
media timeout instead of only saying that the producer stopped.

## Sleep and wake behavior

The add-on initiates a normal authenticated VTM cloud live-view request when a viewer
connects. It has **no separately implemented or verified DP1C wake-up command**.
Whether that normal request wakes a sleeping DP1C still requires a physical test.
The optional library function to delay battery-device sleep is not used: its device
compatibility and operation parameters have not been verified for this camera.

The user's preview also failed immediately after viewing through the official app,
so sleep has not been established as the cause of the reported preview failure.
A successful probe proves a short capture, not live wake-up behavior. First verify
live playback while awake, then test again after the camera has been idle.

## "Error with media stream contents" in Generic Camera

Continuous `LIVE_MEDIA` byte counters prove media transfer, not successful decoding.
The frontend's HLS player displays this message for a media error; a matching Core
log entry is not guaranteed. A custom card's JavaScript error is separate evidence
and does not establish the cause of the video failure.

For the next attempt, update to **0.2.2**, keep `mode: live`, and select:

```yaml
decrypt_mode: auto
video_mode: compatible
```

Keep the same account, stream credentials and Generic Camera configuration. Restart,
wait for `LIVE: ready`, and retry preview once. This is a compatibility fallback,
not a confirmed diagnosis of the physical camera's media error. It rebuilds video
headers/keyframes rather than directly copying the original bitstream.

After about five seconds of media, the add-on runs bounded local checks and emits:

- `LIVE_CHECK`: codec/dimensions/profile, five-frame decode result, keyframe count,
  and aggregate missing/non-increasing timestamp counts and maximum timestamp gap.
- `LIVE_DECRYPT_CHECK`: whether the incremental plaintext equals the pinned
  library's whole-capture decryption for the same bounded input prefix.
- `LIVE_MEDIA ... ffmpeg_flags=...`: fixed, allowlisted FFmpeg error categories.

Share those result lines if it still fails. The checks run once per producer on
private, bounded in-memory samples, with no retained media files or upload. Raw
FFmpeg errors, media bytes, image/video hashes, URLs and keys are not logged.
They add a small amount of temporary CPU work; compatible mode additionally uses
ongoing CPU for encoding. This version has been tested with synthetic media and
PyAV/fMP4 remuxing locally, not on the user's physical camera yet.

### Decryption formats and picture quality

Earlier versions forced encrypted NAL headers (`header_bytes=0`). This was inferred
from the library's default, not verified against the camera's actual picture. The
reported grey clip means that assumption needs checking. `decrypt_mode: auto` now
uses the pinned library's detector to select clear/encrypted H.264/HEVC headers and
prints `LIVE_DECRYPT_MODE`. It is heuristic detection, not proof of a correct image.
The explicit modes remain available for a later controlled test if needed.

`LIVE_CHECK` explicitly reports `visual_quality: not_assessed`. Five decoded frames
can still be grey, concealed or visually incorrect. Transcoding also cannot repair
incorrectly decrypted source data. The user's visual playback result remains
necessary. A decryption-comparison result of `different` is not an automatic verdict:
our clear-header synthetic fixture reproduces an upstream whole-capture edge case
while the incremental result matches known plaintext and decodes correctly.

### Nonzero cloud stream-start result (including 5405)

A nonzero `LIVE_CLOUD: stream_result` is a refusal from the VTM stream service, even
if a previous metadata API response had `meta_code: 200`. Version 0.2.2 stops before
starting FFmpeg and pauses new producer requests for 120 seconds. The meaning of
5405 is not established by the inspected library; it is not labelled as sleep,
wrong credentials, or a specific session limit.

Close other live viewers and pause before one new attempt. Do not repeatedly
restart or request new verification codes in response to 5405. If it persists,
share the stream-result/start-failure lines; decode checks cannot run without media.
