# EZVIZ DP1C Cloud Video

The user confirmed a **normal live picture** in Generic Camera using this configuration:

```yaml
mode: live
decrypt_video: false
video_mode: compatible
```

The DP1C cloud feed tested here was already clear. Applying the add-on's decryption
pass damaged its H.264 data, producing grey video and decoder errors. An encrypted
alarm image does not establish that live video also needs decryption.

Version **0.2.3** defaults to decryption off and compatible encoding. Existing
installations retain their settings: if upgrading from an earlier version with
`decrypt_video: true`, explicitly change it to `false`. The slug remains
`ezviz_dp1c_video_probe`; update the existing add-on to retain its configuration.

## Set up Home Assistant OS

1. Keep the working RenierM26 HACS integration for motion and alarm images.
2. Configure this add-on with your EZVIZ account/password, serial, and API region.
   The default EU API host is `apiieu.ezvizlife.com`. Enter secrets locally only.
3. Set the three values shown above. Set `stream_username: dp1c` and choose a
   separate `stream_password` of 16–128 letters/digits/underscore/hyphen. This local
   stream password is different from your EZVIZ account password.
4. Leave `verification_code`, `camera_verification_code` and `camera_key` empty for
   normal setup unless an explicit account-login challenge requires a login code.
   Decryption off skips camera-key retrieval and its device-encryption email flow.
5. Save and Start. Wait for `LIVE: ready on port 8558`.
6. Add **Generic Camera** under Settings → Devices & services:

   | Field | Value |
   |---|---|
   | Stream source | `http://HOME_ASSISTANT_LAN_IP:8558/live.ts` |
   | Still image URL (0.2.4+) | `http://HOME_ASSISTANT_LAN_IP:8558/snapshot.jpg` |
   | Authentication | Basic |
   | Username | Your `stream_username` |
   | Password | Your `stream_password` |

   Use your HA machine's LAN IP and the mapped port in the add-on Network section.
   On 0.2.3, leave Still image URL blank; that version has no JPEG endpoint.
   This version has no Ingress/Open Web UI player.
7. Once the preview shows a normal picture, check **Everything looks good** and
   press **Submit**. Add the resulting camera entity to your dashboard.

For an existing Generic Camera entity, open its configuration/reconfiguration and
set the still-image URL above, retaining the stream source and Basic credentials.
Disable **Use stream for stills** if that option is shown, so still requests use
the JPEG endpoint instead of extracting images from the HA video stream.

The endpoint uses HTTP Basic authentication on your trusted LAN. Do not port-forward
it to the Internet. View remotely through Home Assistant's existing secured access.

## Runtime behavior

- Compatible mode sends Baseline H.264 at 10 fps, with regular keyframes and repeated
  headers. It uses CPU for decoding/re-encoding. It is the user-confirmed picture
  configuration. `video_mode: copy` is an optional lower-CPU mode tested locally;
  it has not yet been confirmed on this physical DP1C with decryption off.
- Video only; audio and two-way talk are not implemented.
- One cloud producer is shared by up to four consumers. It starts when a viewer
  connects and closes about ten seconds after the last disconnect is detected.
- **Keep Preload stream disabled.** HA may retain a stream briefly after closing
  a view. Live dashboard cards, thumbnail refreshes or recording may hold/reopen it.
- A producer runs for at most `max_view_seconds` (default 300, range 30–900).
  Clients can reconnect afterward. This is intended for short views, not continuous
  recording. Physical wake-from-sleep behavior and long-term stability remain unverified.
- There is no separately verified DP1C wake-up command. A view initiates a normal
  VTM cloud live-view request. No sleep/privacy settings are changed by the add-on.
- Media timeout is 45 seconds. Errors cause a cooldown; a nonzero cloud start result
  causes a 120-second cooldown. Authentication challenges block producers until restart.
- Only amd64 container builds were validated here. aarch64 is declared but not built here.

## Still images and HomeKit (0.2.4+)

`/snapshot.jpg` returns a JPEG from the current video feed, using the same Basic
authentication as `/live.ts`. There are no new configuration fields. Requests
share an existing cloud producer; when no video is running, a request starts one
briefly. It stops after the normal idle timeout once the snapshot consumer leaves.

- The last successful JPEG is cached **in memory for 30 seconds**. Concurrent
  requests share one capture. Images are not saved to `/media`, `/data` or logs.
- After the cache expires, the next request captures a new frame. It does not
  silently return an old image if the camera is unavailable. Restart clears the cache.
- A cold capture can take several seconds and times out after about 25 seconds.
  Busy streams, cloud cooldowns, sleeping/unresponsive cameras or failed decoding
  return HTTP 503. Client/HomeKit timeouts can be shorter than this limit.
- Frequent HomeKit/dashboard thumbnail requests can start live video repeatedly
  and use battery. The cache limits repeated captures within each 30-second window;
  it does not make frequent thumbnail polling free. Keep Preload stream disabled.
- This image is separate from the **last alarm image** supplied by your existing
  HACS integration. Live-video decryption settings apply; an encrypted alarm image
  is not a reason to enable live-video decryption.

