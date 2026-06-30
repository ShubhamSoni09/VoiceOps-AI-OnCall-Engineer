import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'
import PushToTalk, { copyCurrentUrlToClipboard, getCommandShortcutLabel, voiceUnavailableNotice } from './PushToTalk.jsx'

function renderPushToTalk(props = {}) {
  return renderToStaticMarkup(
    <PushToTalk
      listening={false}
      phase="idle"
      transcript={null}
      onToggle={vi.fn()}
      commandText=""
      commandInputRef={{ current: null }}
      onCommandTextChange={vi.fn()}
      onCommandSubmit={vi.fn()}
      processing={false}
      liveMeeting={false}
      liveStatus=""
      agentName="Ada"
      onToggleLive={vi.fn()}
      {...props}
    />,
  )
}

describe('PushToTalk', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('copies the current URL with explicit success and failure results', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)

    await expect(copyCurrentUrlToClipboard({
      windowObject: { location: { href: 'http://127.0.0.1:5174/' } },
      navigatorObject: { clipboard: { writeText } },
    })).resolves.toEqual({ ok: true, message: 'Copied' })
    expect(writeText).toHaveBeenCalledWith('http://127.0.0.1:5174/')

    await expect(copyCurrentUrlToClipboard({
      windowObject: { location: { href: 'http://127.0.0.1:5174/' } },
      navigatorObject: {},
    })).resolves.toEqual({ ok: false, message: 'Copy failed' })

    await expect(copyCurrentUrlToClipboard({
      windowObject: { location: { href: 'http://127.0.0.1:5174/' } },
      navigatorObject: { clipboard: { writeText: vi.fn().mockRejectedValue(new Error('denied')) } },
    })).resolves.toEqual({ ok: false, message: 'Copy failed' })
  })

  it('uses platform-specific command shortcut labels', () => {
    expect(getCommandShortcutLabel('MacIntel')).toBe('⌘K')
    expect(getCommandShortcutLabel('iPhone')).toBe('⌘K')
    expect(getCommandShortcutLabel('Win32')).toBe('Ctrl K')
    expect(getCommandShortcutLabel('Linux x86_64')).toBe('Ctrl K')
    expect(getCommandShortcutLabel()).toBe('Ctrl K')
  })

  it('distinguishes browser API gaps from unsupported live audio sessions', () => {
    expect(voiceUnavailableNotice({
      browserMicBlocked: true,
      message: 'Live meeting cannot access the browser microphone API.',
    })).toBe('This browser cannot use the microphone.')
    expect(voiceUnavailableNotice({
      browserMicBlocked: true,
      message: 'Live audio capture is not supported in this browser session.',
    })).toBe('This browser session cannot start live audio.')
    expect(voiceUnavailableNotice({
      browserMicBlocked: true,
      message: 'This browser cannot record live audio. Type a command below.',
    })).toBe('This browser cannot record live audio.')
    expect(voiceUnavailableNotice({
      browserMicBlocked: true,
      message: 'Microphone capture requires a secure local page. Open the app on http://127.0.0.1 or localhost.',
    })).toBe('Open this app on 127.0.0.1 or localhost for microphone access.')
  })

  it('uses meeting-oriented idle copy instead of production incident copy', () => {
    const html = renderPushToTalk()

    expect(html).toContain('aria-label="Command dock"')
    expect(html).toContain('Ready for text or voice')
    expect(html).toContain('Ask memory, request a patch, or run tests.')
    expect(html).toContain('placeholder="Ask memory, patch, or tests"')
    expect(html).toMatch(/<kbd>(⌘K|Ctrl K)<\/kbd> focus input/)
    expect(html).toContain('class="live-label-full">Live meeting</span>')
    expect(html).toContain('class="live-label-short">Live</span>')
    expect(html).not.toContain('Type instead')
    expect(html).not.toContain('what did we decide?')
    expect(html).not.toContain('what files did Alice mention?')
    expect(html).not.toContain('production')
    expect(html).not.toContain('deploy the fix')
  })

  it('uses the configured agent name in live meeting status', () => {
    const html = renderPushToTalk({ phase: 'live', liveMeeting: true, agentName: 'Ada' })

    expect(html).toContain('Live meeting')
    expect(html).toContain('say Ada to ask the agent')
    expect(html).toContain('Stop live')
  })

  it('uses cancel copy while live meeting is still starting', () => {
    const html = renderPushToTalk({ phase: 'calibrating', liveMeeting: true, liveStatus: 'Requesting microphone' })

    expect(html).toContain('Starting live meeting')
    expect(html).toContain('Requesting microphone')
    expect(html).toContain('class="live-label-full">Cancel</span>')
    expect(html).toContain('aria-label="Cancel live meeting startup"')
    expect(html).toContain('title="Cancel live meeting startup"')
    expect(html).not.toContain('Stop live')
    expect(html).not.toContain('Calibrating speakers')
    expect(html).not.toContain('WhisperX')
  })

  it('keeps degraded live meeting status provider-neutral', () => {
    const html = renderPushToTalk({ phase: 'degraded', liveMeeting: true, liveStatus: 'CPU mode may lag' })

    expect(html).toContain('Processing audio')
    expect(html).toContain('CPU mode may lag')
    expect(html).toContain('class="live-label-full">Cancel</span>')
    expect(html).not.toContain('WhisperX processing')
  })

  it('keeps typed commands available during live meetings', () => {
    const html = renderPushToTalk({
      phase: 'live',
      liveMeeting: true,
      agentName: 'Ada',
      commandText: 'what is open?',
    })

    expect(html).toContain('aria-label="Type command"')
    expect(html).not.toContain('aria-label="Type command" disabled=""')
    expect(html).toContain('aria-label="Send command"')
    expect(html).toContain('title="Send command"')
    expect(html).not.toContain('aria-label="Send command" disabled=""')
  })

  it('shows microphone recovery guidance without disabling typed commands', () => {
    const html = renderPushToTalk({
      voiceUnavailable: true,
      voiceUnavailableMessage: 'Microphone permission is blocked.',
      voiceRecoveryHint: 'Allow microphone access in browser site settings.',
      voiceRetryable: true,
    })

    expect(html).toContain('Microphone permission is blocked.')
    expect(html).toContain('Allow microphone access in browser site settings.')
    expect(html).toContain('role="alert"')
    expect(html).toContain('Voice needs attention')
    expect(html).toContain('typed commands ready')
    expect(html).not.toContain('Ask memory, request a patch, or run tests.')
    expect(html).not.toContain('Ready for text or voice')
    expect(html).toContain('type while voice is unavailable')
    expect(html).toContain('Retry live')
    expect(html).toContain('aria-label="Retry live meeting after microphone permission update"')
    expect(html).toContain('title="Allow microphone access in browser site settings, then start live meeting again."')
    expect(html).toContain('class="ptt voice-unavailable voice-retryable"')
    expect(html).not.toContain('class="live-label-full">Allow mic</span>')
    expect(html).not.toContain('aria-label="Microphone permission blocked"')
    expect(html).toContain('title="Allow microphone access in browser site settings, then try voice again"')
    expect(html).toContain('class="ptt-type-fallback"')
    expect(html).toContain('aria-label="Type a command instead"')
    expect(html).toContain('Type instead')
    expect(html).toContain('aria-label="Type command"')
    expect(html).not.toContain('aria-label="Type command" disabled=""')
  })

  it('does not offer retry when live audio is unavailable in the browser context', () => {
    vi.stubGlobal('window', { location: { href: 'http://127.0.0.1:5174/' } })
    const html = renderPushToTalk({
      voiceUnavailable: true,
      voiceUnavailableMessage: 'Live meeting cannot access the browser microphone API.',
      voiceRecoveryHint: 'Typed commands stay enabled. For voice, copy this link and open it in Chrome or Safari.',
      voiceRetryable: false,
    })

    expect(html).toContain('This browser cannot use the microphone.')
    expect(html).toContain('Voice unavailable')
    expect(html).toContain('typed commands ready')
    expect(html).not.toContain('Ask memory, request a patch, or run tests.')
    expect(html).not.toContain('Ready for text or voice')
    expect(html).not.toContain('Live meeting cannot access the browser microphone API.')
    expect(html).toContain('Typed commands stay enabled.')
    expect(html).toContain('copy this link and open it in Chrome or Safari')
    expect(html).not.toContain('browser context')
    expect(html).not.toContain('http://127.0.0.1:5174/react')
    expect(html).not.toContain('No mic here')
    expect(html).not.toContain('Live unavailable')
    expect(html).toContain('class="ptt voice-unavailable voice-blocked"')
    expect(html).not.toContain('class="live-meeting"')
    expect(html).not.toContain('aria-label="Live meeting unavailable"')
    expect(html).toContain('<button class="mic text-mode" type="button" aria-label="Type command"')
    expect(html).toContain('title="Voice unavailable; type a command instead"')
    expect(html).not.toMatch(/<button class="mic text-mode"[^>]*disabled=""/)
    expect(html).not.toContain('aria-label="Voice unavailable"')
    expect(html).not.toContain('Retry live')
    expect(html).toContain('class="ptt-type-fallback"')
    expect(html).toContain('Type instead')
    expect(html).not.toContain('class="ptt-open-url"')
    expect(html).not.toContain('href="http://127.0.0.1:5174/"')
    expect(html).not.toContain('Open local link')
    expect(html).toContain('class="ptt-copy-url"')
    expect(html).toContain('Copy local link')
    expect(html).toContain('aria-label="Copy local app URL"')
    expect(html).toContain('paste it into Chrome or Safari')
    expect(html).toContain('open it in Chrome or Safari')
    expect(html).toContain('type while voice is unavailable')
    expect(html).toContain('aria-label="Type command"')
    expect(html).not.toContain('aria-label="Type command" disabled=""')
  })

  it('uses session-specific copy when live audio startup is unsupported', () => {
    const html = renderPushToTalk({
      voiceUnavailable: true,
      voiceUnavailableMessage: 'Live audio capture is not supported in this browser session. Type a command below, or open this local app in Chrome or Safari.',
      voiceRecoveryHint: 'Typed commands stay enabled here. This browser session cannot start live audio; open this local app in Chrome or Safari.',
      voiceRetryable: false,
    })

    expect(html).toContain('This browser session cannot start live audio.')
    expect(html).toContain('Typed commands stay enabled here.')
    expect(html).not.toContain('This browser cannot use the microphone.')
    expect(html).not.toContain('Live audio capture is not supported in this browser session. Type a command below')
    expect(html).toContain('Copy local link')
  })

  it('uses recorder-specific copy when live recording is unavailable', () => {
    const html = renderPushToTalk({
      voiceUnavailable: true,
      voiceUnavailableMessage: 'This browser cannot record live audio. Type a command below.',
      voiceRecoveryHint: 'Typed commands stay enabled. For voice, copy this link and open it in Chrome or Safari.',
      voiceRetryable: false,
    })

    expect(html).toContain('This browser cannot record live audio.')
    expect(html).not.toContain('This browser cannot use the microphone.')
    expect(html).toContain('Copy local link')
    expect(html).toContain('Type instead')
  })

  it('uses local-origin copy when microphone capture is blocked by page security', () => {
    const html = renderPushToTalk({
      voiceUnavailable: true,
      voiceUnavailableMessage: 'Microphone capture requires a secure local page. Open the app on http://127.0.0.1 or localhost.',
      voiceRecoveryHint: 'Open the app on http://127.0.0.1 or localhost, then retry microphone capture.',
      voiceRetryable: false,
    })

    expect(html).toContain('Open this app on 127.0.0.1 or localhost for microphone access.')
    expect(html).toContain('Open the app on http://127.0.0.1 or localhost')
    expect(html).toContain('class="ptt voice-unavailable voice-blocked"')
    expect(html).toContain('class="ptt-type-fallback"')
    expect(html).toContain('Type instead')
    expect(html).toContain('Copy local link')
    expect(html).not.toContain('Retry live')
    expect(html).not.toContain('This browser cannot use the microphone.')
  })

  it('does not duplicate microphone errors in the transcript line', () => {
    const html = renderPushToTalk({
      liveStatus: 'Microphone permission is blocked.',
      transcript: 'Microphone permission is blocked.',
      voiceUnavailable: true,
      voiceUnavailableMessage: 'Microphone permission is blocked.',
      voiceRecoveryHint: 'Allow microphone access in browser site settings.',
    })

    expect(html.match(/Microphone permission is blocked\./g)).toHaveLength(1)
  })

  it('suppresses raw live capture failures when voice recovery guidance is shown', () => {
    const html = renderPushToTalk({
      transcript: 'Live audio capture is not supported in this browser session. Type a command below, or open this local app in Chrome or Safari.',
      voiceUnavailable: true,
      voiceUnavailableMessage: 'Live meeting cannot access the browser microphone API.',
      voiceRecoveryHint: 'Typed commands stay enabled. For voice, copy this link and open it in Chrome or Safari.',
      voiceRetryable: false,
    })

    expect(html).toContain('This browser cannot use the microphone.')
    expect(html).toContain('copy this link and open it in Chrome or Safari')
    expect(html).not.toContain('browser context')
    expect(html).not.toContain('Live audio capture is not supported')
  })
})
