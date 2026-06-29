import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import TopBar, { workspaceStatusLabels } from './TopBar.jsx'

function renderTopBar(overrides = {}) {
  return renderToStaticMarkup(
    <TopBar
      theme="dark"
      onToggleTheme={vi.fn()}
      user={{ name: 'Priya Nair', initials: 'PN', role_label: 'teammate' }}
      statusCounts={{ critical: 2, active: 3 }}
      workspace={{ connected: true, name: 'voiceops', branch: 'main' }}
      roomSyncStatus="live"
      meetingClosureSmoke={null}
      {...overrides}
    />,
  )
}

describe('TopBar', () => {
  it('summarizes the meeting workspace instead of incident counts', () => {
    const html = renderTopBar()

    expect(html).toContain('meeting code workspace')
    expect(html).toContain('voiceops · main')
    expect(html).toContain('Room live')
    expect(html).toContain('aria-label="Toggle theme"')
    expect(html).toContain('aria-label="Sign out"')
    expect(html).not.toContain('critical')
    expect(html).not.toContain('active')
    expect(html).not.toContain('All clear')
  })

  it('normalizes legacy on-call user role labels', () => {
    const html = renderTopBar({
      user: { name: 'Priya Nair', initials: 'PN', role_label: 'On-call engineer' },
    })

    expect(html).toContain('teammate')
    expect(html).not.toContain('On-call engineer')
  })

  it('keeps disconnected repository state explicit', () => {
    const html = renderTopBar({
      workspace: { connected: false },
      roomSyncStatus: 'reconnecting',
    })

    expect(html).toContain('No repo connected')
    expect(html).toContain('Reconnecting')
  })

  it('uses neutral loading copy before bootstrap finishes', () => {
    const html = renderTopBar({
      workspace: undefined,
      roomSyncStatus: 'reconnecting',
      user: null,
    })

    expect(html).toContain('Checking repo')
    expect(html).toContain('Connecting')
    expect(html).not.toContain('No repo connected')
    expect(html).not.toContain('Reconnecting')
  })

  it('labels harness status as an E2E gate while preserving the test hook', () => {
    const html = renderTopBar({
      meetingClosureSmoke: {
        status: 'completed',
        steps: ['room_joined', 'patch_approved'],
      },
    })

    expect(html).toContain('data-testid="meeting-closure-smoke-status"')
    expect(html).toContain('E2E gate completed')
    expect(html).not.toContain('UI smoke')
  })

  it('keeps long workspace status scannable while preserving the full title', () => {
    const labels = workspaceStatusLabels({
      connected: true,
      name: 'VoiceOps-AI-OnCall-Engineer',
      branch: 'feat/ui-incident-dashboard',
    })

    expect(labels.label).toBe('VoiceOps-AI-OnCall-… · feat/ui-incident-…')
    expect(labels.title).toBe('VoiceOps-AI-OnCall-Engineer · feat/ui-incident-dashboard')

    const html = renderTopBar({
      workspace: {
        connected: true,
        name: 'VoiceOps-AI-OnCall-Engineer',
        branch: 'feat/ui-incident-dashboard',
      },
    })

    expect(html).toContain('VoiceOps-AI-OnCall-… · feat/ui-incident-…')
    expect(html).toContain('title="VoiceOps-AI-OnCall-Engineer · feat/ui-incident-dashboard"')
    expect(html).toContain('aria-label="Workspace: VoiceOps-AI-OnCall-Engineer · feat/ui-incident-dashboard"')
  })
})
