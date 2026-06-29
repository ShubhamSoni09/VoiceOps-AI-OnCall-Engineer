import { describe, expect, it } from 'vitest'
import {
  liveLatencySummary,
  liveSpeakerReadiness,
  mergeLiveCorrectionStats,
  mergeLiveLatencyStats,
  mergeLiveServerStats,
  resetLiveLatencyDiagnostics,
  resetLiveQueueDiagnostics,
} from './liveDiagnostics.js'

describe('live diagnostics helpers', () => {
  it('resets client and backend queue counters together', () => {
    expect(resetLiveQueueDiagnostics()).toMatchObject({
      skippedChunks: 0,
      queuedChunks: 0,
      pendingChunks: 0,
      replacedPendingChunks: 0,
      droppedChunks: 0,
      heldChunk: false,
      heldChunkKb: 0,
      captionFallback: false,
      correctionPending: false,
      lateCorrections: 0,
    })
  })

  it('merges monotonic backend queue stats without lowering existing counters', () => {
    const merged = mergeLiveServerStats(
      {
        skippedChunks: 3,
        queuedChunks: 1,
        pendingChunks: 1,
        replacedPendingChunks: 0,
        droppedChunks: 2,
        maxChunkKb: 128,
      },
      {
        skipped_chunks: 2,
        queued_chunks: 4,
        pending_chunks: 0,
        replaced_pending_chunks: 1,
        dropped_chunks: 3,
        max_chunk_bytes: 262144,
      },
    )

    expect(merged).toMatchObject({
      skippedChunks: 3,
      queuedChunks: 4,
      pendingChunks: 0,
      replacedPendingChunks: 1,
      droppedChunks: 3,
      maxChunkKb: 256,
    })
  })

  it('tracks caption fallback and late speaker corrections', () => {
    const fallback = mergeLiveCorrectionStats(
      { captionFallback: false, correctionPending: false, lateCorrections: 0 },
      {
        type: 'session_status',
        caption_fallback: true,
        correction_pending: true,
      },
    )
    const corrected = mergeLiveCorrectionStats(fallback, {
      type: 'speaker_correction',
      late_correction: true,
      correction_pending: false,
      caption_fallback: false,
    })

    expect(fallback).toMatchObject({
      captionFallback: true,
      correctionPending: true,
      lateCorrections: 0,
    })
    expect(corrected).toMatchObject({
      captionFallback: false,
      correctionPending: false,
      lateCorrections: 1,
    })
  })

  it('resets live latency diagnostics', () => {
    expect(resetLiveLatencyDiagnostics()).toMatchObject({
      latencySequence: null,
      latencyStage: 'idle',
      latencyElapsedMs: null,
      latencyChunks: [],
      latencyCompletionCount: 0,
      latencyColdStartMs: null,
      latencyBestWarmMs: null,
    })
  })

  it('records cold start and best warm chunk latency from backend stage events', () => {
    const firstCompleted = mergeLiveLatencyStats(
      resetLiveLatencyDiagnostics(),
      { type: 'session_status', sequence: 1, stage: 'completed', elapsed_ms: 5860 },
      'completed',
    )
    const secondCompleted = mergeLiveLatencyStats(
      firstCompleted,
      { type: 'session_status', sequence: 2, stage: 'completed', elapsed_ms: 999 },
      'completed',
    )

    expect(firstCompleted).toMatchObject({
      latencySequence: 1,
      latencyCompletionCount: 1,
      latencyColdStartSequence: 1,
      latencyColdStartMs: 5860,
      latencyLastChunkSequence: 1,
      latencyLastChunkMs: 5860,
      latencyBestWarmMs: null,
    })
    expect(secondCompleted).toMatchObject({
      latencySequence: 2,
      latencyCompletionCount: 2,
      latencyColdStartSequence: 1,
      latencyColdStartMs: 5860,
      latencyLastChunkSequence: 2,
      latencyLastChunkMs: 999,
      latencyBestWarmMs: 999,
    })
  })

  it('does not double count repeated completed status for the same chunk', () => {
    const first = mergeLiveLatencyStats(
      resetLiveLatencyDiagnostics(),
      { sequence: 1, stage: 'completed', elapsed_ms: 1200 },
      'completed',
    )
    const repeated = mergeLiveLatencyStats(
      first,
      { sequence: 1, stage: 'completed', elapsed_ms: 1250 },
      'completed',
    )

    expect(repeated).toMatchObject({
      latencyCompletionCount: 1,
      latencyColdStartMs: 1200,
      latencyLastChunkMs: 1250,
    })
    expect(repeated.latencyChunks).toEqual([{ sequence: 1, totalMs: 1250 }])
  })

  it('summarizes live latency without pretending warm path exists too early', () => {
    expect(liveLatencySummary(resetLiveLatencyDiagnostics())).toMatchObject({
      label: 'waiting',
      tone: 'neutral',
    })
    expect(liveLatencySummary({
      ...resetLiveLatencyDiagnostics(),
      latencySequence: 1,
      latencyStage: 'transcribing',
      latencyElapsedMs: 640,
    })).toMatchObject({
      label: 'chunk 1 transcribing',
      tone: 'warn',
      detail: 'First chunk has spent 640ms in backend processing.',
    })
    expect(liveLatencySummary({
      ...resetLiveLatencyDiagnostics(),
      latencyCompletionCount: 1,
      latencyColdStartMs: 5860,
    })).toMatchObject({
      label: 'cold 5.9s',
      tone: 'warn',
    })
    expect(liveLatencySummary({
      ...resetLiveLatencyDiagnostics(),
      latencyCompletionCount: 2,
      latencyColdStartMs: 5860,
      latencyBestWarmMs: 999,
    })).toMatchObject({
      label: 'warm 999ms',
      tone: 'ok',
      detail: 'Cold start 5.9s. Best warm chunk 999ms, 5.9x faster.',
    })
  })

  it('summarizes live speaker readiness for mock and WhisperX states', () => {
    expect(liveSpeakerReadiness({ provider: 'mock' })).toMatchObject({
      label: 'mock ready',
      tone: 'ok',
    })
    expect(liveSpeakerReadiness(
      { provider: 'local', active: false },
      {
        speaker_verification: {
          verified: true,
          distinct_speaker_count: 2,
          speaker_labels: ['SPEAKER_00', 'SPEAKER_01'],
          quality: { level: 'verified' },
        },
      },
    )).toMatchObject({
      label: 'verified 2 labels',
      tone: 'ok',
      detail: 'Real diarization smoke verified SPEAKER_00, SPEAKER_01. Quality verified.',
    })
    expect(liveSpeakerReadiness(
      { provider: 'local', active: false },
      {
        speaker_verification: {
          verified: true,
          distinct_speaker_count: 1,
          speaker_labels: ['SPEAKER_00'],
          detail: 'Verified 1 speaker label with WhisperX',
        },
      },
    )).toMatchObject({
      label: 'verified 1 label',
      tone: 'warn',
      detail: 'Verified 1 speaker label with WhisperX Only 1 speaker label was verified; run strict 2+ speaker verification.',
    })
    expect(liveSpeakerReadiness(
      { provider: 'local', active: false },
      {
        speaker_verification: {
          verified: false,
          status: 'failed',
          detail: 'Only one speaker label was found.',
        },
      },
    )).toMatchObject({
      label: 'verification failed',
      tone: 'warn',
      detail: 'Only one speaker label was found.',
    })
    expect(liveSpeakerReadiness({
      provider: 'whisperx',
      phase: 'degraded',
      captionFallback: true,
    })).toMatchObject({
      label: 'degraded',
      tone: 'warn',
    })
    expect(liveSpeakerReadiness({
      provider: 'whisperx',
      correctionPending: true,
      speakerVerificationReady: true,
      speakerVerificationCount: 2,
    })).toMatchObject({
      label: 'calibrating',
      tone: 'warn',
    })
    expect(liveSpeakerReadiness({
      provider: 'whisperx',
      speakerVerificationReady: true,
      speakerVerificationCount: 2,
      speakerVerificationLabels: ['SPEAKER_00', 'SPEAKER_01'],
      speakerVerificationQuality: 'verified',
      warmupRecommended: true,
    })).toMatchObject({
      label: 'verified 2 labels',
      tone: 'ok',
      detail: 'Real diarization smoke verified SPEAKER_00, SPEAKER_01. Quality verified.',
    })
    expect(liveSpeakerReadiness({
      provider: 'whisperx',
      speakerVerificationReady: true,
      speakerVerificationCount: 1,
      speakerVerificationLabels: ['SPEAKER_00'],
      speakerVerificationDetail: 'Verified 1 speaker label with WhisperX',
    })).toMatchObject({
      label: 'verified 1 label',
      tone: 'warn',
      detail: 'Verified 1 speaker label with WhisperX Only 1 speaker label was verified; run strict 2+ speaker verification.',
    })
    expect(liveSpeakerReadiness({
      provider: 'whisperx',
      speakerVerificationStatus: 'failed',
      speakerVerificationDetail: 'Only one speaker label was found.',
    })).toMatchObject({
      label: 'verification failed',
      tone: 'warn',
      detail: 'Only one speaker label was found.',
    })
    expect(liveSpeakerReadiness({
      provider: 'whisperx',
      warmupRecommended: true,
      warmupHint: 'Warm first',
    })).toMatchObject({
      label: 'warmup recommended',
      tone: 'warn',
      detail: 'Warm first',
    })
    expect(liveSpeakerReadiness({
      provider: 'whisperx',
      cpuMode: true,
    })).toMatchObject({
      label: 'cpu ready',
      tone: 'warn',
    })
    expect(liveSpeakerReadiness({ provider: 'whisperx' })).toMatchObject({
      label: 'ready',
      tone: 'ok',
    })
  })
})
