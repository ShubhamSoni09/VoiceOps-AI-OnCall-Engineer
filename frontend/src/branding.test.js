import { describe, expect, it } from 'vitest'
import { APP_CONSOLE_TITLE } from './branding.js'

describe('branding', () => {
  it('uses team console language for the product shell', () => {
    expect(APP_CONSOLE_TITLE).toContain('Team Console')
    expect(APP_CONSOLE_TITLE).not.toContain('Incident')
  })
})
