import json
from functools import lru_cache
from collections.abc import Callable

from app.auth.models import UserPublic
from app.collab.models import MessageRole
from app.collab.service import CollaborationService, get_collaboration_service
from app.config import get_settings
from app.config import Settings
from app.speakers.models import (
    LiveProviderResult,
    MappingSource,
    SegmentIngestRequest,
    SegmentIngestResponse,
    SpeakerMapping,
    SpeakerMappingRequest,
    SpeakerRoomState,
    SpeakerSegment,
    SpeakerValidationReport,
    VoiceProfile,
    VoiceProfileStatus,
)
from app.speakers.provider import MockSpeakerProvider, get_speaker_provider
from app.speakers.store import SpeakerStorePort, create_speaker_store, utc_now

LiveStageCallback = Callable[[str, str | None], None]


class SpeakerService:
    def __init__(
        self,
        store: SpeakerStorePort,
        collab: CollaborationService,
        provider: MockSpeakerProvider,
    ) -> None:
        self._store = store
        self._collab = collab
        self._provider = provider

    @property
    def provider_name(self) -> str:
        return self._provider.name

    def ingest_segments(self, room_id: str, body: SegmentIngestRequest) -> SegmentIngestResponse:
        normalized = self._provider.normalize_segments(body.segments)
        messages = []
        enriched_segments: list[SpeakerSegment] = []
        for segment in normalized:
            if not segment.text:
                continue
            enriched = self._apply_mapping(room_id, segment)
            message = self._collab.add_speaker_message(
                room_id,
                text=enriched.text,
                speaker_label=enriched.speaker_label,
                confidence=enriched.confidence,
                identified_user_id=enriched.identified_user_id,
                identified_user_name=enriched.identified_user_name,
                identity_confidence=enriched.identity_confidence,
                source=body.source,
                metadata={
                    "session_id": body.session_id,
                    "start_ms": enriched.start_ms,
                    "end_ms": enriched.end_ms,
                    "identity_source": enriched.identity_source
                    or self._identity_source(room_id, enriched.speaker_label),
                    **body.metadata,
                },
            )
            messages.append(message)
            enriched_segments.append(enriched)
        return SegmentIngestResponse(
            room_id=room_id,
            message_count=len(messages),
            segments=enriched_segments,
        )

    def map_speaker(
        self,
        room_id: str,
        body: SpeakerMappingRequest,
        *,
        actor: UserPublic | None = None,
    ) -> SpeakerMapping:
        participant = self._collab.get_participant(room_id, body.user_id)
        if participant is None:
            raise ValueError("user_id must reference a participant in the room")

        now = utc_now()
        existing = self._store.get_mapping(room_id, body.speaker_label)
        mapping = SpeakerMapping(
            room_id=room_id,
            speaker_label=body.speaker_label,
            user_id=participant.id,
            user_name=participant.name,
            confidence=body.confidence,
            source=body.source,
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )
        self._store.upsert_mapping(mapping)
        self._store.upsert_profile(
            VoiceProfile(
                user_id=participant.id,
                display_name=participant.name,
                sample_count=0,
                status=VoiceProfileStatus.ACTIVE,
                created_at=self._store.get_profile(participant.id).created_at
                if self._store.get_profile(participant.id)
                else now,
                updated_at=now,
            )
        )
        reattributed = self._collab.reattribute_speaker_history(
            room_id,
            speaker_label=mapping.speaker_label,
            actor_id=participant.id,
            actor_name=participant.name,
            actor_initials=participant.initials,
            identity_confidence=mapping.confidence,
            identity_source=mapping.source.value,
        )
        self._record_mapping_audit(room_id, mapping, existing, actor, reattributed)
        return mapping

    def room_state(self, room_id: str) -> SpeakerRoomState:
        profiles = self._profiles_for_room(room_id)
        mappings = self._store.list_mappings(room_id)
        mapped_labels = {mapping.speaker_label for mapping in mappings}
        unknown: dict[str, SpeakerSegment] = {}
        for message in self._collab.list_messages(room_id, limit=500):
            label = message.speaker_label or message.metadata.get("speaker_label")
            if (
                message.role == MessageRole.USER
                and label
                and label not in mapped_labels
                and not message.metadata.get("identified_user_id")
            ):
                unknown[label] = SpeakerSegment(
                    speaker_label=label,
                    text=message.text,
                    confidence=message.confidence,
                    identified_user_id=None,
                    identified_user_name=None,
                )
        return SpeakerRoomState(
            room_id=room_id,
            provider=self.provider_name,
            profiles=profiles,
            mappings=mappings,
            unknown_speakers=list(unknown.values()),
        )

    def validation_report(self, room_id: str, settings: Settings) -> SpeakerValidationReport:
        state = self.room_state(room_id)
        observed_labels = _observed_speaker_labels(self._collab.list_messages(room_id, limit=500))
        mapped_labels = sorted({mapping.speaker_label for mapping in state.mappings})
        unknown_labels = sorted({speaker.speaker_label for speaker in state.unknown_speakers})
        low_confidence_unknowns = [
            speaker
            for speaker in state.unknown_speakers
            if speaker.confidence is not None and speaker.confidence < 0.7
        ]
        verification = _verification_report(settings, self.provider_name)
        warnings: list[str] = []
        next_steps: list[str] = []
        if unknown_labels:
            warnings.append(f"{len(unknown_labels)} speaker label(s) still need manual mapping.")
            next_steps.append("Assign unknown speaker labels to teammates in the Speakers panel.")
        if low_confidence_unknowns:
            warnings.append(f"{len(low_confidence_unknowns)} unknown label(s) have low confidence and need confirmation.")
            next_steps.append("Confirm low-confidence labels before trusting attribution in handoff.")
        if not verification["ready"]:
            warnings.append(verification["detail"])
            next_steps.append("Run strict 2+ speaker verification before treating real diarization as demo-ready.")

        ready = not warnings
        status = "ready" if ready else "calibration_needed" if unknown_labels else "verification_needed"
        return SpeakerValidationReport(
            room_id=room_id,
            provider=self.provider_name,
            status=status,
            ready=ready,
            mapped_speaker_count=len(mapped_labels),
            unknown_speaker_count=len(unknown_labels),
            low_confidence_unknown_count=len(low_confidence_unknowns),
            observed_speaker_labels=observed_labels,
            mapped_speaker_labels=mapped_labels,
            unknown_speaker_labels=unknown_labels,
            verification_status=verification["status"],
            verification_ready=verification["ready"],
            verification_count=verification["count"],
            verification_labels=verification["labels"],
            warnings=warnings,
            next_steps=next_steps,
        )

    def segment_authenticated_transcript(self, text: str) -> list[SpeakerSegment]:
        return self._provider.segment_transcript(text)

    async def warmup_provider(self, stage_callback: LiveStageCallback | None = None) -> None:
        await self._provider.warmup(stage_callback=stage_callback)

    async def process_live_chunk(
        self,
        room_id: str,
        *,
        session_id: str,
        sequence: int,
        audio_bytes: bytes,
        mime_type: str,
        debug_text: str | None = None,
        stage_callback: LiveStageCallback | None = None,
    ) -> LiveProviderResult:
        result = (
            _debug_live_result(
                session_id=session_id,
                sequence=sequence,
                text=debug_text,
                stage_callback=stage_callback,
            )
            if debug_text
            else await self._provider.process_live_chunk(
                audio_bytes,
                session_id=session_id,
                sequence=sequence,
                mime_type=mime_type,
                stage_callback=stage_callback,
            )
        )
        if result.segments:
            ingest = self.ingest_segments(
                room_id,
                SegmentIngestRequest(
                    session_id=session_id,
                    source="live_audio",
                    segments=result.segments,
                    metadata={
                        "live_temp_id": result.temp_id,
                        "live_sequence": sequence,
                    },
                ),
            )
            result = result.model_copy(update={"segments": ingest.segments})
        return result

    def _apply_mapping(self, room_id: str, segment: SpeakerSegment) -> SpeakerSegment:
        mapping = self._store.get_mapping(room_id, segment.speaker_label)
        if mapping is None:
            return segment.model_copy(
                update={
                    "identity_source": segment.identity_source or MappingSource.MOCK.value,
                }
            )
        return segment.model_copy(
            update={
                "identified_user_id": mapping.user_id,
                "identified_user_name": mapping.user_name,
                "identity_confidence": mapping.confidence,
                "identity_source": mapping.source.value,
            }
        )

    def _identity_source(self, room_id: str, speaker_label: str) -> str:
        mapping = self._store.get_mapping(room_id, speaker_label)
        return mapping.source.value if mapping else MappingSource.MOCK.value

    def _record_mapping_audit(
        self,
        room_id: str,
        mapping: SpeakerMapping,
        previous: SpeakerMapping | None,
        actor: UserPublic | None,
        reattributed: dict[str, int] | None = None,
    ) -> None:
        actor_name = actor.name if actor else "A teammate"
        if previous and previous.user_id != mapping.user_id:
            text = (
                f"{actor_name} corrected {mapping.speaker_label} "
                f"from {previous.user_name} to {mapping.user_name}."
            )
            event = "speaker_mapping_corrected"
        elif previous:
            text = f"{actor_name} confirmed {mapping.speaker_label} as {mapping.user_name}."
            event = "speaker_mapping_confirmed"
        else:
            text = f"{actor_name} mapped {mapping.speaker_label} to {mapping.user_name}."
            event = "speaker_mapping_created"

        self._collab.add_system_message(
            room_id,
            text,
            metadata={
                "source": "speaker_mapping",
                "event": event,
                "speaker_label": mapping.speaker_label,
                "mapped_user_id": mapping.user_id,
                "mapped_user_name": mapping.user_name,
                "previous_user_id": previous.user_id if previous else None,
                "previous_user_name": previous.user_name if previous else None,
                "mapping_source": mapping.source.value,
                "confidence": mapping.confidence,
                "actor_id": actor.id if actor else None,
                "actor_name": actor_name,
                "reattributed_messages": (reattributed or {}).get("messages", 0),
                "reattributed_memory": (reattributed or {}).get("memory", 0),
            },
        )

    def _profiles_for_room(self, room_id: str) -> list[VoiceProfile]:
        now = utc_now()
        profiles: dict[str, VoiceProfile] = {}
        for participant in self._collab.list_participants(room_id):
            participant_kind = (
                participant.kind.value if hasattr(participant.kind, "value") else str(participant.kind)
            )
            if participant_kind != "human":
                continue
            stored_profile = self._store.get_profile(participant.id)
            profiles.setdefault(
                participant.id,
                stored_profile or VoiceProfile(
                    user_id=participant.id,
                    display_name=participant.name,
                    sample_count=0,
                    status=VoiceProfileStatus.ACTIVE,
                    created_at=participant.joined_at or now,
                    updated_at=participant.last_seen_at or now,
                ),
            )
        return list(profiles.values())


