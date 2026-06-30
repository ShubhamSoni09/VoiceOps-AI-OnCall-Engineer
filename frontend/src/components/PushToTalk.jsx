import { useState } from 'react'
import { Broadcast, Check, Keyboard, Microphone, PaperPlaneTilt, Stop, WarningCircle, Waveform } from '@phosphor-icons/react'
import { APP_NAME } from '../branding.js'

const WAVE_BARS = Array.from({ length: 36 }, (_, i) => ({
  delay: (i * 0.045).toFixed(2),
  dur: (0.7 + (i % 5) * 0.12).toFixed(2),
}))

function StateLine({ phase, agentName, voiceUnavailable, voiceRetryable }) {
  if (voiceUnavailable && phase !== 'live') {
    return (
      <>
        <WarningCircle size={14} /> {voiceRetryable ? 'Voice needs attention' : 'Voice unavailable'} · typed commands ready
      </>
    )
  }
  if (phase === 'live') {
    return (
      <>
        <span className="live">● Live meeting</span> · say {agentName} to ask the agent
      </>
    )
  }
  if (phase === 'calibrating') {
    return (
      <>
        <Waveform size={14} /> Starting live meeting
      </>
    )
  }
  if (phase === 'degraded') {
    return (
      <>
        <Waveform size={14} /> Processing audio
      </>
    )
  }
  if (phase === 'listening') {
    return (
      <>
        <span className="live">● Listening</span> · speak now, tap to stop
      </>
    )
  }
  if (phase === 'captured') {
    return (
      <>
        <Check size={14} color="var(--ok)" /> Captured · routing to agent
      </>
    )
  }
  return (
    <>
      <Waveform size={14} /> Ready for text or voice
    </>
  )
}

function hasBlockedMicrophonePermission(message = '', recoveryHint = '') {
  return /permission|notallowed/i.test(`${message} ${recoveryHint}`)
}

export function voiceUnavailableNotice({ browserMicBlocked, message } = {}) {
  if (!browserMicBlocked) return message || 'Microphone unavailable. Type a command below.'
  if (/secure local page|127\.0\.0\.1|localhost|securityerror/i.test(String(message || ''))) {
    return 'Open this app on 127.0.0.1 or localhost for microphone access.'
  }
  if (/browser session|live audio capture is not supported/i.test(String(message || ''))) {
    return 'This browser session cannot start live audio.'
  }
  if (/record|recording|mediarecorder/i.test(String(message || ''))) {
    return 'This browser cannot record live audio.'
  }
  return 'This browser cannot use the microphone.'
}

export function getCommandShortcutLabel(platform = '') {
  return /mac|iphone|ipad|ipod/i.test(String(platform || '')) ? '⌘K' : 'Ctrl K'
}

export async function copyCurrentUrlToClipboard({
  windowObject = typeof window !== 'undefined' ? window : undefined,
  navigatorObject = typeof navigator !== 'undefined' ? navigator : undefined,
} = {}) {
  const url = windowObject?.location?.href || ''
  if (!url || !navigatorObject?.clipboard?.writeText) {
    return { ok: false, message: 'Copy failed' }
  }
  try {
    await navigatorObject.clipboard.writeText(url)
    return { ok: true, message: 'Copied' }
  } catch {
    return { ok: false, message: 'Copy failed' }
  }
}

