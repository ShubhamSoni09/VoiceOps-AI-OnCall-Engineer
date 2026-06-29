from __future__ import annotations

import json
import sys

from app.config import Settings


WARMUP_STAGE_PREFIX = "VOICEOPS_WARMUP_STAGE="


def emit(stage: str, message: str) -> None:
    print(
        f"{WARMUP_STAGE_PREFIX}{json.dumps({'stage': stage, 'message': message})}",
        flush=True,
    )


def main() -> int:
    settings = Settings()
    try:
        if not settings.hf_token:
            raise RuntimeError("HF_TOKEN is required for pyannote diarization warmup")
        emit("importing", "Importing WhisperX and pyannote")
        import whisperx
        from whisperx.diarize import DiarizationPipeline

        emit("loading_asr", f"Loading WhisperX {settings.whisperx_model}")
        whisperx.load_model(
            settings.whisperx_model,
            settings.whisperx_device,
            compute_type=settings.whisperx_compute_type,
        )

        emit("loading_diarization", "Loading pyannote speaker diarization")
        DiarizationPipeline(
            token=settings.hf_token,
            device=settings.whisperx_device,
        )

        emit("completed", "WhisperX and pyannote warmup completed")
        return 0
    except Exception as exc:
        print(str(exc), file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
