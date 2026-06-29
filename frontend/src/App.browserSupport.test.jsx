import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  browserSupportDiagnostic,
  getUserMediaWithTimeout,
  liveStatusDisplayMessage,
  microphoneFailureDiagnosticsUpdate,
  microphonePermissionDiagnostic,
  speechUnavailableFallback,
  userMessageRoleLabel,
  voiceCaptureState,
} from './appVoiceSupport.js'

function stubBrowserSupport({ mediaRecorder = true, getUserMedia = true, fetch = true, permissionState } = {}) {
  vi.stubGlobal('window', {
    MediaRecorder: mediaRecorder ? function MediaRecorder() {} : undefined,
    SpeechRecognition: undefined,
    webkitSpeechRecognition: undefined,
    isSecureContext: true,
    location: { search: '' },
  })
  vi.stubGlobal('navigator', {
    mediaDevices: getUserMedia ? { getUserMedia: vi.fn() } : undefined,
    permissions: permissionState
      ? { query: vi.fn().mockResolvedValue({ state: permissionState }) }
      : undefined,
    languages: ['en-US'],
    language: 'en-US',
  })
  vi.stubGlobal('fetch', fetch ? vi.fn() : undefined)
}

describe('browser support diagnostics', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('reports MediaRecorder support gaps immediately', () => {
    stubBrowserSupport({ mediaRecorder: false, getUserMedia: true })

    const diagnostic = browserSupportDiagnostic()

    expect(diagnostic.lastErrorKind).toBe('media-recorder-unavailable')
    expect(diagnostic.lastErrorMessage).toContain('cannot record live audio')
    expect(diagnostic.recoveryHint).toContain('copy this link')
    expect(diagnostic.lastErrorMessage).not.toContain('MediaRecorder')
  })

  it('reports microphone API support gaps immediately', () => {
    stubBrowserSupport({ mediaRecorder: true, getUserMedia: false })

    const diagnostic = browserSupportDiagnostic()

    expect(diagnostic.lastErrorKind).toBe('get-user-media-unavailable')
    expect(diagnostic.lastErrorMessage).toContain('cannot use the microphone')
    expect(diagnostic.recoveryHint).toContain('copy this link')
    expect(diagnostic.lastErrorMessage).not.toContain('browser microphone API')
  })

  it('prioritizes missing microphone capture when the embedded browser exposes no audio APIs', () => {
    stubBrowserSupport({ mediaRecorder: false, getUserMedia: false })

    const diagnostic = browserSupportDiagnostic()

    expect(diagnostic.lastErrorKind).toBe('get-user-media-unavailable')
    expect(diagnostic.lastErrorMessage).toContain('cannot use the microphone')
    expect(diagnostic.recoveryHint).toContain('copy this link')
    expect(diagnostic.recoveryHint).not.toContain('browser context')
  })

  it('handles embedded browser contexts with no navigator object', () => {
    vi.stubGlobal('window', {
      MediaRecorder: function MediaRecorder() {},
      SpeechRecognition: undefined,
      webkitSpeechRecognition: undefined,
      isSecureContext: true,
      location: { search: '' },
    })
    vi.stubGlobal('navigator', undefined)
    vi.stubGlobal('fetch', vi.fn())

    const diagnostic = browserSupportDiagnostic()

    expect(diagnostic.lastErrorKind).toBe('get-user-media-unavailable')
    expect(diagnostic.lastErrorMessage).toContain('cannot use the microphone')
    expect(diagnostic.recoveryHint).toContain('Typed commands')
  })

  it('reports browser API runtime gaps immediately', () => {
    stubBrowserSupport({ mediaRecorder: true, getUserMedia: true, fetch: false })

    const diagnostic = browserSupportDiagnostic()

    expect(diagnostic.lastErrorKind).toBe('api-runtime-unavailable')
    expect(diagnostic.lastErrorMessage).toContain('fetch support')
    expect(diagnostic.recoveryHint).toContain('Chrome')
  })

  it('reads denied microphone permission without requesting audio capture', async () => {
    stubBrowserSupport({ permissionState: 'denied' })

    const diagnostic = await microphonePermissionDiagnostic()

    expect(navigator.permissions.query).toHaveBeenCalledWith({ name: 'microphone' })
    expect(navigator.mediaDevices.getUserMedia).not.toHaveBeenCalled()
    expect(diagnostic.microphonePermission).toBe('denied')
    expect(diagnostic.lastErrorKind).toBe('NotAllowedError')
    expect(diagnostic.lastErrorMessage).toContain('permission is blocked')
    expect(diagnostic.recoveryHint).toContain('browser site settings')
  })

  it('keeps browser capability gaps during permission diagnostics', async () => {
    stubBrowserSupport({ mediaRecorder: false, getUserMedia: false })

    const diagnostic = await microphonePermissionDiagnostic()

    expect(diagnostic.microphonePermission).toBe('unknown')
    expect(diagnostic.lastErrorKind).toBe('get-user-media-unavailable')
    expect(diagnostic.lastErrorMessage).toContain('cannot use the microphone')
    expect(diagnostic.recoveryHint).toContain('copy this link')
  })

  it('keeps granted microphone permission as a non-blocking diagnostic', async () => {
    stubBrowserSupport({ permissionState: 'granted' })

    const diagnostic = await microphonePermissionDiagnostic()

    expect(diagnostic.microphonePermission).toBe('granted')
    expect(diagnostic.lastErrorKind).toBeUndefined()
  })

  it('keeps diagnostics quiet when browser voice support is present', () => {
    stubBrowserSupport()

    const diagnostic = browserSupportDiagnostic()

    expect(diagnostic.getUserMedia).toBe(true)
    expect(diagnostic.mediaRecorder).toBe(true)
    expect(diagnostic.lastErrorKind).toBeUndefined()
  })

  it('does not turn API runtime failures into microphone warnings', () => {
    expect(voiceCaptureState({
      lastErrorKind: 'api-runtime-unavailable',
      lastErrorMessage: 'Console API calls need browser fetch support.',
      recoveryHint: 'Open the app in Chrome.',
      getUserMedia: true,
      mediaRecorder: true,
    })).toEqual({
      unavailable: false,
      message: '',
      recoveryHint: '',
      retryable: true,
    })
  })

  it('keeps real microphone failures visible in the command bar', () => {
    expect(voiceCaptureState({
      lastErrorKind: 'NotAllowedError',
      lastErrorMessage: 'Microphone permission is blocked.',
      recoveryHint: 'Allow microphone access.',
      getUserMedia: true,
      mediaRecorder: true,
    })).toEqual({
      unavailable: true,
      message: 'Microphone permission is blocked.',
      recoveryHint: 'Allow microphone access.',
      retryable: true,
    })
  })

  it('surfaces denied microphone permission even before capture is attempted', () => {
    expect(voiceCaptureState({
      microphonePermission: 'denied',
      lastErrorKind: '',
      lastErrorMessage: '',
      recoveryHint: '',
      getUserMedia: true,
      mediaRecorder: true,
    })).toEqual({
      unavailable: true,
      message: 'Microphone permission is blocked. Allow microphone access for this browser, then try again.',
      recoveryHint: expect.stringContaining('browser site settings'),
      retryable: true,
    })
  })

  it('does not mark the browser microphone API unavailable after runtime permission failures', () => {
    expect(microphoneFailureDiagnosticsUpdate(
      {
        kind: 'NotAllowedError',
        message: 'Microphone permission is blocked.',
        recoveryHint: 'Allow microphone access.',
      },
      'microphone_error',
      { getUserMedia: true },
    )).toMatchObject({
      getUserMedia: true,
      active: false,
      phase: 'idle',
      stage: 'microphone_error',
      lastEvent: 'microphone_NotAllowedError',
      lastErrorKind: 'NotAllowedError',
      lastErrorMessage: 'Microphone permission is blocked.',
    })
  })

  it('marks browser capability gaps as non-retryable from the command bar', () => {
    expect(voiceCaptureState({
      lastErrorKind: 'get-user-media-unavailable',
      lastErrorMessage: 'This browser cannot use the microphone. Type a command below.',
      recoveryHint: 'Open the console in Chrome or Safari for voice.',
      getUserMedia: false,
      mediaRecorder: true,
    })).toEqual({
      unavailable: true,
      message: 'This browser cannot use the microphone. Type a command below.',
      recoveryHint: 'Open the console in Chrome or Safari for voice.',
      retryable: false,
    })
  })

  it('does not offer retry when the browser session does not support live audio capture', () => {
    expect(voiceCaptureState({
      lastErrorKind: 'NotSupportedError',
      lastErrorMessage: 'Live audio capture is not supported in this browser session.',
      recoveryHint: 'Typed commands stay enabled here. For voice, open Chrome or Safari.',
      getUserMedia: true,
      mediaRecorder: true,
    })).toEqual({
      unavailable: true,
      message: 'Live audio capture is not supported in this browser session.',
      recoveryHint: 'Typed commands stay enabled here. For voice, open Chrome or Safari.',
      retryable: false,
    })
  })

  it('does not offer retry for insecure microphone capture contexts', () => {
    expect(voiceCaptureState({
      lastErrorKind: 'SecurityError',
      lastErrorMessage: 'Microphone capture requires a secure local page. Open the app on http://127.0.0.1 or localhost.',
      recoveryHint: 'Open the app on http://127.0.0.1 or localhost, then retry microphone capture.',
      getUserMedia: true,
      mediaRecorder: true,
    })).toEqual({
      unavailable: true,
      message: 'Microphone capture requires a secure local page. Open the app on http://127.0.0.1 or localhost.',
      recoveryHint: 'Open the app on http://127.0.0.1 or localhost, then retry microphone capture.',
      retryable: false,
    })
  })

  it('treats raw browser capability flags as non-retryable even without an error kind', () => {
    expect(voiceCaptureState({
      getUserMedia: false,
      mediaRecorder: true,
      lastErrorKind: '',
      lastErrorMessage: '',
      recoveryHint: '',
    })).toEqual({
      unavailable: true,
      message: 'Microphone capture is unavailable in this browser.',
      recoveryHint: expect.stringContaining('copy this link'),
      retryable: false,
    })

    expect(voiceCaptureState({
      getUserMedia: true,
      mediaRecorder: false,
      lastErrorKind: '',
      lastErrorMessage: '',
      recoveryHint: '',
    })).toEqual({
      unavailable: true,
      message: 'Live meeting recording is unavailable in this browser.',
      recoveryHint: expect.stringContaining('copy this link'),
      retryable: false,
    })
  })

  it('routes mic clicks to Live meeting when speech recognition is unavailable but recording is supported', () => {
    expect(speechUnavailableFallback({
      mediaRecorder: true,
      getUserMedia: true,
    })).toEqual({
      startLiveMeeting: true,
      message: 'Browser speech recognition is unavailable. Starting Live meeting instead.',
      code: '',
      recoveryHint: '',
    })
  })

  it('keeps live command bar status user-facing while preserving provider messages', () => {
    expect(liveStatusDisplayMessage({
      type: 'session_status',
      state: 'listening',
      message: 'Live meeting socket connected',
    })).toBe('Streaming room audio')

    expect(liveStatusDisplayMessage({
      type: 'session_status',
      state: 'degraded',
      stage: 'socket_error',
      message: 'WebSocket closed unexpectedly',
    })).toBe('Live meeting connection interrupted')

    expect(liveStatusDisplayMessage({
      type: 'session_status',
      state: 'degraded',
      message: 'CPU mode may lag',
    })).toBe('CPU mode may lag')
  })

  it('keeps mic fallback explicit when browser recording is unavailable too', () => {
    expect(speechUnavailableFallback({
      mediaRecorder: true,
      getUserMedia: false,
    })).toMatchObject({
      startLiveMeeting: false,
      code: 'get-user-media-unavailable',
      message: expect.stringContaining('cannot use the microphone'),
      recoveryHint: expect.stringContaining('copy this link'),
    })
  })

  it('keeps mic fallback focused on capture when all browser audio APIs are hidden', () => {
    expect(speechUnavailableFallback({
      mediaRecorder: false,
      getUserMedia: false,
    })).toMatchObject({
      startLiveMeeting: false,
      code: 'get-user-media-unavailable',
      message: expect.stringContaining('cannot use the microphone'),
      recoveryHint: expect.stringContaining('copy this link'),
    })
  })

  it('does not keep a stale microphone warning while live capture is active', () => {
    expect(voiceCaptureState({
      active: true,
      lastErrorKind: 'NotAllowedError',
      lastErrorMessage: 'Microphone permission is blocked.',
      recoveryHint: 'Allow microphone access.',
      getUserMedia: true,
      mediaRecorder: true,
    })).toEqual({
      unavailable: false,
      message: '',
      recoveryHint: '',
      retryable: true,
    })
  })

  it('uses teammate language for user messages when no role label is configured', () => {
    expect(userMessageRoleLabel(null)).toBe('teammate')
    expect(userMessageRoleLabel({ role_label: 'On-call engineer' })).toBe('teammate')
    expect(userMessageRoleLabel({ role_label: 'reviewer' })).toBe('reviewer')
  })

  it('times out stalled microphone permission requests', async () => {
    vi.useFakeTimers()
    const mediaDevices = {
      getUserMedia: vi.fn(() => new Promise(() => {})),
    }

    const request = getUserMediaWithTimeout(mediaDevices, { audio: true }, 100)
    const assertion = expect(request).rejects.toMatchObject({
      name: 'microphone-timeout',
      code: 'microphone-timeout',
    })
    await vi.advanceTimersByTimeAsync(100)

    await assertion
  })

  it('keeps successful microphone streams open before timeout', async () => {
    vi.useFakeTimers()
    const stop = vi.fn()
    const stream = {
      getTracks: () => [{ stop }],
    }
    const mediaDevices = {
      getUserMedia: vi.fn(async () => stream),
    }

    await expect(getUserMediaWithTimeout(mediaDevices, { audio: true }, 100)).resolves.toBe(stream)

    expect(stop).not.toHaveBeenCalled()
  })

  it('stops a microphone stream if it resolves after the timeout rejected', async () => {
    vi.useFakeTimers()
    let resolveCapture
    const stop = vi.fn()
    const stream = {
      getTracks: () => [{ stop }],
    }
    const mediaDevices = {
      getUserMedia: vi.fn(() => new Promise((resolve) => {
        resolveCapture = resolve
      })),
    }

    const request = getUserMediaWithTimeout(mediaDevices, { audio: true }, 100)
    const assertion = expect(request).rejects.toMatchObject({
      name: 'microphone-timeout',
    })
    await vi.advanceTimersByTimeAsync(100)
    await assertion

    resolveCapture(stream)
    await Promise.resolve()

    expect(stop).toHaveBeenCalledTimes(1)
  })
})
