export const CAPTION_STATE = {
  IDLE: 'idle',
  STARTING: 'starting',
  ACTIVE: 'active',
  PAUSED: 'paused',
  UNAVAILABLE: 'unavailable',
  STOPPED: 'stopped',
}

export function compactCaptionText(value, maxLength = 600) {
  const normalized = String(value || '').replace(/\s+/g, ' ').trim()
  if (!normalized) return ''
  if (normalized.length <= maxLength) return normalized
  return `...${normalized.slice(-maxLength)}`
}

export function captionStatusLabel(state, supported = true) {
  if (!supported || state === CAPTION_STATE.UNAVAILABLE) return 'unavailable'
  if (state === CAPTION_STATE.ACTIVE) return 'browser preview'
  if (state === CAPTION_STATE.STARTING) return 'starting'
  if (state === CAPTION_STATE.PAUSED) return 'paused'
  if (state === CAPTION_STATE.STOPPED) return 'stopped'
  return 'idle'
}

export function captionStatusClass(state, supported = true) {
  if (!supported || state === CAPTION_STATE.UNAVAILABLE || state === CAPTION_STATE.PAUSED) return 'warn'
  if (state === CAPTION_STATE.ACTIVE) return 'ok'
  return ''
}
