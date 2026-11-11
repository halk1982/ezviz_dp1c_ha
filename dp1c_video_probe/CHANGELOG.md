# 0.2.4

- Add authenticated `/snapshot.jpg` for Generic Camera's still-image URL and HomeKit thumbnail testing. Reuse the existing cloud producer, cache JPEGs in RAM for 30 seconds and share concurrent captures.
- Bound capture time, input and JPEG size; release snapshot consumers/decoder processes after success or failure. Expired images are not returned on failure.
- Check streaming imports, FFmpeg and FFprobe at add-on startup; give unauthenticated clients a Basic-authentication hint without exposing configured credentials.
- Document the snapshot battery tradeoff, advanced options and physical-camera/HomeKit test steps. Preserve existing options, credentials and add-on slug.
- Add an MIT license for original project code. Dependencies retain their respective licenses.
- Snapshot behavior has local synthetic coverage; physical-camera/HomeKit validation is pending. No stable GitHub Release is claimed.

# 0.2.3

- Fix the confirmed DP1C grey-picture cause: live video was already clear and local decryption damaged it. Default to `decrypt_video: false`.
- Default to user-confirmed `video_mode: compatible` for new configurations; preserve existing settings.
- Skip camera-key requests when decryption is off, even when an old key cache exists. A saved key cannot override the current flag.
- Explicitly label health checks as encoded-output checks and include observed pipeline errors alongside their result.
- Add clear-stream passthrough/incorrect-decryption regression coverage and update the known-working setup guide.

# 0.2.2

- Select NAL header decryption mode automatically instead of forcing encrypted headers; log the selected header size. Explicit modes remain available.
- Fail immediately on nonzero VTM stream-start results and apply a 120-second producer cooldown; do not assign an unverified meaning to 5405.

- Add optional `video_mode: compatible`: Baseline H.264 at 10 fps with regular keyframes, repeated headers, no B-frames and a rebased output timeline. Default remains `copy`.
- Once per producer, check a bounded local sample for five decodable frames, codec/profile, keyframes and timestamp anomalies.
- Compare incremental decryption with whole-capture decryption of the same private input prefix.
- Show fixed FFmpeg error categories during streaming; exclude raw responses, metadata, URLs, credentials and media from logs.
- Add a targeted PyAV/fMP4 compatibility test for both output modes. Physical-camera media error remains under investigation.

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
