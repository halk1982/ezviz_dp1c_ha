# 0.2.1

- Fix a reproduced live-video stall for encrypted video-only MPEG-PS input: release complete NAL runs before stream end while preserving PES/AES continuations.
- Reduce FFmpeg startup buffering and forward available TS output immediately.
- Report cloud connection stages, aggregate media byte counts, safe FFmpeg error categories, process exit codes and explicit stop reasons.
- Preserve existing sessions, camera-key caches, credentials and live-mode configuration.
- Add a full encrypted H.264 producer-to-HTTP-to-decoder regression test. No new wake-up command is claimed.

# 0.2.0

- Add `mode: live` with an authenticated `/live.ts` endpoint for Generic Camera.
- Share one on-demand cloud producer across up to four consumers; close it when idle.
- Bound media buffering, connection duration and authentication retries.
- Cache the resolved camera key privately to avoid repeated email-code requests.
- Preserve `mode: probe` and the existing add-on slug/configuration.
- Live output is video only. Physical-camera sustained playback remains experimental.

# 0.1.2

- Explicitly request the DEVICE_ENCRYPTION verification challenge when camera-key access requires it and no code is supplied.
- Preserve the session after a challenge request; do not request a new code while submitting an existing code.
- Add allowlisted numeric API status diagnostics without exposing response bodies or credentials.

# 0.1.1

- Separate account-login verification (`verification_code`) from camera-key access verification (`camera_verification_code`).
- Preserve the successful account session between attempts in a private token file.
- Identify camera-key retrieval separately from cloud stream capture in logs.
- Add safe, stage-specific guidance and remove partial captures after failures.
- Clarify that the probe creates a test clip, not a Home Assistant camera entity.

# 0.1.0

- Initial experimental cloud video capture and decoder test.
