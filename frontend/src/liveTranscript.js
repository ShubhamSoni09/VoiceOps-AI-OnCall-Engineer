export const BROWSER_CAPTION_ID = 'live-browser-caption'

export function buildLivePartialMessage(evt, now) {
  return {
    id: liveMessageId(evt.temp_id),
    role: 'user',
    name: 'Live meeting',
    role2: 'provisional transcript',
    when: now,
    text: evt.text || '',
    fromVoice: true,
    speakerLabel: 'calibrating',
    tempId: evt.temp_id,
    draft: true,
    provisional: true,
  }
}

export function buildInstantCaptionMessage(text, now) {
  const trimmed = text?.trim()
  if (!trimmed) return null
  return {
    id: BROWSER_CAPTION_ID,
    role: 'user',
    name: 'Live captions',
    role2: 'instant transcript',
    when: now,
    text: trimmed,
    fromVoice: true,
    speakerLabel: 'local captions',
    tempId: 'browser-caption',
    draft: true,
    provisional: true,
  }
}

export function upsertLivePartial(messages, evt, now) {
  if (!evt?.temp_id || !evt.text) return messages
  const next = buildLivePartialMessage(evt, now)
  let found = false
  const updated = messages.map((message) => {
    if (message.id !== next.id) return message
    found = true
    if (!message.draft) return message
    return { ...message, ...next }
  })
  return found ? updated : [...messages, next]
}

export function upsertInstantCaption(messages, text, now) {
  const next = buildInstantCaptionMessage(text, now)
  if (!next) return messages
  let found = false
  const updated = messages.map((message) => {
    if (message.id !== BROWSER_CAPTION_ID) return message
    found = true
    return { ...message, ...next }
  })
  return found ? updated : [...messages, next]
}

export function removeInstantCaption(messages) {
  return messages.filter((message) => message.id !== BROWSER_CAPTION_ID)
}

export function nextCaptionHint(currentText, lastSentText = '') {
  const current = normalizeCaption(currentText)
  const lastSent = normalizeCaption(lastSentText)
  if (!current) return { debugText: null, nextSent: lastSent }
  if (current === lastSent) return { debugText: null, nextSent: lastSent }
  if (lastSent && current.startsWith(lastSent)) {
    const delta = current.slice(lastSent.length).trim()
    return { debugText: delta || null, nextSent: current }
  }
  return { debugText: current, nextSent: current }
}

export function buildLiveAudioPayload({
  sequence,
  mimeType,
  audioBase64,
  provider,
  captionText = '',
  lastCaptionSent = '',
}) {
  const payload = {
    type: 'audio_chunk',
    sequence,
    mime_type: mimeType,
    audio_base64: audioBase64,
  }
  let nextCaptionSent = normalizeCaption(lastCaptionSent)
  if (provider === 'mock') {
    const hint = nextCaptionHint(captionText, lastCaptionSent)
    nextCaptionSent = hint.nextSent
    if (hint.debugText) payload.debug_text = hint.debugText
  }
  return { payload, nextCaptionSent }
}

export function chooseSupportedAudioMimeType(mediaRecorder = globalThis.MediaRecorder) {
  const candidates = [
    'audio/webm;codecs=opus',
    'audio/webm',
    'audio/mp4;codecs=mp4a.40.2',
    'audio/mp4',
    'audio/mpeg',
    'audio/wav',
  ]
  if (!mediaRecorder || typeof mediaRecorder.isTypeSupported !== 'function') return ''
  return candidates.find((candidate) => mediaRecorder.isTypeSupported(candidate)) || ''
}

export function applySpeakerAttribution(messages, evt, now) {
  const segment = evt?.segment || evt?.segments?.[0]
  if (!segment || !evt?.temp_id) return messages
  const id = liveMessageId(evt.temp_id)
  const next = buildAttributedMessage(id, evt.temp_id, segment, now)
  const withoutCaption = removeInstantCaption(messages)
  let found = false
  const updated = withoutCaption.map((message) => {
    if (message.id !== id) return message
    found = true
    return {
      ...message,
      ...next,
      text: segment.text || message.text,
      when: message.when || now,
    }
  })
  return found ? updated : [...updated, next]
}

function buildAttributedMessage(id, tempId, segment, now) {
  const mappedName = segment.identified_user_name || null
  return {
    id,
    role: 'user',
    name: mappedName || 'Unknown speaker',
    role2: mappedName ? 'identified speaker' : 'speaker attribution',
    when: now,
    text: segment.text || '',
    fromVoice: true,
    speakerLabel: segment.speaker_label,
    confidence: segment.confidence,
    identityConfidence: segment.identity_confidence,
    identitySource: segment.identity_source || segment.metadata?.identity_source,
    tempId,
    draft: false,
    provisional: false,
    corrected: true,
  }
}

function liveMessageId(tempId) {
  return `live-${tempId}`
}

function normalizeCaption(value) {
  return String(value || '').replace(/\s+/g, ' ').trim()
}
