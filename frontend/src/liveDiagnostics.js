const COUNTER_FIELDS = [
  ['skipped_chunks', 'skippedChunks'],
  ['queued_chunks', 'queuedChunks'],
  ['pending_chunks', 'pendingChunks'],
  ['replaced_pending_chunks', 'replacedPendingChunks'],
  ['dropped_chunks', 'droppedChunks'],
  ['duplicate_chunks', 'duplicateChunks'],
  ['out_of_order_chunks', 'outOfOrderChunks'],
  ['empty_chunks', 'emptyChunks'],
  ['oversized_chunks', 'oversizedChunks'],
  ['gap_chunks', 'gapChunks'],
]

const MAX_LATENCY_CHUNKS = 6

function normalizeMs(value) {
  return typeof value === 'number' && Number.isFinite(value)
    ? Math.max(0, Math.round(value))
    : null
}

function formatLatency(ms) {
  if (ms == null) return 'pending'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)}s`
}

export function resetLiveQueueDiagnostics() {
  return {
    skippedChunks: 0,
    queuedChunks: 0,
    pendingChunks: 0,
    replacedPendingChunks: 0,
    reconnectAttempts: 0,
    droppedChunks: 0,
    duplicateChunks: 0,
    outOfOrderChunks: 0,
    emptyChunks: 0,
    oversizedChunks: 0,
    gapChunks: 0,
    replacedChunks: 0,
    heldChunk: false,
    heldChunkKb: 0,
    captionFallback: false,
    correctionPending: false,
    lateCorrections: 0,
  }
}

export function resetLiveLatencyDiagnostics() {
  return {
    latencySequence: null,
    latencyStage: 'idle',
    latencyElapsedMs: null,
    latencyChunks: [],
    latencyCompletionCount: 0,
    latencyColdStartSequence: null,
    latencyColdStartMs: null,
    latencyLastChunkSequence: null,
    latencyLastChunkMs: null,
    latencyBestWarmMs: null,
  }
}

export function mergeLiveServerStats(previous, data) {
  const next = {}
  COUNTER_FIELDS.forEach(([serverKey, stateKey]) => {
    if (typeof data?.[serverKey] !== 'number') {
      next[stateKey] = previous?.[stateKey]
      return
    }
    next[stateKey] = stateKey === 'pendingChunks'
      ? data[serverKey]
      : Math.max(previous?.[stateKey] || 0, data[serverKey])
  })
  next.maxChunkKb = typeof data?.max_chunk_bytes === 'number'
    ? Math.max(1, Math.round(data.max_chunk_bytes / 1024))
    : previous?.maxChunkKb
  return next
}

export function mergeLiveCorrectionStats(previous, data) {
  return {
    captionFallback: typeof data?.caption_fallback === 'boolean'
      ? data.caption_fallback
      : previous?.captionFallback || false,
    correctionPending: typeof data?.correction_pending === 'boolean'
      ? data.correction_pending
      : previous?.correctionPending || false,
    lateCorrections: (previous?.lateCorrections || 0)
      + (data?.late_correction === true && data?.type === 'speaker_correction' ? 1 : 0),
  }
}

export function mergeLiveLatencyStats(previous, data, stage) {
  const sequence = typeof data?.sequence === 'number' ? data.sequence : previous?.latencySequence ?? null
  const elapsedMs = normalizeMs(data?.elapsed_ms)
  const latencyStage = stage || data?.stage || previous?.latencyStage || 'idle'
  const previousChunks = Array.isArray(previous?.latencyChunks) ? previous.latencyChunks : []
  let latencyChunks = previousChunks
  let latencyCompletionCount = previous?.latencyCompletionCount || 0
  let latencyColdStartSequence = previous?.latencyColdStartSequence ?? null
  let latencyColdStartMs = previous?.latencyColdStartMs ?? null
  let latencyLastChunkSequence = previous?.latencyLastChunkSequence ?? null
  let latencyLastChunkMs = previous?.latencyLastChunkMs ?? null
  let latencyBestWarmMs = previous?.latencyBestWarmMs ?? null

  if (latencyStage === 'completed' && typeof sequence === 'number' && elapsedMs != null) {
    const existingIndex = previousChunks.findIndex((chunk) => chunk.sequence === sequence)
    const completedChunk = { sequence, totalMs: elapsedMs }
    if (existingIndex >= 0) {
      latencyChunks = previousChunks.map((chunk, index) => (index === existingIndex ? completedChunk : chunk))
    } else {
      latencyChunks = [...previousChunks, completedChunk]
      latencyCompletionCount += 1
    }
    latencyChunks = latencyChunks
      .slice()
      .sort((left, right) => left.sequence - right.sequence)
      .slice(-MAX_LATENCY_CHUNKS)

    if (latencyColdStartMs == null) {
      latencyColdStartMs = elapsedMs
      latencyColdStartSequence = sequence
    } else if (sequence !== latencyColdStartSequence) {
      latencyBestWarmMs = latencyBestWarmMs == null
        ? elapsedMs
        : Math.min(latencyBestWarmMs, elapsedMs)
    }
    latencyLastChunkSequence = sequence
    latencyLastChunkMs = elapsedMs
  }

  return {
    latencySequence: sequence,
    latencyStage,
    latencyElapsedMs: elapsedMs ?? previous?.latencyElapsedMs ?? null,
    latencyChunks,
    latencyCompletionCount,
    latencyColdStartSequence,
    latencyColdStartMs,
    latencyLastChunkSequence,
    latencyLastChunkMs,
    latencyBestWarmMs,
  }
}

export function liveLatencySummary(diagnostics) {
  const completionCount = diagnostics?.latencyCompletionCount || 0
  const coldStartMs = diagnostics?.latencyColdStartMs ?? null
  const bestWarmMs = diagnostics?.latencyBestWarmMs ?? null
  const currentSequence = diagnostics?.latencySequence
  const currentStage = diagnostics?.latencyStage || diagnostics?.stage || 'idle'
  const currentMs = diagnostics?.latencyElapsedMs ?? null

  if (!currentSequence && completionCount === 0) {
    return {
      label: 'waiting',
      tone: 'neutral',
      detail: 'Waiting for the first live audio chunk.',
    }
  }
  if (completionCount === 0) {
    return {
      label: `chunk ${currentSequence || '-'} ${currentStage}`,
      tone: 'warn',
      detail: currentMs == null
        ? 'First chunk is in progress.'
        : `First chunk has spent ${formatLatency(currentMs)} in backend processing.`,
    }
  }
  if (bestWarmMs == null) {
    return {
      label: `cold ${formatLatency(coldStartMs)}`,
      tone: 'warn',
      detail: 'First final chunk measured. Waiting for a second chunk to prove model reuse.',
    }
  }
  const speedup = coldStartMs && bestWarmMs ? Math.max(1, coldStartMs / Math.max(bestWarmMs, 1)) : null
  const speedupText = speedup ? `${speedup.toFixed(speedup < 10 ? 1 : 0)}x faster` : 'warm path measured'
  return {
    label: `warm ${formatLatency(bestWarmMs)}`,
    tone: bestWarmMs <= 2500 ? 'ok' : 'warn',
    detail: `Cold start ${formatLatency(coldStartMs)}. Best warm chunk ${formatLatency(bestWarmMs)}, ${speedupText}.`,
  }
}

export function liveSpeakerReadiness(diagnostics, readiness = null) {
  const provider = diagnostics?.provider || 'local'
  const systemVerification = readiness?.speaker_verification
  const systemVerified = Boolean(systemVerification?.verified)
  if (
    provider !== 'whisperx'
    && !diagnostics?.active
    && systemVerification
    && (systemVerified || systemVerification.status === 'failed' || systemVerification.status === 'invalid')
  ) {
    const labels = Array.isArray(systemVerification.speaker_labels)
      ? systemVerification.speaker_labels.filter(Boolean)
      : []
    const count = Number(systemVerification.distinct_speaker_count || labels.length || 0)
    const quality = systemVerification.quality?.level ? ` Quality ${systemVerification.quality.level}.` : ''
    if (systemVerified) {
      if (count < 2) {
        const verb = count === 1 ? 'was' : 'were'
        return {
          label: count === 1 ? 'verified 1 label' : 'verification weak',
          tone: 'warn',
          detail: `${systemVerification.detail || 'Real diarization smoke has passed.'} Only ${count} speaker label${count === 1 ? '' : 's'} ${verb} verified; run strict 2+ speaker verification.`,
        }
      }
      return {
        label: count > 0 ? `verified ${count} labels` : 'verified',
        tone: 'ok',
        detail: labels.length
          ? `Real diarization smoke verified ${labels.slice(0, 3).join(', ')}.${quality}`
          : `${systemVerification.detail || 'Real diarization smoke has passed.'}${quality}`,
      }
    }
    return {
      label: systemVerification.status === 'invalid' ? 'verification invalid' : 'verification failed',
      tone: 'warn',
      detail: systemVerification.detail || 'Real diarization smoke is not passing yet.',
    }
  }
  if (provider !== 'whisperx') {
    return {
      label: provider === 'mock' ? 'mock ready' : 'local ready',
      tone: 'ok',
      detail: provider === 'mock'
        ? 'Mock speaker labels are deterministic.'
        : 'Local browser audio path is available.',
    }
  }
  if (diagnostics?.phase === 'degraded' || diagnostics?.captionFallback) {
    return {
      label: 'degraded',
      tone: 'warn',
      detail: 'Browser captions are visible while speaker labels finish.',
    }
  }
  if (diagnostics?.correctionPending) {
    return {
      label: 'calibrating',
      tone: 'warn',
      detail: 'Speaker attribution is still being refined.',
    }
  }
  if (diagnostics?.speakerVerificationReady) {
    const count = diagnostics.speakerVerificationCount || diagnostics.speakerVerificationLabels?.length || 0
    const labels = Array.isArray(diagnostics.speakerVerificationLabels)
      ? diagnostics.speakerVerificationLabels.filter(Boolean).join(', ')
      : ''
    const quality = diagnostics.speakerVerificationQuality
      ? ` Quality ${diagnostics.speakerVerificationQuality}.`
      : ''
    if (count < 2) {
      const verb = count === 1 ? 'was' : 'were'
      return {
        label: count === 1 ? 'verified 1 label' : 'verification weak',
        tone: 'warn',
        detail: `${diagnostics.speakerVerificationDetail || 'Real diarization smoke has passed.'} Only ${count} speaker label${count === 1 ? '' : 's'} ${verb} verified; run strict 2+ speaker verification.`,
      }
    }
    return {
      label: count > 0 ? `verified ${count} labels` : 'verified',
      tone: 'ok',
      detail: labels
        ? `Real diarization smoke verified ${labels}.${quality}`
        : `${diagnostics.speakerVerificationDetail || 'Real diarization smoke has passed.'}${quality}`,
    }
  }
  if (diagnostics?.speakerVerificationStatus === 'failed' || diagnostics?.speakerVerificationStatus === 'invalid') {
    return {
      label: diagnostics.speakerVerificationStatus === 'invalid' ? 'verification invalid' : 'verification failed',
      tone: 'warn',
      detail: diagnostics.speakerVerificationDetail || 'Real diarization smoke is not passing yet.',
    }
  }
  if (diagnostics?.warmupRecommended) {
    return {
      label: 'warmup recommended',
      tone: 'warn',
      detail: diagnostics.warmupHint || 'Warm WhisperX before the meeting to reduce first-turn lag.',
    }
  }
  if (diagnostics?.cpuMode) {
    return {
      label: 'cpu ready',
      tone: 'warn',
      detail: 'WhisperX is available, but CPU diarization may lag.',
    }
  }
  return {
    label: 'ready',
    tone: 'ok',
    detail: 'WhisperX speaker attribution is ready.',
  }
}