Keep the same Generic Camera entity exposed through **HomeKit Bridge**. The bridge
should obtain still images through that entity; no add-on URL or password needs to
be entered in Apple Home. This change does not add a doorbell event or two-way audio:
keep your existing doorbell trigger configuration.

Test on your installation before relying on it:

1. Confirm the Generic Camera still preview shows a normal picture.
2. Open Apple Home and check its thumbnail with live viewing closed.
3. Change the scene, wait more than 30 seconds, then request another still and check
   that it updates. Apple Home can retain its own thumbnail until it requests a new one.
4. Press the doorbell and check the notification image and existing event behavior.
5. Close all views and confirm `LIVE_STOP: reason=no_viewers` appears; watch battery
   use over normal daily thumbnail refreshes.

These Home Assistant, HomeKit, sleep/wake and battery checks need a physical camera.
Local synthetic JPEG tests do not establish that they pass.

## Everyday and advanced options

For the confirmed DP1C setup, use `mode: live`, `video_mode: compatible`,
`decrypt_video: false`, your account/password/serial/region and separate stream
credentials. `max_view_seconds` bounds each producer's lifetime.

Keep the remaining options for troubleshooting and other feeds rather than deleting
them from existing installations: `mode: probe` is a diagnostic capture, `video_mode:
copy` avoids re-encoding, and `decrypt_mode`, `camera_key` and
`camera_verification_code` apply only when decryption is explicitly enabled.
`verification_code` is needed only for an account-login challenge. Do not populate
those fields for a normal clear-video setup.

The HTTP server listens on `0.0.0.0` **inside the add-on container** so the mapped
port can reach it. Binding it to container localhost would prevent Generic Camera
from using that published port. Limit access using your LAN/firewall and Supervisor
port settings; a container-localhost toggle is not a useful default here.

## Troubleshooting and logs

For grey video on versions through 0.2.2, first check `decrypt_video: false` and
restart. Save alone is not sufficient to apply an existing server's configuration.
`LIVE_DECRYPT_MODE: mode=disabled` confirms the local decryption pass is disabled.
This does not disable or change any security setting on the camera or EZVIZ account.

If playback still fails, share only the recent `LIVE_DECRYPT_MODE`, `LIVE_CHECK`,
`LIVE_MEDIA`, `LIVE_FAIL` and `LIVE_STOP` lines. These exclude raw media, responses,
URLs and credentials. Do not share your configuration, tokens or camera key.

`LIVE_CHECK` is explicitly scoped to **encoded output**. Five returned frames and
valid timestamps do not establish a correct picture. When compatible encoding
conceals damaged input into grey frames, its output can pass a decode check while
`pipeline_error_flags` still report PPS/slice/macroblock errors. Visual verification
remains necessary. The summary includes both the output result and observed pipeline
errors so these are not confused.

A nonzero `LIVE_CLOUD: stream_result` means the VTM service rejected stream start,
even if a preceding metadata request had `meta_code: 200`. The specific meaning of
5405 is not established by the inspected library. Close other viewers and pause
before one new attempt; do not repeatedly restart or request new email codes for it.

## Probe mode and advanced decryption

`mode: probe` makes one bounded ten-second capture, verifies decoding, then stops.
On success it writes `/media/ezviz_dp1c_probe/live-test.ts`. Inspect the picture
privately in VLC or through your media share; a decoder PASS is not a visual-quality
test. The next probe replaces the old clip. Keep footage private and delete it when done.

Only enable `decrypt_video: true` if there is evidence that a particular feed needs
it. `decrypt_mode` selects `auto`, `encrypted-header`, `h264-clear-header` or `hevc`.
Automatic detection identifies NAL-header layout; it cannot prove that the video
body is encrypted. It must not be used as justification to decrypt an already-clear
feed. Encrypted modes retain bounded buffering and the pinned library's helpers.

| Field | Purpose |
|---|---|
| `verification_code` | Temporary account **login** challenge code only. |
| `camera_verification_code` | Temporary **[device encryption]** email code, only for camera-key retrieval when decryption is enabled. |
| `camera_key` | Permanent camera encryption key, never an emailed one-time code. |
| `stream_password` | Your separate password for the local video endpoint. |

Explicit camera-key challenges request DEVICE_ENCRYPTION, separate from account
login. Preserve leading zeros, use the field named in the hint, and do not disable
account security. With live decryption off, the camera-key fields and key cache are
not used. Cached sessions/keys remain private in `/data` and may be included in
add-on backups; keep backups private.

## Validation boundaries

The owner reported normal physical-DP1C live picture with decryption disabled and
compatible mode. Local tests use synthetic media: clear/encrypted data, decode,
timestamps, HTTP authentication, stream sharing and PyAV/fMP4 compatibility. They
cannot establish every firmware/account behavior, visually correct scenes, prolonged
playback, wake behavior, audio or unseen network conditions.
JPEG extraction and its short cache are tested with synthetic video; HomeKit
thumbnail/notification behavior has not yet been validated on the physical camera.
