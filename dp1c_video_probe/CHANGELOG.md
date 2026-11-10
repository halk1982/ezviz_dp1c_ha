# 0.1.1

- Separate account-login verification (`verification_code`) from camera-key access verification (`camera_verification_code`).
- Preserve the successful account session between attempts in a private token file.
- Identify camera-key retrieval separately from cloud stream capture in logs.
- Add safe, stage-specific guidance and remove partial captures after failures.
- Clarify that the probe creates a test clip, not a Home Assistant camera entity.

# 0.1.0

- Initial experimental cloud video capture and decoder test.
