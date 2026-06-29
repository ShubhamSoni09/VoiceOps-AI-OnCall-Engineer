import { CAPTION_STATE } from './liveCaptions.js'
import {
  resetLiveLatencyDiagnostics,
  resetLiveQueueDiagnostics,
} from './liveDiagnostics.js'
import { microphoneRecoveryHint } from './microphoneErrors.js'
import { teammateRoleLabel } from './roleLabels.js'

export function speechLanguage() {
  const nav = typeof navigator !== 'undefined' ? navigator : null
  return nav?.languages?.[0] || nav?.language || 'en-US'
}

export function browserLiveSupport() {
  const params = typeof window !== 'undefined' ? new URLSearchParams(window.location.search) : null
  return {
    mediaRecorder: typeof window !== 'undefined' && Boolean(window.MediaRecorder),
    mediaDevices: typeof navigator !== 'undefined' && Boolean(navigator.mediaDevices),
    getUserMedia: typeof navigator !== 'undefined' && Boolean(navigator.mediaDevices?.getUserMedia),
    permissionsApi: typeof navigator !== 'undefined' && Boolean(navigator.permissions?.query),
    microphonePermission: 'unknown',
    instantCaptions: typeof window !== 'undefined' && Boolean(window.SpeechRecognition || window.webkitSpeechRecognition),
    secureContext: typeof window !== 'undefined' ? Boolean(window.isSecureContext) : false,
    smokeMode: Boolean(params?.has('live_smoke') || params?.has('mock_mic_e2e') || params?.has('real_mic_e2e')),
  }
}

export async function microphonePermissionDiagnostic(support = browserLiveSupport()) {
  if (!support.getUserMedia) {
    return {
      ...support,
      microphonePermission: 'unknown',
      lastEvent: 'get-user-media-unavailable',
      lastMessage: 'This browser cannot use the microphone. Type a command below.',
      lastErrorKind: 'get-user-media-unavailable',
      lastErrorMessage: 'This browser cannot use the microphone. Type a command below.',
      recoveryHint: microphoneRecoveryHint({ code: 'get-user-media-unavailable' }),
    }
  }
  if (!support.mediaRecorder) {
    return {
      ...support,
      microphonePermission: 'unknown',
      lastEvent: 'media-recorder-unavailable',
      lastMessage: 'This browser cannot record live audio. Type a command below.',
      lastErrorKind: 'media-recorder-unavailable',
      lastErrorMessage: 'This browser cannot record live audio. Type a command below.',
      recoveryHint: microphoneRecoveryHint({ code: 'media-recorder-unavailable' }),
    }
  }
  if (!support.permissionsApi || typeof navigator === 'undefined') {
    return {
      ...support,
      microphonePermission: 'unknown',
    }
  }
  try {
    const permission = await navigator.permissions.query({ name: 'microphone' })
    const state = permission?.state || 'unknown'
    if (state === 'denied') {
      return {
        ...support,
        microphonePermission: state,
        lastEvent: 'microphone_permission_denied',
        lastMessage: 'Microphone permission is blocked. Allow microphone access for this browser, then try again.',
        lastErrorKind: 'NotAllowedError',
        lastErrorMessage: 'Microphone permission is blocked. Allow microphone access for this browser, then try again.',
        recoveryHint: microphoneRecoveryHint({ name: 'NotAllowedError' }),
      }
    }
    return {
      ...support,
      microphonePermission: state,
    }
  } catch {
    return {
      ...support,
      microphonePermission: 'unknown',
    }
  }
}

export function liveRecorderTimesliceMs() {
  if (typeof window === 'undefined') return 2000
  const value = Number(new URLSearchParams(window.location.search).get('live_timeslice_ms') || 2000)
  if (!Number.isFinite(value)) return 2000
  return Math.max(500, Math.min(12000, Math.round(value)))
}

export function getUserMediaWithTimeout(mediaDevices, constraints, timeoutMs = 10000) {
  if (!mediaDevices?.getUserMedia) {
    const error = new Error('Microphone capture API is unavailable.')
    error.name = 'get-user-media-unavailable'
    error.code = 'get-user-media-unavailable'
    return Promise.reject(error)
  }
  let timer
  let timedOut = false
  const timerHost = typeof window !== 'undefined' ? window : globalThis
  const captureRequest = mediaDevices.getUserMedia(constraints)
    .then((stream) => {
      if (timedOut) {
        stream?.getTracks?.().forEach((track) => {
          try {
            track.stop()
          } catch {
            // Track can already be ended if the browser revoked capture.
          }
        })
      }
      return stream
    })
  const timeout = new Promise((_, reject) => {
    timer = timerHost.setTimeout(() => {
      timedOut = true
      const error = new Error('Microphone permission did not complete before the timeout.')
      error.name = 'microphone-timeout'
      error.code = 'microphone-timeout'
      reject(error)
    }, timeoutMs)
  })
  return Promise.race([captureRequest, timeout])
    .finally(() => timerHost.clearTimeout(timer))
}

export function initialLiveDiagnostics() {
  return {
    ...browserLiveSupport(),
    active: false,
    phase: 'idle',
    chunksSent: 0,
    ...resetLiveQueueDiagnostics(),
    ...resetLiveLatencyDiagnostics(),
    maxChunkKb: 0,
    backendBusy: false,
    stage: 'idle',
    provider: 'local',
    captionState: browserLiveSupport().instantCaptions ? CAPTION_STATE.IDLE : CAPTION_STATE.UNAVAILABLE,
    captionPreview: '',
    model: '',
    device: '',
    computeType: '',
    cpuMode: false,
    warmupRecommended: false,
    warmupHint: '',
    speakerVerificationStatus: '',
    speakerVerificationReady: false,
    speakerVerificationCount: 0,
    speakerVerificationLabels: [],
    speakerVerificationQuality: '',
    speakerVerificationDetail: '',
    stageEvents: [],
    stageTotalMs: null,
    lastEvent: 'idle',
    lastMessage: 'Not started',
    lastErrorKind: '',
    lastErrorMessage: '',
    recoveryHint: '',
  }
}

