import { describe, expect, it } from 'vitest'
import { microphoneDiagnostics, microphoneErrorMessage, microphoneRecoveryHint } from './microphoneErrors.js'

describe('microphone error messages', () => {
  it('explains blocked browser microphone permission', () => {
    expect(microphoneErrorMessage({ name: 'NotAllowedError' })).toContain('permission is blocked')
  })

  it('explains missing microphone hardware', () => {
    expect(microphoneErrorMessage({ name: 'NotFoundError' })).toContain('No microphone was found')
  })

  it('explains busy macOS microphone devices', () => {
    expect(microphoneErrorMessage({ name: 'NotReadableError' })).toContain('busy or blocked by macOS')
  })

  it('explains browser audio constraint failures as input selection issues', () => {
    expect(microphoneErrorMessage({ name: 'OverconstrainedError' }))
      .toContain('selected microphone input')
    expect(microphoneRecoveryHint({ name: 'OverconstrainedError' }))
      .toContain('Select a different microphone input')
    expect(microphoneDiagnostics({ name: 'OverconstrainedError' })).toMatchObject({
      kind: 'OverconstrainedError',
      deviceUnavailable: true,
      permissionBlocked: false,
    })
  })

  it('keeps speech recognition service failures distinct from microphone hardware failures', () => {
    expect(microphoneErrorMessage({ error: 'network' }, 'speech')).toContain('speech recognition service')
    expect(microphoneErrorMessage({ error: 'service-not-allowed' }, 'speech')).toContain('speech recognition is blocked')
  })

  it('returns diagnostics flags for permission and device failures', () => {
    expect(microphoneDiagnostics({ name: 'NotAllowedError' })).toMatchObject({
      kind: 'NotAllowedError',
      permissionBlocked: true,
      recoveryHint: expect.stringContaining('browser site settings'),
    })
    expect(microphoneRecoveryHint({ name: 'NotAllowedError' })).toContain('Type commands still work')
    expect(microphoneRecoveryHint({ name: 'NotAllowedError' })).toContain('Chrome or Safari')
    expect(microphoneDiagnostics({ name: 'NotReadableError' })).toMatchObject({
      kind: 'NotReadableError',
      deviceUnavailable: true,
      recoveryHint: expect.stringContaining('Close other recording apps'),
    })
  })

  it('gives actionable fallback steps for unsupported browser capture', () => {
    expect(microphoneErrorMessage({ name: 'NotSupportedError', message: 'Not supported' }))
      .toContain('Live audio capture is not supported')
    expect(microphoneRecoveryHint({ name: 'NotSupportedError' }))
      .toMatch(/typed commands/i)
    expect(microphoneRecoveryHint({ name: 'NotSupportedError' }))
      .toContain('this local app')
    expect(microphoneRecoveryHint({ name: 'NotSupportedError' }))
      .not.toContain('Live audio capture is not supported')
    expect(microphoneRecoveryHint({ name: 'NotSupportedError' }))
      .toContain('For voice, open this local app')
    expect(microphoneRecoveryHint({ code: 'media-recorder-unavailable' }))
      .toContain('copy this link')
    expect(microphoneRecoveryHint({ code: 'media-recorder-unavailable' }))
      .not.toContain('127.0.0.1:5174')
    expect(microphoneRecoveryHint({ code: 'media-recorder-unavailable' }))
      .not.toContain('MediaRecorder')
    expect(microphoneRecoveryHint({ code: 'get-user-media-unavailable' }))
      .toContain('copy this link')
    expect(microphoneRecoveryHint({ code: 'get-user-media-unavailable' }))
      .not.toContain('127.0.0.1:5174')
    expect(microphoneRecoveryHint({ code: 'get-user-media-unavailable' }))
      .not.toContain('browser context')
  })

  it('explains stalled microphone permission prompts', () => {
    expect(microphoneErrorMessage({ name: 'microphone-timeout' }))
      .toContain('permission did not complete')
    expect(microphoneRecoveryHint({ code: 'microphone-timeout' }))
      .toContain('Approve microphone access')
  })
})
