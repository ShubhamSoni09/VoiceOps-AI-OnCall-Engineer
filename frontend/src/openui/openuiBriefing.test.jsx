import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { WORKSPACE_BRIEFING } from './sample.js'
import { voiceopsSystemPrompt } from './library.jsx'

describe('OpenUI workspace briefing', () => {
  it('uses collaborative coding room language instead of incident response copy', () => {
    const librarySource = readFileSync(new URL('./library.jsx', import.meta.url), 'utf8')

    expect(WORKSPACE_BRIEFING).toContain('Meeting closure for app.py')
    expect(WORKSPACE_BRIEFING).toContain('Pending patches')
    expect(WORKSPACE_BRIEFING).not.toContain('checkout-api')
    expect(WORKSPACE_BRIEFING).not.toContain('deploy preview')

    expect(voiceopsSystemPrompt).toContain('AI teammate')
    expect(voiceopsSystemPrompt).toContain('collaborative coding room')
    expect(voiceopsSystemPrompt).not.toContain('on-call engineer')
    expect(voiceopsSystemPrompt).not.toContain('incident analysis')

    expect(librarySource).toContain('workspace briefing')
    expect(librarySource).not.toContain('incident briefing')
  })
})
