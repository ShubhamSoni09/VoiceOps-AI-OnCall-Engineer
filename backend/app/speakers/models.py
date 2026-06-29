from datetime import datetime
from enum import Enum

from typing import Any

from pydantic import BaseModel, Field


class MappingSource(str, Enum):
    MANUAL = "manual"
    MOCK = "mock"
    PROFILE = "profile"


class VoiceProfileStatus(str, Enum):
    ACTIVE = "active"
    UNKNOWN = "unknown"


class SpeakerSegment(BaseModel):
    speaker_label: str
    text: str
    start_ms: int | None = None
    end_ms: int | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    identified_user_id: str | None = None
    identified_user_name: str | None = None
    identity_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    identity_source: str | None = None


class SpeakerMapping(BaseModel):
    room_id: str
    speaker_label: str
    user_id: str
    user_name: str
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    source: MappingSource = MappingSource.MANUAL
    created_at: datetime
    updated_at: datetime


class VoiceProfile(BaseModel):
    user_id: str
    display_name: str
    sample_count: int = 0
    status: VoiceProfileStatus = VoiceProfileStatus.ACTIVE
    created_at: datetime
    updated_at: datetime


class SegmentIngestRequest(BaseModel):
    session_id: str = "default"
    source: str = "meeting_audio"
    segments: list[SpeakerSegment]
    metadata: dict[str, Any] = Field(default_factory=dict)


class SegmentIngestResponse(BaseModel):
    room_id: str
    message_count: int
    segments: list[SpeakerSegment]


class SpeakerMappingRequest(BaseModel):
    speaker_label: str
    user_id: str
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    source: MappingSource = MappingSource.MANUAL


class SpeakerRoomState(BaseModel):
    room_id: str
    provider: str
    profiles: list[VoiceProfile]
    mappings: list[SpeakerMapping]
    unknown_speakers: list[SpeakerSegment]


class SpeakerValidationReport(BaseModel):
    room_id: str
    provider: str
    status: str
    ready: bool
    mapped_speaker_count: int = 0
    unknown_speaker_count: int = 0
    low_confidence_unknown_count: int = 0
    observed_speaker_labels: list[str] = Field(default_factory=list)
    mapped_speaker_labels: list[str] = Field(default_factory=list)
    unknown_speaker_labels: list[str] = Field(default_factory=list)
    verification_status: str = "not_required"
    verification_ready: bool = True
    verification_count: int = 0
    verification_labels: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)


class LiveStartEvent(BaseModel):
    session_id: str = "default"
    mime_type: str = "audio/webm"
    sample_rate: int | None = None
    resume_session_id: str | None = None
    last_sequence: int = Field(default=0, ge=0)


class LiveAudioChunkEvent(BaseModel):
    sequence: int
    audio_base64: str | None = None
    mime_type: str | None = None
    debug_text: str | None = None


class LiveStopEvent(BaseModel):
    reason: str | None = None


class LiveProviderResult(BaseModel):
    temp_id: str
    partial_text: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    segments: list[SpeakerSegment] = Field(default_factory=list)
