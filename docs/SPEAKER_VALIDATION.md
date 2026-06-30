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

## Install Real Verification

Local demo mode uses `SPEAKER_PROVIDER=mock`. Real verification requires WhisperX, pyannote access, and local audio tooling.

1. Install system audio tooling:

```bash
# macOS
brew install ffmpeg

# Ubuntu/Debian
sudo apt-get update && sudo apt-get install -y ffmpeg
```

2. Create a Hugging Face token and accept the pyannote diarization model terms.

3. Configure the backend:

```env
SPEAKER_PROVIDER=whisperx
HF_TOKEN=...
WHISPERX_DEVICE=cpu
WHISPERX_WORKER_MODE=subprocess
SPEAKER_VERIFICATION_TIMEOUT_SECONDS=300
```

4. Restart the backend, then use the right rail `Runtime` panel:

- `Warm` loads the models before a live meeting.
- `Generate sample` runs a generated two-speaker verification.
- `Choose audio` plus `Verify` checks your own sample.

5. Map unknown labels in the Speakers panel after verification.

## Failure States

| State | Meaning | Fix |
| --- | --- | --- |
| `not_verified` | WhisperX is configured but no valid verification evidence exists. | Run `Generate sample` or upload two-speaker audio. |
| `failed` | Verification job crashed or timed out. | Check `HF_TOKEN`, pyannote access, ffmpeg, and increase `SPEAKER_VERIFICATION_TIMEOUT_SECONDS`. |
| `weak` | Audio produced too few speakers or low-quality labels. | Use cleaner two-speaker audio and keep `2+ speakers` enabled. |
| `invalid` | Saved verification evidence is malformed or stale. | Rerun verification from the Runtime panel. |
| `calibration_needed` | Speaker labels exist but are not mapped to teammates. | Use the Speakers panel assignment controls. |

CLI check:

```bash
cd backend
HF_TOKEN=<token> python scripts/demo_operator.py --profile real-mac --run --timeout 300 --json
```
