export const REAL_DIARIZATION_COMMAND = 'cd backend && python scripts/demo_readiness.py --real-audio <sample.wav> --require-real-diarization'
export const MOCK_E2E_COMMAND = 'cd frontend && npm run e2e:all -- --json'
export const REAL_LIVE_E2E_COMMAND = 'cd frontend && npm run e2e:all -- --json --real-live --real-live-worker-mode persistent_subprocess --real-live-chunks 2 --real-live-timeout 300'
export const REAL_BROWSER_LIVE_E2E_COMMAND = 'cd frontend && npm run e2e:all -- --json --real-browser-live --real-browser-live-worker-mode persistent_subprocess --real-browser-live-timeout 180'

export function demoEvidenceSummary(readiness) {
  const speakerVerification = speakerVerificationSummary(readiness)
  const readinessReady = Boolean(readiness?.ready)
  const realDiarizationReady = Boolean(speakerVerification?.multiSpeakerReady)
  const evidence = evidenceById(readiness?.demo_evidence?.records)
  const mockEvidence = evidence.mock_e2e
  const realLiveEvidence = evidence.real_live_backend
  const realBrowserEvidence = evidence.real_browser_live
  const realLiveStrict = strictLiveEvidencePassed(realLiveEvidence, { requireTimeline: true })
  const realBrowserStrict = strictLiveEvidencePassed(realBrowserEvidence, { requireTimeline: false })

  return [
    {
      id: 'mock_e2e',
      label: 'Mock closure harness',
      ...evidenceSummary(
        mockEvidence,
        {
          tone: readinessReady ? 'ok' : 'warn',
          status: readinessReady ? 'ready' : 'check required',
          metric: 'fast regression',
          detail: 'Covers mock live meeting, memory Q&A, action approval, git workflow, and handoff without real audio.',
          command: MOCK_E2E_COMMAND,
        },
        MOCK_E2E_COMMAND,
      ),
    },
    {
      id: 'real_live_backend',
      label: 'Real WhisperX backend',
      ...evidenceSummary(
        realLiveEvidence,
        {
          tone: realLiveStrict ? 'ok' : 'warn',
          status: realLiveStrict ? 'recorded' : speakerVerification?.verified ? 'weak evidence' : 'manual gate',
          metric: speakerVerification?.metric || 'not verified',
          detail: realDiarizationReady
            ? speakerVerification.detail
            : speakerVerification?.verified
              ? speakerVerification.detail
            : 'Run the real backend live gate to prove WhisperX produces speaker labels without browser audio.',
          command: REAL_LIVE_E2E_COMMAND,
          labels: speakerVerification?.labels || '',
          elapsed: speakerVerification?.elapsed || '',
        },
        REAL_LIVE_E2E_COMMAND,
      ),
    },
    {
      id: 'real_browser_live',
      label: 'Real browser mic path',
      ...evidenceSummary(
        realBrowserEvidence,
        {
          tone: realBrowserStrict ? 'ok' : 'warn',
          status: realBrowserStrict ? 'recorded' : 'manual gate',
          metric: 'strict UI path',
          detail: 'Runs Chromium fake microphone through MediaRecorder, backend WhisperX, timeline rendering, and speaker labels.',
          command: REAL_BROWSER_LIVE_E2E_COMMAND,
        },
        REAL_BROWSER_LIVE_E2E_COMMAND,
      ),
    },
  ]
}

