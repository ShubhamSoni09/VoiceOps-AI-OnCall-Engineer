import { describe, expect, it } from 'vitest'
import { buildSpeakerCalibrationRows, speakerCalibrationStatus } from './speakerCalibration.js'

const participants = [
  { id: 'user-priya', name: 'Priya Nair', kind: 'human' },
  { id: 'user-admin', name: 'Sam Ortiz', kind: 'human' },
  { id: 'agent-voiceops', name: 'VoiceOps', kind: 'agent' },
]

describe('speaker calibration helpers', () => {
  it('marks high-confidence unknown speakers as needing assignment', () => {
    const status = speakerCalibrationStatus({ confidence: 0.82 })

    expect(status).toEqual({ label: 'Needs assignment', tone: 'needs' })
  })

  it('asks for confirmation on low-confidence unknown speakers', () => {
    const status = speakerCalibrationStatus({ confidence: 0.62 })

    expect(status).toEqual({ label: 'Confirm speaker', tone: 'confirm' })
  })

  it('builds rows with human-only assignment options', () => {
    const rows = buildSpeakerCalibrationRows(
      {
        mappings: [],
        unknown_speakers: [
          { speaker_label: 'SPEAKER_00', confidence: 0.69, text: 'Can you fix app.py?' },
        ],
      },
      participants,
    )

    expect(rows).toHaveLength(1)
    expect(rows[0]).toMatchObject({
      speakerLabel: 'SPEAKER_00',
      preview: 'Can you fix app.py?',
      status: { label: 'Confirm speaker', tone: 'confirm' },
    })
    expect(rows[0].assignableParticipants.map((p) => p.id)).toEqual(['user-priya', 'user-admin'])
  })

  it('places unresolved labels before assigned labels', () => {
    const rows = buildSpeakerCalibrationRows(
      {
        mappings: [
          { speaker_label: 'SPEAKER_01', user_id: 'user-admin', user_name: 'Sam Ortiz', source: 'manual' },
        ],
        unknown_speakers: [
          { speaker_label: 'SPEAKER_00', confidence: 0.8, text: 'Unknown turn' },
          { speaker_label: 'SPEAKER_01', confidence: 0.9, text: 'Mapped turn' },
        ],
      },
      participants,
    )

    expect(rows.map((row) => row.speakerLabel)).toEqual(['SPEAKER_00', 'SPEAKER_01'])
    expect(rows[1].status).toEqual({ label: 'Assigned', tone: 'assigned' })
    expect(rows[1].preview).toBe('Mapped turn')
  })
})