def _debug_live_result(
    *,
    session_id: str,
    sequence: int,
    text: str,
    stage_callback: LiveStageCallback | None = None,
) -> LiveProviderResult:
    clean_text = text.strip()
    start_ms = max(sequence - 1, 0) * 2000
    end_ms = start_ms + 1800
    segment = SpeakerSegment(
        speaker_label=f"SPEAKER_{sequence % 2:02d}",
        text=clean_text,
        start_ms=start_ms,
        end_ms=end_ms,
        confidence=0.72,
    )
    return LiveProviderResult(
        temp_id=f"{session_id}-{sequence}",
        partial_text=clean_text,
        start_ms=start_ms,
        end_ms=end_ms,
        segments=[segment],
    )


def _observed_speaker_labels(messages) -> list[str]:
    labels = {
        str(message.speaker_label or message.metadata.get("speaker_label"))
        for message in messages
        if message.speaker_label or message.metadata.get("speaker_label")
    }
    return sorted(label for label in labels if label and label != "None")


def _verification_report(settings: Settings, provider_name: str) -> dict:
    if provider_name != "whisperx":
        return {"status": "not_required", "ready": True, "count": 0, "labels": [], "detail": ""}
    path = settings.speaker_verification_path.expanduser()
    if not path.exists():
        return {
            "status": "not_verified",
            "ready": False,
            "count": 0,
            "labels": [],
            "detail": "No real diarization verification report has been recorded.",
        }
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "status": "invalid",
            "ready": False,
            "count": 0,
            "labels": [],
            "detail": "Speaker verification report is unreadable.",
        }
    labels = raw.get("speaker_labels") if isinstance(raw.get("speaker_labels"), list) else []
    count = int(raw.get("distinct_speaker_count") or len(labels) or 0)
    verified = bool(raw.get("verified")) and count >= 2
    status = "verified" if verified else "weak" if raw.get("verified") else "failed" if raw.get("error") else "not_verified"
    detail = str(raw.get("detail") or raw.get("error") or "")
    if not detail:
        detail = "Strict 2+ speaker verification passed." if verified else "Strict 2+ speaker verification has not passed."
    return {
        "status": status,
        "ready": verified,
        "count": count,
        "labels": [str(label) for label in labels[:8]],
        "detail": detail,
    }


@lru_cache
def get_speaker_service() -> SpeakerService:
    settings = get_settings()
    return SpeakerService(
        create_speaker_store(settings),
        get_collaboration_service(),
        get_speaker_provider(settings.speaker_provider, settings),
    )
