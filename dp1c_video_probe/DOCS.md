# First live-video test

This is the add-on to test **video**. Keep your existing RenierM26 HACS integration.
It does not need MQTT, a replacement integration, Internet Explorer, or local RTSP.
It is a one-shot experiment, not a working camera entity or permanent video server.

1. Configure your EZVIZ account/password and DP1C serial in this add-on's local
   Configuration tab. Leave the EU API hostname unless your account uses another
   region. Credentials stay on your Home Assistant host.
2. `camera_key` is the device verification/encryption key, not your account password.
   Leave it empty initially: the upstream library attempts to obtain the key through
   your authenticated account. Keep `decrypt_video: true` for the first test.
3. Verification codes have **two different purposes**:
   - `FAIL: stage=login error_class=EzvizAuthVerificationCode`: put the account-login
     code in `verification_code` and retry. Clear it after login succeeds.
   - `FAIL: stage=camera_key error_class=EzvizAuthVerificationCode`: put the newly
     received camera-access code in `camera_verification_code`, leave
     `verification_code` empty, and retry promptly. Preserve any leading zeros.
   - `camera_key` is the permanent device encryption key, **not** an emailed code.
     If you know that key, entering it avoids the cloud key-retrieval request.
   The successful session is saved privately in `/data/session.json` for subsequent
   attempts. Changing account or region starts a new session. Other challenge types
   may require further support. Do not disable account security.
4. Close live view in your phone app, then press **Start** on this add-on. It attempts
   a 10-second live capture and checks for decodable frames. It stops on completion,
   failure, or a two-minute overall timeout. **Stopped is expected.** Do not enable
   Start on boot or Watchdog. There is no continuous stream yet.
5. Share only its `PASS` line or `FAIL: stage=... error_class=...` line, plus any `API_CODES` line. The latter contains only numeric status codes and a fixed operation name. Do not post
   credentials, raw logs from other tools, serial numbers, or video to the public repo.

On success a private clip is available at `/media/ezviz_dp1c_probe/live-test.ts`
(Home Assistant's local media directory). Download/open privately in VLC if useful.
The next test overwrites it. Delete it when finished; recordings may contain visitors
and may be included in backups. No files are uploaded to GitHub by this add-on.

This uses `pyezvizapi==1.0.5.0`'s **experimental** cloud VTM implementation. The
DP1C may need a different protocol or negotiation. A failed test does not establish
that cloud video is impossible. A successful short capture does not establish a
stable continuous stream, good audio, browser codec compatibility, or wake behavior.

The prototype logs only stages and exception class names. This protects credentials
but can leave failures ambiguous; further targeted, locally redacted diagnostics may
be needed. Do not repeatedly retry authentication failures.

## Intended next stage

If the capture works, implement an on-demand video add-on with a protected local
stream endpoint and connection limits. Connect that stream to a Home Assistant
camera entity (for example Generic Camera). Keep the existing HACS integration for
motion and snapshots. A separate HACS integration is only needed if entity setup or
camera-specific controls require it.

## Updating from 0.1.0 or 0.1.1

Refresh the add-on store/repositories and update this add-on to **0.1.2**. The old
version incorrectly sent a code requested during camera-key retrieval to login.
Clear both `verification_code` and `camera_verification_code` first and run the updated probe once. Leave `camera_key` empty unless you know the permanent device encryption key. Version 0.1.2 explicitly requests a code for the DEVICE_ENCRYPTION operation when needed. If it now
asks at `camera_key`, enter the fresh code in `camera_verification_code` and retry.
The earlier generic login `PyEzvizError` does not establish that your password is wrong.

A successful probe prints PASS and writes the private clip to
`/media/ezviz_dp1c_probe/live-test.ts`. Browse **Media → My media** in Home Assistant
(the label can vary) or access the media folder via Samba; download and open in VLC
if the browser cannot play MPEG-TS. There is still no camera entity or live dashboard
view. On failure, no validated clip is available; partial files are removed. A hard
timeout may leave a partial file, which must not be treated as a successful capture.

The email contains a temporary access code, not the camera encryption key. After
`INFO: requested camera-encryption verification code`, enter that **new** email's
code in `camera_verification_code` promptly, leave `verification_code` blank, and
restart once. Do not reuse a code from a previous login or verification attempt.
If it fails again, share the newest PROBE/FAIL/API_CODES lines rather than retrying
repeatedly. We cannot infer whether a generic PyEzvizError means an invalid code,
expired code, unsupported endpoint, or another server failure without those codes.