export default function PushToTalk({
  listening,
  phase,
  transcript,
  onToggle,
  commandText,
  commandInputRef,
  onCommandTextChange,
  onCommandSubmit,
  processing,
  liveMeeting,
  liveStatus,
  agentName = APP_NAME,
  onToggleLive,
  voiceUnavailable = false,
  voiceUnavailableMessage = '',
  voiceRecoveryHint = '',
  voiceRetryable = true,
}) {
  const [copyStatus, setCopyStatus] = useState('')
  const browserMicBlocked = voiceUnavailable && !voiceRetryable && !liveMeeting
  const voiceNotice = voiceUnavailable
    ? voiceUnavailableNotice({ browserMicBlocked, message: voiceUnavailableMessage })
    : ''
  const recoveryHint = browserMicBlocked
    ? voiceRecoveryHint || 'Open this local app in Chrome or Safari for voice.'
    : voiceRecoveryHint
  const transcriptText = String(transcript || '').trim()
  const duplicateVoiceNotice = voiceNotice && transcriptText === voiceNotice
  const suppressVoiceFailureTranscript = voiceUnavailable && !liveMeeting
  const visibleTranscript = suppressVoiceFailureTranscript || duplicateVoiceNotice
    ? ''
    : transcriptText || (voiceNotice ? '' : liveStatus)
  const transcriptFallback = voiceUnavailable && !liveMeeting ? '' : 'Ask memory, request a patch, or run tests.'
  const transcriptLine = visibleTranscript || transcriptFallback
  const commandHint = voiceUnavailable && !liveMeeting
    ? 'type while voice is unavailable'
    : 'focus input'
  const commandShortcutLabel = getCommandShortcutLabel(
    typeof navigator === 'undefined'
      ? ''
      : navigator.userAgentData?.platform || navigator.platform,
  )
  const commandPlaceholder = 'Ask memory, patch, or tests'
  const liveUnavailable = voiceUnavailable && !voiceRetryable && !liveMeeting
  const liveStarting = liveMeeting && phase !== 'live'
  const permissionBlocked = voiceUnavailable && !liveMeeting
    && hasBlockedMicrophonePermission(voiceUnavailableMessage, voiceRecoveryHint)
  const liveButtonLabel = liveMeeting
    ? liveStarting ? 'Cancel' : 'Stop live'
    : liveUnavailable
      ? 'No mic here'
      : permissionBlocked
        ? 'Retry live'
        : voiceUnavailable
          ? 'Retry live'
          : 'Live meeting'
  const liveButtonShortLabel = liveMeeting
    ? liveStarting ? 'Cancel' : 'Stop'
    : liveUnavailable
      ? 'No mic'
      : permissionBlocked
        ? 'Retry'
        : voiceUnavailable
          ? 'Retry'
          : 'Live'
  const liveButtonTitle = liveMeeting
    ? liveStarting ? 'Cancel live meeting startup' : 'Stop live meeting'
    : liveUnavailable
      ? 'This browser session cannot capture microphone audio. Typed commands still work; open this local app in Chrome or Safari for voice.'
      : permissionBlocked
        ? 'Allow microphone access in browser site settings, then start live meeting again.'
        : voiceUnavailable
          ? 'Retry live meeting'
          : 'Start live meeting'
  const liveButtonAriaLabel = liveMeeting
    ? liveStarting ? 'Cancel live meeting startup' : 'Stop live meeting'
    : liveUnavailable
      ? 'Live meeting unavailable'
      : permissionBlocked
        ? 'Retry live meeting after microphone permission update'
        : voiceUnavailable
          ? 'Retry live meeting'
          : 'Start live meeting'
  const showLiveMeetingButton = !liveUnavailable
  const micUnavailable = voiceUnavailable && !voiceRetryable && !liveMeeting
  const micButtonLabel = listening
    ? 'Stop voice capture'
    : micUnavailable
      ? 'Type command'
    : voiceUnavailable && !liveMeeting
      ? voiceRetryable
        ? permissionBlocked
          ? 'Retry voice capture'
          : 'Retry voice capture'
        : 'Voice unavailable'
      : 'Push to talk'
  const micButtonTitle = listening
    ? 'Stop voice capture'
    : micUnavailable
      ? 'Voice unavailable; type a command instead'
    : voiceUnavailable && !liveMeeting
      ? voiceRetryable
        ? permissionBlocked
          ? 'Allow microphone access in browser site settings, then try voice again'
          : 'Retry microphone capture'
        : 'This browser cannot capture microphone audio; typed commands still work'
      : 'Push to talk'
  const footerClass = [
    'ptt',
    voiceUnavailable && !liveMeeting ? 'voice-unavailable' : '',
    voiceUnavailable && !liveMeeting && voiceRetryable ? 'voice-retryable' : '',
    voiceUnavailable && !liveMeeting && !voiceRetryable ? 'voice-blocked' : '',
  ].filter(Boolean).join(' ')
  const showTypedFallback = voiceUnavailable && !liveMeeting
  const copyButtonLabel = copyStatus === 'Copied' ? 'Copied' : 'Copy local link'

  function focusTypedCommand() {
    const input = commandInputRef?.current
    input?.focus?.()
    input?.select?.()
  }

  async function copyLocalUrl() {
    const result = await copyCurrentUrlToClipboard()
    setCopyStatus(result.message)
    const timerHost = typeof window !== 'undefined' ? window : globalThis
    timerHost.setTimeout?.(() => setCopyStatus(''), 1800)
  }

  function handleMicButtonClick() {
    if (micUnavailable) {
      focusTypedCommand()
      return
    }
    onToggle?.()
  }

  return (
    <footer className={footerClass} aria-label="Command dock">
      <button
        className={`mic${micUnavailable ? ' text-mode' : ''}`}
        type="button"
        onClick={handleMicButtonClick}
        aria-label={micButtonLabel}
        title={micButtonTitle}
        disabled={liveMeeting}
      >
        {micUnavailable ? <Keyboard size={23} /> : listening ? <Stop size={23} /> : <Microphone size={23} />}
      </button>

      <div className="ptt-mid">
        <div className="ptt-state">
          <StateLine
            phase={phase}
            agentName={agentName}
            voiceUnavailable={voiceUnavailable && !liveMeeting}
            voiceRetryable={voiceRetryable}
          />
        </div>
        {transcriptLine && <div className="ptt-trans">{transcriptLine}</div>}
        {voiceNotice && !liveMeeting && (
          <div className="ptt-voice-warning" role="alert">
            <WarningCircle size={13} />
            <span>{voiceNotice}</span>
            {recoveryHint && <small>{recoveryHint}</small>}
            {showTypedFallback && (
              <div className="ptt-recovery-actions">
                <button
                  className="ptt-type-fallback"
                  type="button"
                  onClick={focusTypedCommand}
                  disabled={processing}
                  aria-label="Type a command instead"
                  title="Focus typed command input"
                >
                  Type instead
                </button>
                {browserMicBlocked && (
                  <>
                    <button
                      className="ptt-copy-url"
                      type="button"
                      onClick={copyLocalUrl}
                      disabled={processing}
                      aria-label="Copy local app URL"
                      title="Copy this local URL, then paste it into Chrome or Safari for microphone access"
                    >
                      {copyButtonLabel}
                    </button>
                  </>
                )}
                {copyStatus && (
                  <small className={`ptt-copy-status ${copyStatus === 'Copied' ? 'ok' : 'warn'}`} aria-live="polite">
                    {copyStatus === 'Copied' ? 'Local URL copied.' : 'Copy unavailable; use the address bar.'}
                  </small>
                )}
              </div>
            )}
          </div>
        )}
        <form className="command-entry" onSubmit={onCommandSubmit}>
          <input
            ref={commandInputRef}
            aria-label="Type command"
            value={commandText}
            onChange={(event) => onCommandTextChange(event.target.value)}
            placeholder={commandPlaceholder}
            disabled={processing}
          />
          <button
            type="submit"
            aria-label="Send command"
            title="Send command"
            disabled={processing || !commandText.trim()}
          >
            <PaperPlaneTilt size={15} />
          </button>
        </form>
        <div className="wave">
          {WAVE_BARS.map((b, i) => (
            <i key={i} style={{ animationDelay: `${b.delay}s`, animationDuration: `${b.dur}s` }} />
          ))}
        </div>
      </div>

      <div className="ptt-actions">
        {showLiveMeetingButton && (
          <button
            className={`live-meeting${liveMeeting ? ' on' : ''}`}
            type="button"
            onClick={onToggleLive}
            aria-label={liveButtonAriaLabel}
            aria-pressed={liveMeeting}
            title={liveButtonTitle}
            disabled={listening}
          >
            {liveMeeting ? <Stop size={15} /> : <Broadcast size={15} />}
            <span className="live-label-full">{liveButtonLabel}</span>
            <span className="live-label-short">{liveButtonShortLabel}</span>
          </button>
        )}
        <div className="ptt-hint">
          <kbd>{commandShortcutLabel}</kbd> {commandHint}
        </div>
      </div>
    </footer>
  )
}