export function browserSupportDiagnostic() {
  const support = browserLiveSupport()
  if (typeof globalThis === 'undefined' || typeof globalThis.fetch !== 'function') {
    return {
      ...support,
      lastEvent: 'api-runtime-unavailable',
      lastMessage: 'Console API calls need browser fetch support. Open this app in a modern browser.',
      lastErrorKind: 'api-runtime-unavailable',
      lastErrorMessage: 'Console API calls need browser fetch support. Open this app in a modern browser.',
      recoveryHint: 'Open the app in Chrome, Safari, or Edge, then refresh the local console.',
    }
  }
  if (!support.getUserMedia) {
    return {
      ...support,
      lastEvent: 'get-user-media-unavailable',
      lastMessage: 'This browser cannot use the microphone. Type a command below.',
      lastErrorKind: 'get-user-media-unavailable',
      lastErrorMessage: 'This browser cannot use the microphone. Type a command below.',
      recoveryHint: microphoneRecoveryHint({ code: 'get-user-media-unavailable' }),
    }
  }
  if (!support.mediaRecorder) {
    return {
      ...support,
      lastEvent: 'media-recorder-unavailable',
      lastMessage: 'This browser cannot record live audio. Type a command below.',
      lastErrorKind: 'media-recorder-unavailable',
      lastErrorMessage: 'This browser cannot record live audio. Type a command below.',
      recoveryHint: microphoneRecoveryHint({ code: 'media-recorder-unavailable' }),
    }
  }
  return support
}

export function voiceCaptureState(liveDiagnostics = {}) {
  const kind = liveDiagnostics.lastErrorKind || ''
  const apiRuntimeOnly = kind === 'api-runtime-unavailable'
  const capabilityUnavailable = liveDiagnostics.getUserMedia === false || liveDiagnostics.mediaRecorder === false
  const capabilityCode = liveDiagnostics.getUserMedia === false
    ? 'get-user-media-unavailable'
    : liveDiagnostics.mediaRecorder === false
      ? 'media-recorder-unavailable'
      : ''
  const permissionDenied = liveDiagnostics.microphonePermission === 'denied'
  const nonRetryableKinds = new Set([
    'get-user-media-unavailable',
    'media-recorder-unavailable',
    'NotSupportedError',
    'SecurityError',
  ])
  const unavailable = Boolean(
    !apiRuntimeOnly
    && !liveDiagnostics.active
    && (
      kind
      || capabilityUnavailable
      || permissionDenied
    ),
  )
  const message = unavailable
    ? liveDiagnostics.lastErrorMessage
      || (permissionDenied
        ? 'Microphone permission is blocked. Allow microphone access for this browser, then try again.'
        : '')
      || (liveDiagnostics.getUserMedia === false
        ? 'Microphone capture is unavailable in this browser.'
        : liveDiagnostics.mediaRecorder === false
          ? 'Live meeting recording is unavailable in this browser.'
          : '')
    : ''
  return {
    unavailable,
    message,
    recoveryHint: unavailable
      ? liveDiagnostics.recoveryHint
        || (permissionDenied ? microphoneRecoveryHint({ name: 'NotAllowedError' }) : '')
        || (capabilityCode ? microphoneRecoveryHint({ code: capabilityCode }) : '')
      : '',
    retryable: unavailable ? !capabilityUnavailable && !nonRetryableKinds.has(kind) : true,
  }
}

export function speechUnavailableFallback(support = browserLiveSupport()) {
  if (support.mediaRecorder && support.getUserMedia) {
    return {
      startLiveMeeting: true,
      message: 'Browser speech recognition is unavailable. Starting Live meeting instead.',
      code: '',
      recoveryHint: '',
    }
  }
  const code = !support.getUserMedia ? 'get-user-media-unavailable' : 'media-recorder-unavailable'
  const message = !support.getUserMedia
    ? 'This browser cannot use the microphone. Type a command below.'
    : 'This browser cannot record live audio. Type a command below.'
  return {
    startLiveMeeting: false,
    code,
    message,
    recoveryHint: microphoneRecoveryHint({ code }),
  }
}

export function liveStatusDisplayMessage(event = {}) {
  const message = String(event.message || '').trim()
  if (!message) return ''
  const state = String(event.state || '').toLowerCase()
  const stage = String(event.stage || '').toLowerCase()
  const technicalTransportMessage = /\b(websocket|socket)\b/i.test(message)
  if (technicalTransportMessage && (state === 'listening' || state === 'live')) {
    return 'Streaming room audio'
  }
  if (technicalTransportMessage && (state === 'degraded' || stage === 'socket_error')) {
    return 'Live meeting connection interrupted'
  }
  return message
}

export function clearedLiveCaptureError() {
  return {
    ...browserLiveSupport(),
    lastErrorKind: '',
    lastErrorMessage: '',
    recoveryHint: '',
  }
}

export function microphoneFailureDiagnosticsUpdate(diagnostic, stage, support = browserLiveSupport()) {
  return {
    getUserMedia: support.getUserMedia,
    active: false,
    phase: 'idle',
    stage,
    lastEvent: `microphone_${diagnostic.kind}`,
    lastMessage: diagnostic.message,
    lastErrorKind: diagnostic.kind,
    lastErrorMessage: diagnostic.message,
    recoveryHint: diagnostic.recoveryHint,
  }
}

export function userMessageRoleLabel(user) {
  return teammateRoleLabel(user?.role_label)
}