export function latestDemoGateResult(messages) {
  if (!Array.isArray(messages)) return null
  const message = [...messages].reverse().find((item) => item?.metadata?.source === 'demo_gate_result')
  if (!message) return null
  const metadata = message.metadata || {}
  const status = metadata.gate_status || metadata.evidence_status || 'finished'
  const passed = status === 'succeeded' || metadata.evidence_status === 'passed'
  const failed = status === 'failed' || metadata.evidence_status === 'failed'
  const labels = Array.isArray(metadata.speaker_labels)
    ? metadata.speaker_labels.filter(Boolean).slice(0, 3).join(', ')
    : ''
  const chunks = completedChunks({
    completed_chunks: metadata.completed_chunks,
    requested_chunks: metadata.requested_chunks,
  })
  const metric = [
    labels ? `${metadata.distinct_speaker_count || metadata.speaker_labels.length} labels` : '',
    chunks,
    formatElapsed(metadata.duration_ms),
  ].filter(Boolean).join(' | ')
  return {
    id: message.id,
    gateId: metadata.gate_id || '',
    jobId: metadata.gate_job_id || '',
    label: metadata.gate_label || 'Demo gate',
    status: passed ? 'passed' : failed ? 'failed' : status,
    tone: passed ? 'ok' : 'warn',
    detail: metadata.error || metadata.detail || message.text || '',
    metric,
  }
}

function evidenceById(records) {
  if (!Array.isArray(records)) return {}
  return Object.fromEntries(records.filter((item) => item?.id).map((item) => [item.id, item]))
}

function evidenceSummary(record, fallback, fallbackCommand) {
  if (!record) return fallback
  const passed = record.status === 'passed'
  const strictPassed = record.id === 'real_live_backend'
    ? strictLiveEvidencePassed(record, { requireTimeline: true })
    : record.id === 'real_browser_live'
      ? strictLiveEvidencePassed(record, { requireTimeline: false })
      : passed
  const labels = Array.isArray(record.speaker_labels)
    ? record.speaker_labels.filter(Boolean).slice(0, 3).join(', ')
    : ''
  const chunks = completedChunks(record)
  const metric = [
    labels ? `${record.distinct_speaker_count || record.speaker_labels.length} labels` : '',
    chunks,
    formatElapsed(record.duration_ms),
  ].filter(Boolean).join(' | ')
  const command = Array.isArray(record.command) && record.command.length
    ? record.command.join(' ')
    : fallbackCommand
  return {
    tone: strictPassed ? 'ok' : 'warn',
    status: passed && !strictPassed ? 'weak evidence' : passed ? 'passed' : 'failed',
    metric: metric || fallback.metric,
    detail: passed && !strictPassed
      ? `${record.detail || fallback.detail} Evidence must include 2+ speaker labels and completed live chunks.`
      : record.detail || record.error || fallback.detail,
    command,
    labels,
    elapsed: formatCheckedAt(record.checked_at),
  }
}

function strictLiveEvidencePassed(record, { requireTimeline }) {
  if (!record || record.status !== 'passed') return false
  const labels = Array.isArray(record.speaker_labels)
    ? [...new Set(record.speaker_labels.filter(Boolean).map(String))]
    : []
  if (Number(record.distinct_speaker_count || 0) < 2 || labels.length < 2) return false
  const completed = Number(record.completed_chunks || 0)
  const requested = Number(record.requested_chunks || 0)
  if (completed < 1) return false
  if (requested > 0 && completed < requested) return false
  if (requireTimeline && Number(record.timeline_message_count || 0) < 1) return false
  return true
}

function completedChunks(record) {
  const completed = Number(record.completed_chunks || 0)
  const requested = Number(record.requested_chunks || 0)
  if (completed > 0 && requested > 0) return `${completed}/${requested} chunks`
  if (completed > 0) return `${completed} chunks`
  return ''
}

export function speakerVerificationSummary(readiness) {
  const verification = readiness?.speaker_verification
  if (!verification) return null
  const check = (readiness.checks || []).find((item) => item.id === 'diarization')
  const count = Number(verification.distinct_speaker_count || 0)
  const labels = Array.isArray(verification.speaker_labels)
    ? verification.speaker_labels.filter(Boolean)
    : []
  const verified = Boolean(verification.verified)
  const multiSpeakerReady = verified && count >= 2
  const status = verification.status || (verified ? 'verified' : 'not_verified')
  return {
    title: multiSpeakerReady ? 'Real diarization verified' : verified ? 'Real diarization needs 2 speakers' : statusLabel(status),
    tone: multiSpeakerReady ? 'ok' : 'warn',
    status,
    detail: verificationDetail(verification, check, status),
    metric: verified
      ? `${count} speaker label${count === 1 ? '' : 's'}`
      : status === 'invalid'
        ? 'report invalid'
        : 'not verified',
    labels: labels.slice(0, 3).join(', '),
    checkedAt: formatCheckedAt(verification.checked_at),
    elapsed: formatElapsed(verification.elapsed_ms),
    strict: Boolean(verification.strict_multi_speaker),
    generatedAudio: Boolean(verification.generated_audio),
    lastStage: verification.last_stage || '',
    quality: verificationQualitySummary(verification.quality || {}),
    warnings: Array.isArray(verification.warnings) ? verification.warnings.filter(Boolean) : [],
    command: REAL_DIARIZATION_COMMAND,
    verified,
    multiSpeakerReady,
  }
}

