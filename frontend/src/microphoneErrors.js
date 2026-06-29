const SPEECH_ERROR_MESSAGES = {
  'audio-capture': 'No microphone input was captured. Check macOS microphone permission and close apps that may be using the mic.',
  'not-allowed': 'Microphone permission is blocked. Allow microphone access for this browser, then try again.',
  'service-not-allowed': 'Browser speech recognition is blocked. Use Live meeting, or type the command below.',
  network: 'Browser speech recognition service is unavailable. Use Live meeting, or type the command below.',
  'no-speech': 'No speech was detected. Try again closer to the microphone, or use Live meeting.',
  aborted: 'Speech capture stopped. Try again or use Live meeting.',
}

const RECOVERY_HINTS = {
  'not-allowed': 'Type commands still work. For voice, allow microphone access in browser site settings or open this local app in Chrome or Safari.',
  NotAllowedError: 'Type commands still work. For voice, allow microphone access in browser site settings or open this local app in Chrome or Safari.',
  NotFoundError: 'Connect or select a microphone in macOS Sound settings, then retry Live meeting.',
  NotReadableError: 'Close other recording apps and confirm this browser has macOS microphone permission.',
  OverconstrainedError: 'Select a different microphone input in browser or macOS Sound settings, then retry Live meeting.',
  NotSupportedError: 'Typed commands stay enabled here. For voice, open this local app in Chrome or Safari.',
  SecurityError: 'Open the app on http://127.0.0.1 or localhost, then retry microphone capture.',
  AbortError: 'Retry Live meeting. If it repeats, refresh the page and start again.',
  'microphone-timeout': 'Approve microphone access if the browser asks. Otherwise type commands below and retry Live meeting after checking site permissions.',
  unsupported: 'Use typed commands here, or retry live audio in Chrome or Safari with a selected input device.',
  'media-recorder-unavailable': 'Typed commands stay enabled. For voice, copy this link and open it in Chrome or Safari.',
  'get-user-media-unavailable': 'Typed commands stay enabled. For voice, copy this link and open it in Chrome or Safari.',
}

export function microphoneErrorKind(error) {
  return String(error?.name || error?.error || error?.code || 'unknown')
}

export function microphoneErrorMessage(error, mode = 'live') {
  const kind = microphoneErrorKind(error)
  const rawMessage = String(error?.message || '')
  const lower = `${kind} ${rawMessage}`.toLowerCase()

  if (mode === 'speech' && SPEECH_ERROR_MESSAGES[kind]) {
    return SPEECH_ERROR_MESSAGES[kind]
  }
  if (kind === 'NotAllowedError' || /permission|denied|notallowed|not-allowed/.test(lower)) {
    return 'Microphone permission is blocked. Allow microphone access for this browser, then try again.'
  }
  if (kind === 'NotFoundError' || /no.*device|notfound|not found/.test(lower)) {
    return 'No microphone was found. Connect or select an input device, then try Live meeting again.'
  }
  if (kind === 'NotReadableError' || /busy|in use|notreadable|could not start/.test(lower)) {
    return 'The microphone is busy or blocked by macOS. Close Zoom, Meet, Discord, or another recorder, then try again.'
  }
  if (kind === 'OverconstrainedError' || /overconstrained|constraint|constraints/.test(lower)) {
    return 'The selected microphone input cannot satisfy the browser audio settings. Select another input, then try Live meeting again.'
  }
  if (kind === 'NotSupportedError' || /not supported|unsupported/.test(lower)) {
    return 'Live audio capture is not supported in this browser session. Type a command below, or open this local app in Chrome or Safari.'
  }
  if (kind === 'SecurityError' || /secure|https/.test(lower)) {
    return 'Microphone capture requires a secure local page. Open the app on http://127.0.0.1 or localhost.'
  }
  if (kind === 'AbortError') {
    return 'Microphone startup was interrupted. Try Live meeting again.'
  }
  if (kind === 'microphone-timeout') {
    return 'Microphone permission did not complete. Type a command below or retry Live meeting after allowing access.'
  }
  return rawMessage || 'Unable to start microphone capture. Use Live meeting or type the command below.'
}

export function microphoneRecoveryHint(error, mode = 'live') {
  const kind = microphoneErrorKind(error)
  if (mode === 'speech' && kind === 'network') {
    return 'Use Live meeting for local audio processing, or type the command below.'
  }
  if (mode === 'speech' && kind === 'service-not-allowed') {
    return 'Use Live meeting instead of browser speech recognition, or type the command below.'
  }
  return RECOVERY_HINTS[kind] || RECOVERY_HINTS[String(error?.code || '')] || 'Type the command below while microphone capture is unavailable.'
}

export function microphoneDiagnostics(error, mode = 'live') {
  const kind = microphoneErrorKind(error)
  const message = microphoneErrorMessage(error, mode)
  const recoveryHint = microphoneRecoveryHint(error, mode)
  const permissionBlocked = /permission|blocked|notallowed|not-allowed/i.test(`${kind} ${message}`)
  const deviceUnavailable = /no microphone|busy|blocked by macos|no microphone input|selected microphone|another input/i.test(message)
  return {
    kind,
    message,
    recoveryHint,
    permissionBlocked,
    deviceUnavailable,
  }
}
