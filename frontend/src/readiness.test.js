import { describe, expect, it } from 'vitest'
import {
  MOCK_E2E_COMMAND,
  REAL_BROWSER_LIVE_E2E_COMMAND,
  REAL_DIARIZATION_COMMAND,
  REAL_LIVE_E2E_COMMAND,
  demoEvidenceSummary,
  latestDemoGateResult,
  speakerVerificationJobSummary,
  speakerVerificationSummary,
} from './readiness.js'

describe('readiness helpers', () => {
  it('summarizes a verified real diarization smoke', () => {
    const summary = speakerVerificationSummary({
      checks: [{ id: 'diarization', detail: 'verified' }],
      speaker_verification: {
        verified: true,
        status: 'verified',
        detail: 'Verified 2 speaker label(s) with WhisperX',
        distinct_speaker_count: 2,
        speaker_labels: ['SPEAKER_00', 'SPEAKER_01'],
        elapsed_ms: 1234,
        checked_at: '2026-06-18T12:00:00+00:00',
        strict_multi_speaker: true,
        quality: {
          level: 'verified',
          segment_count: 2,
          has_transcript: true,
          multi_speaker: true,
          strict_passed: true,
          notes: [],
        },
      },
    })

    expect(summary).toMatchObject({
      title: 'Real diarization verified',
      tone: 'ok',
      metric: '2 speaker labels',
      labels: 'SPEAKER_00, SPEAKER_01',
      elapsed: '1.2s',
      strict: true,
      quality: {
        label: 'quality verified | 2 segments | transcript captured',
        multiSpeaker: true,
        strictPassed: true,
      },
    })
    expect(summary.checkedAt).toBeTruthy()
  })

  it('returns a command when real diarization is not verified', () => {
    const summary = speakerVerificationSummary({
      checks: [{ id: 'diarization', detail: 'No real smoke report' }],
      speaker_verification: {
        verified: false,
        status: 'not_verified',
        distinct_speaker_count: 0,
        speaker_labels: [],
      },
    })

    expect(summary).toMatchObject({
      title: 'Real diarization not verified',
      tone: 'warn',
      metric: 'not verified',
      command: REAL_DIARIZATION_COMMAND,
    })
    expect(summary.detail).toContain('No real smoke report')
  })

  it('treats single-speaker verification as weak evidence for a multi-person demo', () => {
    const summary = speakerVerificationSummary({
      checks: [{ id: 'diarization', detail: 'weak' }],
      speaker_verification: {
        verified: true,
        status: 'verified',
        detail: 'Verified 1 speaker label with WhisperX',
        distinct_speaker_count: 1,
        speaker_labels: ['SPEAKER_00'],
        quality: {
          level: 'weak',
          segment_count: 1,
          has_transcript: true,
          multi_speaker: false,
          strict_passed: false,
        },
      },
    })

    expect(summary).toMatchObject({
      title: 'Real diarization needs 2 speakers',
      tone: 'warn',
      metric: '1 speaker label',
      labels: 'SPEAKER_00',
      verified: true,
      multiSpeakerReady: false,
    })
    expect(summary.detail).toContain('Only 1 speaker label')

    const items = demoEvidenceSummary({
      ready: true,
      speaker_verification: {
        verified: true,
        status: 'verified',
        detail: 'Verified 1 speaker label with WhisperX',
        distinct_speaker_count: 1,
        speaker_labels: ['SPEAKER_00'],
      },
    })

    expect(items[1]).toMatchObject({
      tone: 'warn',
      status: 'weak evidence',
      metric: '1 speaker label',
    })
    expect(items[1].detail).toContain('strict 2+ speaker verification')
  })

  it('keeps invalid verification reports explicit', () => {
    const summary = speakerVerificationSummary({
      speaker_verification: {
        verified: false,
        status: 'invalid',
        detail: 'Speaker verification report is unreadable',
        error: 'bad json',
      },
    })

    expect(summary).toMatchObject({
      title: 'Verification report invalid',
      tone: 'warn',
      metric: 'report invalid',
      detail: 'Speaker verification report is unreadable',
    })
  })

  it('summarizes failed verification stage and warning', () => {
    const summary = speakerVerificationSummary({
      speaker_verification: {
        verified: false,
        status: 'failed',
        detail: 'WhisperX worker timed out',
        last_stage: 'diarizing',
        warnings: ['CPU diarization can exceed the smoke timeout.'],
        quality: {
          level: 'failed',
          segment_count: 0,
          has_transcript: false,
          multi_speaker: false,
          strict_passed: false,
          notes: ['No final speaker-attributed segments were produced.'],
        },
        elapsed_ms: 600493,
      },
    })

    expect(summary).toMatchObject({
      title: 'Real diarization failed',
      tone: 'warn',
      metric: 'not verified',
      lastStage: 'diarizing',
      elapsed: '600s',
      warnings: ['CPU diarization can exceed the smoke timeout.'],
      quality: {
        label: 'quality failed',
        notes: ['No final speaker-attributed segments were produced.'],
      },
    })
    expect(summary.detail).toContain('Last stage: diarizing.')
  })

  it('summarizes uploaded real-audio verification jobs', () => {
    expect(speakerVerificationJobSummary({
      state: 'running',
      filename: 'meeting.wav',
      timeout_seconds: 120,
      stages: [{ stage: 'running_whisperx', message: 'WhisperX verification is running', elapsed_ms: 1500 }],
    })).toMatchObject({
      label: 'verifying',
      tone: 'warn',
      detail: 'WhisperX verification is running',
      stage: 'running_whisperx',
      elapsed: '1.5s',
      timeout: '120s',
    })
    expect(speakerVerificationJobSummary({ state: 'succeeded', detail: 'done' })).toMatchObject({
      label: 'verified',
      tone: 'ok',
      detail: 'done',
    })
    expect(speakerVerificationJobSummary({ state: 'failed', error: 'timeout' })).toMatchObject({
      label: 'failed',
      tone: 'warn',
      detail: 'timeout',
    })
  })

  it('builds a three-step demo evidence checklist without fake browser pass state', () => {
    const items = demoEvidenceSummary({
      ready: true,
      speaker_verification: {
        verified: true,
        status: 'verified',
        detail: 'Verified 2 speaker label(s) with WhisperX',
        distinct_speaker_count: 2,
        speaker_labels: ['SPEAKER_00', 'SPEAKER_01'],
        elapsed_ms: 1510,
      },
    })

    expect(items.map((item) => item.id)).toEqual(['mock_e2e', 'real_live_backend', 'real_browser_live'])
    expect(items[0]).toMatchObject({
      tone: 'ok',
      status: 'ready',
      command: MOCK_E2E_COMMAND,
    })
    expect(items[1]).toMatchObject({
      tone: 'warn',
      status: 'weak evidence',
      metric: '2 speaker labels',
      labels: 'SPEAKER_00, SPEAKER_01',
      elapsed: '1.5s',
      command: REAL_LIVE_E2E_COMMAND,
    })
    expect(items[2]).toMatchObject({
      tone: 'warn',
      status: 'manual gate',
      command: REAL_BROWSER_LIVE_E2E_COMMAND,
    })
  })

  it('keeps real backend evidence manual when no persisted verification exists', () => {
    const items = demoEvidenceSummary({ ready: false })

    expect(items[0]).toMatchObject({ tone: 'warn', status: 'check required' })
    expect(items[1]).toMatchObject({
      tone: 'warn',
      status: 'manual gate',
      metric: 'not verified',
    })
    expect(items[1].detail).toContain('Run the real backend live gate')
  })

  it('uses persisted demo evidence for real live and browser gates', () => {
    const items = demoEvidenceSummary({
      ready: true,
      demo_evidence: {
        records: [
          {
            id: 'real_live_backend',
            label: 'Real WhisperX backend live',
            status: 'passed',
            checked_at: '2026-06-18T13:00:00+00:00',
            detail: 'Real live WebSocket smoke passed',
            duration_ms: 7100,
            command: ['python', 'scripts/smoke_live_meeting_real.py', '--json'],
            speaker_labels: ['SPEAKER_00', 'SPEAKER_01'],
            distinct_speaker_count: 2,
            requested_chunks: 2,
            completed_chunks: 2,
            timeline_message_count: 4,
          },
          {
            id: 'real_browser_live',
            label: 'Real browser mic live',
            status: 'passed',
            checked_at: '2026-06-18T13:05:00+00:00',
            detail: 'Chromium fake microphone reached MediaRecorder',
            speaker_labels: ['SPEAKER_00', 'SPEAKER_01'],
            distinct_speaker_count: 2,
            requested_chunks: 1,
            completed_chunks: 1,
          },
        ],
      },
    })

    expect(items[1]).toMatchObject({
      tone: 'ok',
      status: 'passed',
      metric: '2 labels | 2/2 chunks | 7.1s',
      detail: 'Real live WebSocket smoke passed',
      command: 'python scripts/smoke_live_meeting_real.py --json',
    })
    expect(items[2]).toMatchObject({
      tone: 'ok',
      status: 'passed',
      metric: '2 labels | 1/1 chunks',
      detail: 'Chromium fake microphone reached MediaRecorder',
    })
    expect(items[2].elapsed).toBeTruthy()
  })

  it('downgrades passed live evidence that lacks strict multi-speaker proof', () => {
    const items = demoEvidenceSummary({
      ready: true,
      demo_evidence: {
        records: [
          {
            id: 'real_live_backend',
            label: 'Real WhisperX backend live',
            status: 'passed',
            checked_at: '2026-06-18T13:00:00+00:00',
            detail: 'Smoke process exited zero',
            speaker_labels: ['SPEAKER_00'],
            distinct_speaker_count: 1,
            requested_chunks: 2,
            completed_chunks: 2,
            timeline_message_count: 4,
          },
        ],
      },
      speaker_verification: {
        verified: true,
        status: 'verified',
        detail: 'Verified 2 speaker label(s) with WhisperX',
        distinct_speaker_count: 2,
        speaker_labels: ['SPEAKER_00', 'SPEAKER_01'],
      },
    })

    expect(items[1]).toMatchObject({
      tone: 'warn',
      status: 'weak evidence',
      metric: '1 labels | 2/2 chunks',
    })
    expect(items[1].detail).toContain('Evidence must include 2+ speaker labels')
  })

  it('surfaces failed persisted browser evidence instead of hiding it as manual', () => {
    const items = demoEvidenceSummary({
      demo_evidence: {
        records: [
          {
            id: 'real_browser_live',
            label: 'Real browser mic live',
            status: 'failed',
            checked_at: '2026-06-18T13:05:00+00:00',
            error: 'missing real speaker label SPEAKER_01',
          },
        ],
      },
    })

    expect(items[2]).toMatchObject({
      tone: 'warn',
      status: 'failed',
      detail: 'missing real speaker label SPEAKER_01',
      command: REAL_BROWSER_LIVE_E2E_COMMAND,
    })
  })

  it('summarizes the latest demo gate timeline result for the right rail', () => {
    const result = latestDemoGateResult([
      {
        id: 'msg-old',
        text: 'old gate result',
        metadata: {
          source: 'demo_gate_result',
          gate_id: 'mock_e2e',
          gate_job_id: 'gate-old',
          gate_status: 'failed',
          error: 'old failure',
        },
      },
      {
        id: 'msg-new',
        text: 'Mock closure harness finished: passed in 2.1s.',
        metadata: {
          source: 'demo_gate_result',
          gate_id: 'real_browser_live',
          gate_label: 'Real browser mic live',
          gate_job_id: 'gate-new',
          gate_status: 'succeeded',
          evidence_status: 'passed',
          duration_ms: 2130,
          speaker_labels: ['SPEAKER_00', 'SPEAKER_01'],
          distinct_speaker_count: 2,
          requested_chunks: 2,
          completed_chunks: 2,
          detail: 'Browser mic reached the timeline',
        },
      },
    ])

    expect(result).toMatchObject({
      id: 'msg-new',
      gateId: 'real_browser_live',
      jobId: 'gate-new',
      label: 'Real browser mic live',
      status: 'passed',
      tone: 'ok',
      metric: '2 labels | 2/2 chunks | 2.1s',
      detail: 'Browser mic reached the timeline',
    })
  })

  it('keeps failed demo gate timeline results explicit', () => {
    const result = latestDemoGateResult([
      {
        id: 'msg-failed',
        text: 'Real browser mic live finished: failed.',
        metadata: {
          source: 'demo_gate_result',
          gate_id: 'real_browser_live',
          gate_job_id: 'gate-failed',
          gate_status: 'failed',
          evidence_status: 'failed',
          error: 'missing speaker label SPEAKER_01',
        },
      },
    ])

    expect(result).toMatchObject({
      status: 'failed',
      tone: 'warn',
      detail: 'missing speaker label SPEAKER_01',
    })
  })
})
