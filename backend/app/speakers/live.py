import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

_TRACKING_HISTORY_LIMIT = 512


@dataclass
class AudioChunk:
    sequence: int
    data: bytes
    mime_type: str


@dataclass
class PendingLiveChunk:
    sequence: int
    data: bytes
    mime_type: str
    debug_text: str | None = None

    @property
    def size_bytes(self) -> int:
        return len(self.data or b"")


@dataclass
class RollingAudioBuffer:
    chunk_seconds: float
    window_seconds: float
    _chunks: list[AudioChunk] = field(default_factory=list)

    def add(self, chunk: AudioChunk) -> bytes:
        self._chunks.append(chunk)
        max_chunks = max(1, int(self.window_seconds / self.chunk_seconds))
        if len(self._chunks) > max_chunks:
            self._chunks = self._chunks[-max_chunks:]
        return self.window_bytes()

    def window_bytes(self) -> bytes:
        return b"".join(chunk.data for chunk in self._chunks)

    @property
    def latest_sequence(self) -> int:
        return self._chunks[-1].sequence if self._chunks else 0


class LiveChunkRejected(ValueError):
    def __init__(
        self,
        *,
        reason: str,
        sequence: int,
        message: str,
        size_bytes: int,
        stats: dict[str, Any],
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.sequence = sequence
        self.message = message
        self.size_bytes = size_bytes
        self.stats = stats


@dataclass
class LiveMeetingSession:
    room_id: str
    session_id: str
    mime_type: str
    buffer: RollingAudioBuffer
    max_chunk_bytes: int
    next_sequence: int = 1
    processing_task: Any | None = None
    pending_chunk: PendingLiveChunk | None = None
    trace_id: str = field(default_factory=lambda: f"live-{uuid4().hex[:12]}")
    accepted_chunks: int = 0
    processed_chunks: int = 0
    total_audio_bytes: int = 0
    latest_window_bytes: int = 0
    last_chunk_size_bytes: int = 0
    last_processing_ms: int = 0
    max_processing_ms: int = 0
    skipped_chunks: int = 0
    queued_chunks: int = 0
    replaced_pending_chunks: int = 0
    dropped_chunks: int = 0
    duplicate_chunks: int = 0
    out_of_order_chunks: int = 0
    empty_chunks: int = 0
    oversized_chunks: int = 0
    gap_chunks: int = 0
    reconnect_count: int = 0
    stop_requested: bool = False
    last_seen_at: float = field(default_factory=time.monotonic)
    duplicate_partials: int = 0
    duplicate_results: int = 0
    latest_accepted_sequence: int = 0
    seen_sequences: set[int] = field(default_factory=set)
    emitted_partial_keys: set[str] = field(default_factory=set)
    emitted_segment_keys: set[str] = field(default_factory=set)
    emitted_partial_key_order: list[str] = field(default_factory=list)
    emitted_segment_key_order: list[str] = field(default_factory=list)

    def add_chunk(
        self,
        data: bytes,
        *,
        sequence: int | None = None,
        mime_type: str | None = None,
    ) -> tuple[int, bytes, dict[str, Any]]:
        resolved_sequence = self.next_sequence if sequence is None else sequence
        self.touch()
        size_bytes = len(data or b"")
        if size_bytes <= 0:
            self.empty_chunks += 1
            self.dropped_chunks += 1
            self.next_sequence = max(self.next_sequence, resolved_sequence + 1)
            self._reject(
                "empty_chunk",
                resolved_sequence,
                "Empty audio chunk ignored",
                size_bytes,
            )
        if size_bytes > self.max_chunk_bytes:
            self.oversized_chunks += 1
            self.dropped_chunks += 1
            self.next_sequence = max(self.next_sequence, resolved_sequence + 1)
            self._reject(
                "oversized_chunk",
                resolved_sequence,
                "Audio chunk is too large; ignored",
                size_bytes,
            )
        if resolved_sequence in self.seen_sequences:
            self.duplicate_chunks += 1
            self.dropped_chunks += 1
            self.next_sequence = max(self.next_sequence, resolved_sequence + 1)
            self._reject(
                "duplicate_chunk",
                resolved_sequence,
                "Duplicate audio chunk ignored",
                size_bytes,
            )
        if self.latest_accepted_sequence and resolved_sequence == self.latest_accepted_sequence:
            self.duplicate_chunks += 1
            self.dropped_chunks += 1
            self.next_sequence = max(self.next_sequence, resolved_sequence + 1)
            self._reject(
                "duplicate_chunk",
                resolved_sequence,
                "Duplicate audio chunk ignored",
                size_bytes,
            )
        if self.latest_accepted_sequence and resolved_sequence < self.latest_accepted_sequence:
            self.out_of_order_chunks += 1
            self.dropped_chunks += 1
            self._reject(
                "out_of_order_chunk",
                resolved_sequence,
                "Out-of-order audio chunk ignored",
                size_bytes,
            )
        if self.latest_accepted_sequence and resolved_sequence > self.latest_accepted_sequence + 1:
            self.gap_chunks += resolved_sequence - self.latest_accepted_sequence - 1

        self.next_sequence = max(self.next_sequence, resolved_sequence + 1)
        self.seen_sequences.add(resolved_sequence)
        self.latest_accepted_sequence = resolved_sequence
        self._trim_seen_sequences()
        window = self.buffer.add(
            AudioChunk(
                sequence=resolved_sequence,
                data=data,
                mime_type=mime_type or self.mime_type,
            )
        )
        self.accepted_chunks += 1
        self.total_audio_bytes += size_bytes
        self.last_chunk_size_bytes = size_bytes
        self.latest_window_bytes = len(window)
        return resolved_sequence, window, self.stats()

    def mark_processed(self, elapsed_ms: int) -> None:
        self.processed_chunks += 1
        self.last_processing_ms = max(0, int(elapsed_ms))
        self.max_processing_ms = max(self.max_processing_ms, self.last_processing_ms)

    def mark_reconnected(self, *, last_sequence: int) -> None:
        self.reconnect_count += 1
        self.stop_requested = False
        self.touch()
        self.next_sequence = max(self.next_sequence, last_sequence + 1, 1)
        self.latest_accepted_sequence = max(self.latest_accepted_sequence, last_sequence)

    def mark_stopped(self) -> None:
        self.stop_requested = True
        self.touch()

    def touch(self) -> None:
        self.last_seen_at = time.monotonic()

    def should_emit_partial(self, *, temp_id: str, text: str | None, source: str) -> bool:
        key = f"{temp_id}:{source}:{text or ''}"
        if key in self.emitted_partial_keys:
            self.duplicate_partials += 1
            return False
        self.emitted_partial_keys.add(key)
        self.emitted_partial_key_order.append(key)
        self._trim_keys(self.emitted_partial_keys, self.emitted_partial_key_order)
        return True

    def should_emit_segments(self, *, temp_id: str, segment_payloads: list[dict[str, Any]]) -> bool:
        key = f"{temp_id}:{segment_payloads!r}"
        if key in self.emitted_segment_keys:
            self.duplicate_results += 1
            return False
        self.emitted_segment_keys.add(key)
        self.emitted_segment_key_order.append(key)
        self._trim_keys(self.emitted_segment_keys, self.emitted_segment_key_order)
        return True

    def stats(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "session_id": self.session_id,
            "accepted_chunks": self.accepted_chunks,
            "processed_chunks": self.processed_chunks,
            "total_audio_bytes": self.total_audio_bytes,
            "latest_window_bytes": self.latest_window_bytes,
            "last_chunk_size_bytes": self.last_chunk_size_bytes,
            "last_processing_ms": self.last_processing_ms,
            "max_processing_ms": self.max_processing_ms,
            "skipped_chunks": self.skipped_chunks,
            "queued_chunks": self.queued_chunks,
            "pending_chunks": 1 if self.pending_chunk else 0,
            "replaced_pending_chunks": self.replaced_pending_chunks,
            "dropped_chunks": self.dropped_chunks,
            "duplicate_chunks": self.duplicate_chunks,
            "out_of_order_chunks": self.out_of_order_chunks,
            "empty_chunks": self.empty_chunks,
            "oversized_chunks": self.oversized_chunks,
            "gap_chunks": self.gap_chunks,
            "latest_accepted_sequence": self.latest_accepted_sequence,
            "next_sequence": self.next_sequence,
            "reconnect_count": self.reconnect_count,
            "stop_requested": self.stop_requested,
            "duplicate_partials": self.duplicate_partials,
            "duplicate_results": self.duplicate_results,
            "tracked_sequences": len(self.seen_sequences),
            "tracked_partials": len(self.emitted_partial_keys),
            "tracked_results": len(self.emitted_segment_keys),
        }

    def queue_pending_chunk(self, chunk: PendingLiveChunk) -> None:
        self.queued_chunks += 1
        if self.pending_chunk is not None:
            self.replaced_pending_chunks += 1
        self.pending_chunk = chunk

    def pop_pending_chunk(self) -> PendingLiveChunk | None:
        chunk = self.pending_chunk
        self.pending_chunk = None
        return chunk

    def _reject(self, reason: str, sequence: int, message: str, size_bytes: int) -> None:
        raise LiveChunkRejected(
            reason=reason,
            sequence=sequence,
            message=message,
            size_bytes=size_bytes,
            stats=self.stats(),
        )

    def _trim_seen_sequences(self) -> None:
        if len(self.seen_sequences) <= _TRACKING_HISTORY_LIMIT:
            return
        keep_from = self.latest_accepted_sequence - _TRACKING_HISTORY_LIMIT + 1
        self.seen_sequences = {sequence for sequence in self.seen_sequences if sequence >= keep_from}

    def _trim_keys(self, keys: set[str], order: list[str]) -> None:
        while len(order) > _TRACKING_HISTORY_LIMIT:
            keys.discard(order.pop(0))
