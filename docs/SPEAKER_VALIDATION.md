# Speaker Validation

Speaker validation is the room-level readiness layer for multi-person meeting attribution. It does not store raw audio and does not claim biometric identity. It reports whether the current room has unknown labels, low-confidence attribution, manual mappings, and real WhisperX verification evidence.

## Backend Contract

`GET /speakers/rooms/{room_id}/validation`

Returns:

- `ready`: true only when no unknown labels remain and provider verification is ready.
- `status`: `ready`, `calibration_needed`, or `verification_needed`.
- `observed_speaker_labels`: all labels seen in room timeline metadata.
- `mapped_speaker_labels`: labels mapped to participants.
- `unknown_speaker_labels`: labels still needing manual assignment.
- `low_confidence_unknown_count`: unknown labels below the confidence threshold.
- `verification_status`: `not_required`, `not_verified`, `verified`, `weak`, `failed`, or `invalid`.
- `verification_count` and `verification_labels`: summary of persisted real-diarization verification evidence.
- `warnings` and `next_steps`: human-readable actions for the UI.

## Provider Behavior

- `mock`: verification is `not_required`; readiness depends on room speaker mapping state.
- `whisperx`: readiness requires a valid verification report with at least two distinct speaker labels.

The verification report is read from `SPEAKER_VERIFICATION_PATH`, which is produced by the existing system speaker verification and demo readiness scripts.

## Frontend Behavior

The React Speakers block fetches room state and validation together. It shows:

- provider name,
- ready/map/verify status,
- mapped labels,
- unknown labels with assignment controls,
- the next required calibration step.

Manual mapping remains the source of truth for teammate identity in v1. Historical raw speaker labels stay auditable in message metadata.
