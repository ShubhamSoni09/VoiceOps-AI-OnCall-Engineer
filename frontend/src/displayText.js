import { APP_NAME } from './branding.js'

export function displayText(value) {
  return String(value || '')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/\*([^*]+)\*/g, '$1')
    .replace(/__([^_]+)__/g, '$1')
    .replace(/_([^_]+)_/g, '$1')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/\s+/g, ' ')
    .trim()
}

export function compactDisplayText(value) {
  return displayText(value)
    .replace(
      /\b(I investigated|I checked|Checked|Investigated)\s+([A-Za-z0-9._/-]+)\s+in the\s+\2\s+workspace\b/gi,
      (_match, verb, name) => `${verb} the ${name} workspace`,
    )
    .replace(
      /\bI investigated the ([A-Za-z0-9._/-]+) workspace\. Key files:\s*(.+?)\.\s*All\s+\d+\s+tests?\s+pass(?:ed)?\./gi,
      'Checked $1 · files: $2 · tests pass.',
    )
    .replace(
      /\bI investigated the ([A-Za-z0-9._/-]+) workspace\. Key files:\s*(.+?)\.(?=\s*$)/gi,
      'Checked $1 · files: $2.',
    )
    .replace(
      /\bI investigated the ([A-Za-z0-9._/-]+) workspace\.\s*All\s+\d+\s+tests?\s+pass(?:ed)?\./gi,
      'Checked $1 · tests pass.',
    )
    .replace(/\basked\s+(.+?)\s+to status\b/gi, 'asked $1 for status')
}

export function displayActorName(value, agentName = APP_NAME, fallback = '') {
  const name = displayText(value)
  if (!name) return fallback
  return name.toLowerCase() === APP_NAME.toLowerCase() ? agentName : name
}
