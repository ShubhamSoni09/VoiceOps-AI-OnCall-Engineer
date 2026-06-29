export function clockFromIso(value) {
  if (!value) return ''
  return new Date(value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

export function messagesFromRoom(snapshot) {
  return (snapshot?.messages || []).map((m) => ({
    id: m.id,
    role: m.role === 'agent' ? 'agent' : m.role === 'system' ? 'system' : 'user',
    name: m.actor_name,
    role2: m.role === 'agent'
      ? 'AI teammate'
      : m.role === 'system'
        ? 'audit'
        : m.speaker_label
          ? (m.metadata?.identified_user_id ? 'identified speaker' : 'unassigned speaker')
          : m.source === 'audio'
            ? 'voice'
            : 'teammate',
    when: clockFromIso(m.created_at),
    createdAt: m.created_at || '',
    text: m.text,
    source: m.source,
    metadata: m.metadata || {},
    fromVoice: m.source === 'audio' || m.source === 'voice' || m.source === 'meeting_audio',
    speakerLabel: m.speaker_label,
    confidence: m.confidence,
    identityConfidence: m.metadata?.identity_confidence,
    identitySource: m.metadata?.identity_source,
    liveTempId: m.metadata?.live_temp_id,
    codeReferences: m.metadata?.references || [],
    citations: m.metadata?.citations || [],
    ragRetrieval: m.metadata?.retrieval || null,
  }))
}

export function mergeLocalDrafts(roomMessages, previousMessages) {
  const finalizedTempIds = new Set(roomMessages.map((m) => m.liveTempId).filter(Boolean))
  const drafts = previousMessages.filter((m) => m.draft && m.tempId && !finalizedTempIds.has(m.tempId))
  return [...roomMessages, ...drafts]
}
