export const LOW_SPEAKER_CONFIDENCE = 0.7

export function pct(value) {
  return typeof value === 'number' ? `${Math.round(value * 100)}%` : 'unknown'
}

export function speakerCalibrationStatus(speaker) {
  if (speaker?.mappedUserName) {
    return { label: 'Assigned', tone: 'assigned' }
  }
  if (typeof speaker?.confidence === 'number' && speaker.confidence < LOW_SPEAKER_CONFIDENCE) {
    return { label: 'Confirm speaker', tone: 'confirm' }
  }
  return { label: 'Needs assignment', tone: 'needs' }
}

export function buildSpeakerCalibrationRows(speakers, participants = []) {
  const humans = participants.filter((p) => p.kind !== 'agent')
  const mappings = speakers?.mappings || []
  const unknown = speakers?.unknown_speakers || []
  const latestUnknown = new Map()

  unknown.forEach((speaker) => {
    latestUnknown.set(speaker.speaker_label, speaker)
  })

  const assigned = mappings.map((mapping) => {
    const row = {
      speakerLabel: mapping.speaker_label,
      mappedUserId: mapping.user_id,
      mappedUserName: mapping.user_name,
      source: mapping.source,
      confidence: mapping.confidence,
      preview: latestUnknown.get(mapping.speaker_label)?.text || '',
      assignableParticipants: humans,
    }
    return { ...row, status: speakerCalibrationStatus(row) }
  })

  const assignedLabels = new Set(mappings.map((mapping) => mapping.speaker_label))
  const unresolved = unknown
    .filter((speaker) => !assignedLabels.has(speaker.speaker_label))
    .map((speaker) => {
      const row = {
        speakerLabel: speaker.speaker_label,
        mappedUserId: null,
        mappedUserName: null,
        source: null,
        confidence: speaker.confidence,
        preview: speaker.text || '',
        assignableParticipants: humans,
      }
      return { ...row, status: speakerCalibrationStatus(row) }
    })

  return [...unresolved, ...assigned]
}
