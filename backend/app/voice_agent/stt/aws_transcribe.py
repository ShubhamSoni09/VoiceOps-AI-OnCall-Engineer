import asyncio
import json
import uuid

import boto3

from app.config import Settings
from app.voice_agent.models import TranscriptionResult
from app.voice_agent.stt.base import SpeechToTextProvider


class AWSTranscribeSTT(SpeechToTextProvider):
    """AWS Transcribe streaming/batch STT."""

    def __init__(self, settings: Settings) -> None:
        self._region = settings.aws_region
        self._language = settings.aws_transcribe_language
        self._s3_bucket = getattr(settings, "aws_s3_bucket", None)

    @property
    def name(self) -> str:
        return "aws_transcribe"

    async def transcribe(self, audio_bytes: bytes, *, filename: str = "audio.wav") -> TranscriptionResult:
        if not self._s3_bucket:
            raise ValueError(
                "AWS Transcribe requires aws_s3_bucket in settings. "
                "Upload audio to S3, run Transcribe job, poll for result."
            )

        job_name = f"voiceops-{uuid.uuid4().hex[:12]}"
        s3_key = f"voiceops/uploads/{job_name}/{filename}"

        text = await asyncio.to_thread(self._run_transcribe_job, audio_bytes, s3_key, job_name)

        return TranscriptionResult(
            text=text,
            language=self._language,
            provider=self.name,
        )

    def _run_transcribe_job(self, audio_bytes: bytes, s3_key: str, job_name: str) -> str:
        s3 = boto3.client("s3", region_name=self._region)
        transcribe = boto3.client("transcribe", region_name=self._region)

        s3.put_object(Bucket=self._s3_bucket, Key=s3_key, Body=audio_bytes)
        media_uri = f"s3://{self._s3_bucket}/{s3_key}"

        transcribe.start_transcription_job(
            TranscriptionJobName=job_name,
            Media={"MediaFileUri": media_uri},
            MediaFormat=s3_key.rsplit(".", 1)[-1],
            LanguageCode=self._language,
        )

        while True:
            job = transcribe.get_transcription_job(TranscriptionJobName=job_name)
            status = job["TranscriptionJob"]["TranscriptionJobStatus"]
            if status in ("COMPLETED", "FAILED"):
                break
            import time

            time.sleep(2)

        if status == "FAILED":
            reason = job["TranscriptionJob"].get("FailureReason", "unknown")
            raise RuntimeError(f"AWS Transcribe failed: {reason}")

        transcript_uri = job["TranscriptionJob"]["Transcript"]["TranscriptFileUri"]
        import urllib.request

        with urllib.request.urlopen(transcript_uri) as resp:
            payload = json.loads(resp.read().decode())

        return payload["results"]["transcripts"][0]["transcript"].strip()
