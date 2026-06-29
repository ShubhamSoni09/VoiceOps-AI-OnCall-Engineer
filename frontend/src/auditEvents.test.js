import { describe, expect, it } from 'vitest'
import { buildAuditEvents } from './auditEvents.js'

describe('audit event projection', () => {
  it('projects durable room messages and actions into a newest-first audit feed', () => {
    const events = buildAuditEvents({
      messages: [
        {
          id: 'msg-map',
          name: 'System',
          text: 'Priya mapped SPEAKER_00 to Priya Nair.',
          createdAt: '2026-06-18T12:00:00Z',
          metadata: {
            source: 'speaker_mapping',
            event: 'speaker_mapping_created',
            speaker_label: 'SPEAKER_00',
            mapped_user_name: 'Priya Nair',
            mapping_source: 'manual',
          },
        },
        {
          id: 'msg-gate',
          name: 'Ada',
          text: 'Real browser mic live finished: passed.',
          createdAt: '2026-06-18T12:02:00Z',
          metadata: {
            source: 'demo_gate_result',
            gate_label: 'Real browser mic live',
            gate_job_id: 'gate-live',
            gate_status: 'succeeded',
            evidence_status: 'passed',
            speaker_labels: ['SPEAKER_00', 'SPEAKER_01'],
            requested_chunks: 2,
            completed_chunks: 2,
          },
        },
      ],
      actions: [
        {
          id: 'act-patch',
          action: 'patch',
          status: 'pending_approval',
          pending_approval: true,
          summary: 'Patch waiting for Bob approval',
          requested_by_name: 'Priya Nair',
          files_changed: ['app.py'],
          approval: { test_command: 'python -m pytest -q' },
          created_at: '2026-06-18T12:01:00Z',
        },
      ],
    })

    expect(events.map((event) => event.id)).toEqual([
      'message:msg-gate',
      'action:act-patch:pending_approval:',
      'message:msg-map',
    ])
    expect(events[0]).toMatchObject({
      kind: 'gate',
      tone: 'ok',
      title: 'Demo gate result',
      status: 'passed',
      chips: ['Real browser mic live', 'gate-live', '2 labels', '2/2 chunks'],
    })
    expect(events[1]).toMatchObject({
      kind: 'action',
      tone: 'accent',
      status: 'pending approval',
      chips: ['1 file', 'test python -m pytest -q'],
    })
    expect(events[2]).toMatchObject({
      kind: 'speaker',
      status: 'mapped',
      detail: 'SPEAKER_00 mapped to Priya Nair',
      chips: ['SPEAKER_00', 'Priya Nair', 'manual'],
    })
  })

  it('keeps failed gates and rejected actions explicit', () => {
    const events = buildAuditEvents({
      messages: [
        {
          id: 'msg-failed-gate',
          name: 'Ada',
          text: 'Mock closure harness failed.',
          createdAt: '2026-06-18T12:04:00Z',
          metadata: {
            source: 'demo_gate_result',
            gate_label: 'Mock closure harness',
            gate_job_id: 'gate-failed',
            gate_status: 'failed',
            evidence_status: 'failed',
            error: 'speaker labels collapsed',
          },
        },
      ],
      actions: [
        {
          id: 'act-rejected',
          action: 'patch',
          status: 'rejected',
          summary: 'Sam rejected the patch proposal.',
          requested_by_name: 'Sam Ortiz',
          created_at: '2026-06-18T12:05:00Z',
        },
      ],
    })

    expect(events[0]).toMatchObject({
      id: 'action:act-rejected:rejected:',
      tone: 'warn',
      status: 'rejected',
    })
    expect(events[1]).toMatchObject({
      id: 'message:msg-failed-gate',
      tone: 'warn',
      status: 'failed',
      detail: 'speaker labels collapsed',
    })
  })
})
