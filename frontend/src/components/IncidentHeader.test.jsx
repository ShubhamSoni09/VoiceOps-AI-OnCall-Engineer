import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import IncidentHeader from './IncidentHeader.jsx'
import { workspaceTitle } from '../workspaceHeaderModel.js'

describe('IncidentHeader', () => {
  it('separates signed-in user identity from the AI teammate title', () => {
    const html = renderToStaticMarkup(
      <IncidentHeader
        workspace={{ connected: true, name: 'voiceops', branch: 'main' }}
        user={{ name: 'Priya Nair' }}
      />,
    )

    expect(html).toContain('Signed in')
    expect(html).toContain('Code actions')
    expect(html).toContain('approval first')
    expect(html).toContain('SESSION READY')
    expect(html).toContain('title="voiceops"')
    expect(html).toContain('title="main"')
    expect(html).not.toContain('On-call')
    expect(html).not.toContain('<span class="k">Teammate</span>')
    expect(html).not.toContain('Agent</span>')
    expect(html).not.toContain('STANDBY')
  })

  it('normalizes legacy README titles into team-console language', () => {
    expect(workspaceTitle({
      connected: true,
      name: 'voiceops',
      readme_line: '# VoiceOps - AI On-Call Engineer',
    })).toBe('VoiceOps - AI Teammate Console')

    const html = renderToStaticMarkup(
      <IncidentHeader
        workspace={{
          connected: true,
          name: 'voiceops',
          branch: 'main',
          readme_line: '# VoiceOps - AI On-Call Engineer',
        }}
        user={{ name: 'Priya Nair' }}
      />,
    )

    expect(html).toContain('VoiceOps - AI Teammate Console')
    expect(html).not.toContain('AI On-Call Engineer')
    expect(html).not.toContain('Incident Console')
  })

  it('uses the custom AI teammate name as the main console title', () => {
    const html = renderToStaticMarkup(
      <IncidentHeader
        workspace={{
          connected: true,
          name: 'VoiceOps-AI-OnCall-Engineer',
          branch: 'main',
          readme_line: '# VoiceOps - AI On-Call Engineer',
        }}
        user={{ name: 'Priya Nair' }}
        agentName="Ada"
      />,
    )

    expect(html).toContain('Ada - AI Teammate Console')
    expect(html).toContain('title="VoiceOps-AI-OnCall-Engineer"')
    expect(html).not.toContain('VoiceOps - AI Teammate Console</h1>')
  })

  it('keeps the ready header detail concise instead of repeating the remote URL', () => {
    const html = renderToStaticMarkup(
      <IncidentHeader
        workspace={{
          connected: true,
          name: 'VoiceOps-AI-OnCall-Engineer',
          branch: 'feat/ui-incident-dashboard',
          remote_url: 'https://github.com/team/voiceops.git',
        }}
        user={{ name: 'Priya Nair' }}
        agentName="Ada"
      />,
    )

    expect(html).toContain('SESSION READY')
    expect(html).toContain('GitHub remote')
    expect(html).toContain('title="VoiceOps-AI-OnCall-Engineer"')
    expect(html).toContain('title="feat/ui-incident-dashboard"')
    expect(html).not.toContain('github.com/team/voiceops.git')
  })

  it('does not claim the session is ready while room state is still loading', () => {
    const html = renderToStaticMarkup(
      <IncidentHeader
        workspace={{ connected: true, name: 'voiceops', branch: 'main' }}
        user={{ name: 'Priya Nair' }}
        roomStatus={{ state: 'checking', message: 'loading meeting state' }}
      />,
    )

    expect(html).toContain('ROOM SYNCING')
    expect(html).toContain('loading meeting state')
    expect(html).toContain('syncing approvals')
    expect(html).not.toContain('SESSION READY')
    expect(html).not.toContain('approval first')
  })

  it('keeps the session ready while only the work dashboard is syncing', () => {
    const html = renderToStaticMarkup(
      <IncidentHeader
        workspace={{ connected: true, name: 'voiceops', branch: 'main' }}
        user={{ name: 'Priya Nair' }}
        roomStatus={{ state: 'ready', message: 'work dashboard syncing', dashboardSyncing: true }}
      />,
    )

    expect(html).toContain('SESSION READY')
    expect(html).toContain('work dashboard syncing')
    expect(html).toContain('dashboard syncing')
    expect(html).not.toContain('ROOM SYNCING')
    expect(html).not.toContain('syncing approvals')
  })

  it('shows a sync issue instead of ready when room APIs fail after bootstrap', () => {
    const html = renderToStaticMarkup(
      <IncidentHeader
        workspace={{ connected: true, name: 'voiceops', branch: 'main' }}
        user={{ name: 'Priya Nair' }}
        roomStatus={{ state: 'issue', message: 'Join room failed' }}
      />,
    )

    expect(html).toContain('ROOM SYNC ISSUE')
    expect(html).toContain('Join room failed')
    expect(html).toContain('check room sync')
    expect(html).not.toContain('SESSION READY')
  })

  it('uses workspace setup language when no repository is connected', () => {
    const html = renderToStaticMarkup(
      <IncidentHeader
        workspace={{ connected: false }}
        user={{ name: 'Priya Nair' }}
      />,
    )

    expect(html).toContain('SETUP NEEDED')
    expect(html).toContain('repo required for patches')
    expect(html).toContain('VoiceOps - AI Teammate Console')
    expect(html).toContain('not connected')
    expect(html).toContain('connect repo first')
    expect(html).toContain('Priya Nair')
    expect(html).not.toContain('&gt;-&lt;')
  })

  it('uses neutral loading language before workspace status is known', () => {
    const html = renderToStaticMarkup(
      <IncidentHeader
        workspace={undefined}
        user={null}
      />,
    )

    expect(html).toContain('CHECKING')
    expect(html).toContain('loading workspace')
    expect(html).toContain('VoiceOps - AI Teammate Console')
    expect(html).toContain('checking workspace')
    expect(html).toContain('current teammate')
    expect(html).not.toContain('SETUP NEEDED')
    expect(html).not.toContain('not connected')
  })

  it('does not show placeholder dashes when user identity is not loaded yet', () => {
    const html = renderToStaticMarkup(
      <IncidentHeader
        workspace={{ connected: false }}
        user={null}
      />,
    )

    expect(html).toContain('current teammate')
    expect(html).not.toContain('&gt;-&lt;')
  })
})