function verificationQualitySummary(quality) {
  const level = String(quality.level || '').trim()
  const segments = Number(quality.segment_count || 0)
  const hasTranscript = Boolean(quality.has_transcript)
  const notes = Array.isArray(quality.notes) ? quality.notes.filter(Boolean).map(String) : []
  const parts = []
  if (level) parts.push(`quality ${level}`)
  if (segments > 0) parts.push(`${segments} segment${segments === 1 ? '' : 's'}`)
  if (hasTranscript) parts.push('transcript captured')
  return {
    level,
    label: parts.join(' | '),
    notes,
    multiSpeaker: Boolean(quality.multi_speaker),
    strictPassed: Boolean(quality.strict_passed),
  }
}

function verificationDetail(verification, check, status) {
  const base = verification.detail || check?.detail || fallbackDetail(status)
  const count = Number(verification.distinct_speaker_count || 0)
  if (verification.verified && count < 2) {
    const label = count === 1 ? 'speaker label' : 'speaker labels'
    const verb = count === 1 ? 'was' : 'were'
    return `${base} Only ${count} ${label} ${verb} verified; run strict 2+ speaker verification before treating this as multi-speaker ready.`
  }
  const lastStage = verification.last_stage
  if (!lastStage || status === 'verified') return base
  return `${base} Last stage: ${lastStage}.`
}

export function speakerVerificationJobSummary(job) {
  if (!job) return { label: 'not checked', tone: 'warn', detail: 'No verification job has been requested' }
  const latestStage = Array.isArray(job.stages) ? job.stages[job.stages.length - 1] : null
  if (job.state === 'running') {
    return {
      label: 'verifying',
      tone: 'warn',
      detail: latestStage?.message || (job.filename ? `Processing ${job.filename}` : 'Running real audio verification'),
      stage: latestStage?.stage || '',
      elapsed: formatElapsed(job.elapsed_ms || latestStage?.elapsed_ms),
      timeout: formatElapsed(Number(job.timeout_seconds || 0) * 1000),
    }
  }
  if (job.state === 'succeeded') {
    return {
      label: 'verified',
      tone: 'ok',
      detail: job.detail || 'Real audio verification passed',
      stage: latestStage?.stage || '',
      elapsed: formatElapsed(job.elapsed_ms),
    }
  }
  if (job.state === 'failed') {
    return {
      label: 'failed',
      tone: 'warn',
      detail: job.error || job.detail || 'Real audio verification failed',
      stage: latestStage?.stage || '',
      elapsed: formatElapsed(job.elapsed_ms),
    }
  }
  return {
    label: job.state || 'idle',
    tone: 'warn',
    detail: job.detail || 'Upload a short two-speaker sample to verify real diarization',
  }
}

function statusLabel(status) {
  if (status === 'failed') return 'Real diarization failed'
  if (status === 'invalid') return 'Verification report invalid'
  return 'Real diarization not verified'
}

function fallbackDetail(status) {
  if (status === 'failed') return 'Last real diarization smoke did not pass'
  if (status === 'invalid') return 'The saved verification report cannot be read'
  return 'Run a real audio smoke to prove WhisperX speaker attribution'
}

function formatElapsed(value) {
  const ms = Number(value || 0)
  if (!Number.isFinite(ms) || ms <= 0) return ''
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)}s`
}

function formatCheckedAt(value) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}
