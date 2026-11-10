# Configuration and first camera test

Enter secrets only in the local add-on Configuration tab. Do not share a raw HAR,
API response, token file, image URL, screenshot of credentials, or options file.

## Verify one feature at a time

1. Start the add-on with `motion_codes: []` and `doorbell_codes: []`. Wait for the
   first successful poll. Old messages are suppressed, so startup should not trigger
   an automation. A pre-existing alarm picture may appear in Snapshot.
2. Press the physical doorbell once. Wait at least two polling intervals. Look for
   `New cloud event: category=unknown code=...` in the add-on log. Note the code and
   whether the EZVIZ app itself recorded a corresponding event.
3. Wait until clear, then cause a motion event without pressing the button. Repeat
   several times and compare the codes. If consistently distinct, enter the observed
   codes in `doorbell_codes` and `motion_codes` as quoted strings, then restart.
4. Confirm that Activity now reports `doorbell` and `motion` correctly. If identical
   codes are emitted, keep them unknown; a richer message-field investigation is needed.
5. Confirm that Snapshot changes after a real event. It can be older than the latest
   Activity event if the cloud event has no image.

Safe feedback to share: Home Assistant version, whether the built-in EZVIZ integration
finds the DP1C, whether the phone app records each event, event code values with labels
("pressed button", "walked past"), and the add-on's redacted error class.

## Troubleshooting

- No MQTT device: check broker, MQTT integration, username/password and port. Defaults
  assume the local Mosquitto add-on (`core-mosquitto:1883`). TLS is optional and verifies
  against system CA roots; a private CA is not currently configurable.
- No events: check camera serial, account region and whether events appear in the
  EZVIZ app. Empty cloud results cannot establish camera support. This version accepts
  numeric Unix-second or Unix-millisecond timestamps only.
- Authentication requires attention: use any verification code EZVIZ sent in
  `verification_code`, restart, then clear it after successful login. Challenge types
  beyond the library's supported SMS-code flow may need additional implementation;
  never disable account security to force a login. If a stale session remains a problem,
  stop the add-on and remove its private `/data/token.json` before restarting.
- Snapshot unavailable: confirm `image_key` locally. If a verified image CDN is outside
  `image_hosts`, add that hostname. Do not allow arbitrary hosts. This version deliberately
  does not log signed URLs; determining a new CDN may require local inspection.
- Uninstall: remove the MQTT device and any retained `ezviz_dp1c_<id>/image`, `/code`,
  `/availability` and related discovery topics from your broker if desired. The add-on
  itself has no remote uninstall hook to erase retained MQTT data.

This prototype has not been installed under Supervisor or tested with a real DP1C yet.
