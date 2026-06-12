import asyncio
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from app.config import Settings
from app.voice_agent.models import TranscriptionResult
from app.voice_agent.stt.base import SpeechToTextProvider

SAMPLE_RATE = 16000


def _get_ffmpeg_exe() -> str:
    import shutil

    if path := shutil.which("ffmpeg"):
        return path
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError as exc:
        raise RuntimeError(
            "ffmpeg is required for speech-to-text. Install ffmpeg or run: pip install imageio-ffmpeg"
        ) from exc


def _load_audio(file_path: str, sr: int = SAMPLE_RATE) -> np.ndarray:
    """Decode audio file to float32 mono waveform (Whisper-compatible)."""
    ffmpeg = _get_ffmpeg_exe()
    cmd = [
        ffmpeg,
        "-nostdin",
        "-threads",
        "0",
        "-i",
        file_path,
        "-f",
        "s16le",
        "-ac",
        "1",
        "-acodec",
        "pcm_s16le",
        "-ar",
        str(sr),
        "-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, check=True)
    except FileNotFoundError as exc:
        raise RuntimeError(
            "ffmpeg not found. Run: pip install imageio-ffmpeg"
        ) from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode(errors="replace")
        raise RuntimeError(f"ffmpeg failed to decode audio: {stderr}") from exc

    audio = np.frombuffer(proc.stdout, np.int16).flatten().astype(np.float32) / 32768.0
    return audio


class WhisperSTT(SpeechToTextProvider):
    """Local Whisper-based speech-to-text."""

    def __init__(self, settings: Settings) -> None:
        self._model_name = settings.whisper_model
        self._model = None

    @property
    def name(self) -> str:
        return "whisper"

    def _load_model(self):
        if self._model is None:
            import whisper

            self._model = whisper.load_model(self._model_name)
        return self._model

    def _transcribe_file(self, tmp_path: str) -> dict:
        model = self._load_model()
        audio = _load_audio(tmp_path)
        return model.transcribe(
            audio,
            language="en",
            fp16=False,
            beam_size=1,
            best_of=1,
            condition_on_previous_text=False,
        )

    def preload(self) -> None:
        self._load_model()

    async def transcribe(self, audio_bytes: bytes, *, filename: str = "audio.wav") -> TranscriptionResult:
        suffix = Path(filename).suffix or ".wav"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        try:
            result = await asyncio.to_thread(self._transcribe_file, tmp_path)
            return TranscriptionResult(
                text=result["text"].strip(),
                language=result.get("language"),
                provider=self.name,
            )
        finally:
            Path(tmp_path).unlink(missing_ok=True)
