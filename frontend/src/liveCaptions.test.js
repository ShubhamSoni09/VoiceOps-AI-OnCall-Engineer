import { describe, expect, it } from 'vitest'
import {
  CAPTION_STATE,
  captionStatusClass,
  captionStatusLabel,
  compactCaptionText,
} from './liveCaptions.js'

describe('live caption helpers', () => {
  it('normalizes whitespace and keeps short captions intact', () => {
    expect(compactCaptionText('  Alice   said   fix app.py  ')).toBe('Alice said fix app.py')
  })

  it('keeps the latest caption text when trimming long browser previews', () => {
    const text = `old words ${'x'.repeat(40)} latest words`

    expect(compactCaptionText(text, 18)).toBe('...xxxxx latest words')
  })

  it('labels active captions as browser preview instead of final attribution', () => {
    expect(captionStatusLabel(CAPTION_STATE.ACTIVE, true)).toBe('browser preview')
    expect(captionStatusClass(CAPTION_STATE.ACTIVE, true)).toBe('ok')
  })

  it('marks unsupported or paused captions as warning states', () => {
    expect(captionStatusLabel(CAPTION_STATE.ACTIVE, false)).toBe('unavailable')
    expect(captionStatusClass(CAPTION_STATE.PAUSED, true)).toBe('warn')
    expect(captionStatusClass(CAPTION_STATE.UNAVAILABLE, true)).toBe('warn')
  })
})
