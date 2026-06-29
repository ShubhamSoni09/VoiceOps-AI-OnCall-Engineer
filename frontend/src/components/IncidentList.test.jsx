import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import IncidentList from './IncidentList.jsx'
import { integrationDisplayLabel } from './IncidentList.jsx'
import {
  sessionSetupDetail,
  teamRoomCompactText,
  teamRoomSummaryLabel,
  teamSpeakerSettingsMeta,
  workDashboardLeftRailStatus,
} from '../sessionRailModel.js'

function baseProps(overrides = {}) {
  return {
    incidents: [],
    integrations: [],
    workspace: { connected: false },
    participants: [],
    speakers: { provider: 'mock', profiles: [], mappings: [], unknown_speaker_labels: [] },
    speakerValidation: {
      ready: true,
      mapped_speaker_count: 0,
      unknown_speaker_count: 0,
      verification_count: 0,
    },
    agentSettings: {
      display_name: 'Ada',
      initials: 'AD',
      wake_words: ['ada'],
    },
    ...overrides,
  }
}

describe('IncidentList', () => {
  it('normalizes legacy alerting integration labels for the team coding console', () => {
    expect(integrationDisplayLabel({ id: 'slack', label: 'Slack / PagerDuty' })).toBe('Slack')
    expect(integrationDisplayLabel({ id: 'mcp', label: 'MCP workspace' })).toBe('MCP workspace')

    const html = renderToStaticMarkup(
      <IncidentList
        {...baseProps({
          integrations: [
            { id: 'slack', label: 'Slack / PagerDuty', connected: false },
          ],
        })}
      />,
    )

    expect(html).toContain('Slack')
    expect(html).not.toContain('PagerDuty')
  })

  it('uses neutral loading copy before workspace status is known', () => {
    const html = renderToStaticMarkup(
      <IncidentList
        {...baseProps({
          workspace: undefined,
        })}
      />,
    )

    expect(html).toContain('Preparing session')
    expect(html).toContain('Session setup')
    expect(html).toContain('loading')
    expect(html).toContain('Checking signed-in session, local workspace, and shared room.')
    expect(html).not.toContain('0 open')
    expect(html).not.toContain('Typed commands stay available while repo and room status load.')
    expect(html).not.toContain('Loading repository and team state.')
    expect(html).not.toContain('Connect a repository to track meeting decisions and patches.')
    expect(html).not.toContain('needed for patches')
  })

  it('keeps secondary team settings folded by default', () => {
    const html = renderToStaticMarkup(<IncidentList {...baseProps()} />)

    expect(html).toContain('Session setup')
    expect(html).toContain('repo for patches')
    expect(html).toContain('Connect local repo')
    expect(html).toContain('Text and live meeting work now. Connect a local repo for patch proposals.')
    expect(html).toContain('Workspace setup status')
    expect(html).toContain('Repository')
    expect(html).toContain('patches need repo')
    expect(html).toContain('text + voice ready')
    expect(html).toContain('Work tracking')
    expect(html).toContain('Input')
    expect(html).toContain('text or voice')
    expect(html).toContain('AI teammate')
    expect(html).toContain('name + wake words')
    expect(html).toContain('<details class="left-rail-details">')
    expect(html).toContain('Team settings')
    expect(html).toContain('Speakers')
    expect(html).toContain('speakers ready')
    expect(html).toContain('No speaker labels yet. Start a live meeting or ingest mock segments to map voices to teammates.')
    expect(html).toContain('AI teammate')
    expect(html).toContain('Initials')
    expect(html).toContain('aria-label="AI teammate name"')
    expect(html).toContain('aria-label="AI teammate initials"')
    expect(html).toContain('aria-label="AI teammate wake words"')
    expect(html).toContain('readonly=""')
    expect(html).toContain('read-only')
    expect(html).toContain('Admin only.')
    expect(html).not.toContain('aria-label="Save AI teammate settings"')
    expect(html).not.toContain('Save settings')
    expect(html).not.toContain('>ID<')
    expect(html).not.toContain('Save name')
    expect(html).not.toContain('<details class="left-rail-details" open="">')
  })

  it('shows AI teammate settings as editable for admins', () => {
    const html = renderToStaticMarkup(
      <IncidentList
        {...baseProps({
          onSaveAgentSettings: async () => true,
        })}
      />,
    )

    expect(html).toContain('admin')
    expect(html).toContain('aria-label="Save AI teammate settings"')
    expect(html).toContain('Save settings')
    expect(html).not.toContain('Admin only.')
  })

  it('marks input as text-only when voice capture is unavailable', () => {
    const html = renderToStaticMarkup(
      <IncidentList
        {...baseProps({
          workspace: { connected: true, name: 'voiceops' },
          workDashboard: {
            queue_health: [{ label: 'Open', value: 1, tone: 'info' }],
          },
          inputStatus: {
            detail: 'text only',
            title: 'Voice capture is unavailable. Typed commands still work.',
            ready: false,
          },
        })}
      />,
    )

    expect(html).toContain('Input')
    expect(html).toContain('<small title="Voice capture is unavailable. Typed commands still work.">text only</small>')
    expect(html).not.toContain('<small title="text or voice">text or voice</small>')
    expect(html).not.toContain('class="speaker-error"')
  })

  it('gives unknown speaker assignment controls stable accessible names', () => {
    const html = renderToStaticMarkup(
      <IncidentList
        {...baseProps({
          participants: [
            { id: 'alice', name: 'Alice Chen', initials: 'AC', online: true },
            { id: 'bob', name: 'Bob Smith', initials: 'BS', online: true },
          ],
          speakers: {
            provider: 'mock',
            profiles: [],
            mappings: [],
            unknown_speakers: [
              { speaker_label: 'SPEAKER_00', confidence: 0.82, text: 'Can someone fix the failing test?' },
            ],
          },
          speakerValidation: {
            ready: false,
            mapped_speaker_count: 0,
            unknown_speaker_count: 1,
            verification_count: 0,
          },
        })}
      />,
    )

    expect(html).toContain('aria-label="Assign SPEAKER_00 to teammate"')
    expect(html).toContain('Alice Chen')
    expect(html).toContain('Bob Smith')
  })

  it('summarizes speaker mapping state in the folded team entry', () => {
    expect(teamSpeakerSettingsMeta(
      [{ id: 'a' }, { id: 'b' }, { id: 'c' }],
      { ready: true, verification_count: 2, mapped_speaker_count: 2 },
    )).toBe('3 teammates · 2 verified')
    expect(teamSpeakerSettingsMeta(
      [{ id: 'a' }],
      { ready: false, unknown_speaker_count: 1 },
    )).toBe('1 teammate · map speakers')
  })

  it('shows mapped speaker count when verification has not run yet', () => {
    expect(teamSpeakerSettingsMeta(
      [{ id: 'a' }, { id: 'b' }],
      { ready: true, verification_count: 0, mapped_speaker_count: 2 },
    )).toBe('2 teammates · 2 mapped')
  })

  it('normalizes legacy role labels in the team room', () => {
    const html = renderToStaticMarkup(
      <IncidentList
        {...baseProps({
          participants: [
            {
              id: 'priya',
              name: 'Priya Nair',
              initials: 'PN',
              role_label: 'On-call engineer',
              online: true,
            },
          ],
        })}
      />,
    )

    expect(html).toContain('Priya Nair')
    expect(html).toContain('teammate')
    expect(html).toContain('aria-label="Team room summary: 1 teammate, 1 online"')
    expect(html).toContain('<small>1 teammate</small>')
    expect(html).not.toContain('1 teammate · 1 online')
    expect(html).not.toContain('On-call engineer')
  })

  it('surfaces the AI coworker as part of the team presence', () => {
    const html = renderToStaticMarkup(
      <IncidentList
        {...baseProps({
          workspace: { connected: true, name: 'VoiceOps-AI-OnCall-Engineer' },
          participants: [
            { id: 'priya', name: 'Priya Nair', initials: 'PN', online: true },
          ],
          agentSettings: {
            display_name: 'Ada',
            initials: 'AD',
            wake_words: ['ada'],
          },
        })}
      />,
    )

    expect(html).toContain('class="person ai-coworker-person"')
    expect(html).toContain('<b>Ada</b>')
    expect(html).toContain('AI coworker · wake: ada')
    expect(html).toContain('mini-avatar mini-avatar--agent')
  })

  it('keeps speaker identity status visible outside folded settings', () => {
    const html = renderToStaticMarkup(
      <IncidentList
        {...baseProps({
          speakerValidation: {
            ready: false,
            unknown_speaker_count: 1,
            mapped_speaker_count: 0,
            verification_status: 'needs_mapping',
          },
        })}
      />,
    )

    expect(html).toContain('class="team-speaker-summary attention"')
    expect(html).toContain('aria-label="Speaker identity: Map speakers, 1 unknown label"')
    expect(html).toContain('<span><svg')
    expect(html).toContain('Map speakers')
  })

  it('keeps compact team text short while preserving full online count for assistive tech', () => {
    const people = [
      { id: 'ada', online: true },
      { id: 'priya', online: true },
      { id: 'sam', online: false },
    ]

    expect(teamRoomCompactText(people)).toBe('3 teammates')
    expect(teamRoomSummaryLabel(people)).toBe('Team room summary: 3 teammates, 2 online')
  })

  it('shows a connected empty-work state without asking for repository setup again', () => {
    const repoName = 'VoiceOps-AI-OnCall-Engineer'
    const html = renderToStaticMarkup(
      <IncidentList
        {...baseProps({
          workspace: { connected: true, name: repoName },
          incidents: [],
        })}
      />,
    )

    expect(html).toContain('No tracked work yet')
    expect(html).toContain('Team room')
    expect(html).toContain('<span class="count">0 online</span>')
    expect(html).toContain('Ask about the repo, start a live meeting, or assign focused work.')
    expect(html).toContain(repoName)
    expect(html).toContain(`title="${repoName}"`)
    expect(html).not.toContain('0 open')
    expect(html).not.toContain('needed for patches')
    expect(html).not.toContain('Connect a coding agent before delegating work')
  })

  it('keeps connected setup in checking state while room data is loading', () => {
    const html = renderToStaticMarkup(
      <IncidentList
        {...baseProps({
          workspace: { connected: true, name: 'VoiceOps-AI-OnCall-Engineer' },
          incidents: [],
          roomStatus: { state: 'checking', message: 'loading meeting state' },
        })}
      />,
    )

    expect(html).toContain('Team room')
    expect(html).toContain('checking')
    expect(html).toContain('Syncing room')
    expect(html).toContain('Syncing timeline, approvals, memory, and handoff.')
    expect(html).toContain('Work tracking')
    expect(html).toContain('<small title="loading meeting state">checking</small>')
    expect(html).not.toContain('No tracked work yet')
    expect(html).not.toContain('Ask about the repo, start a live meeting')
  })

  it('does not treat dashboard-only loading as room loading', () => {
    expect(sessionSetupDetail({
      known: true,
      connected: true,
      hasActivity: false,
      roomState: 'ready',
      dashboardSyncing: true,
    })).toBe('Room timeline is ready. Work status is still syncing.')

    const html = renderToStaticMarkup(
      <IncidentList
        {...baseProps({
          workspace: { connected: true, name: 'VoiceOps-AI-OnCall-Engineer' },
          incidents: [],
          roomStatus: { state: 'ready', message: 'work dashboard syncing', dashboardSyncing: true },
        })}
      />,
    )

    expect(html).toContain('Team room')
    expect(html).toContain('<span class="count">syncing</span>')
    expect(html).toContain('Room ready')
    expect(html).toContain('Room timeline is ready. Work status is still syncing.')
    expect(html).toContain('<small title="work dashboard syncing">syncing</small>')
    expect(html).not.toContain('Syncing room')
    expect(html).not.toContain('Loading room timeline, approvals, memory, and handoff.')
    expect(html).not.toContain('Syncing timeline, approvals, memory, and handoff.')
    expect(html).not.toContain('No tracked work yet')
  })

  it('shows a sync issue instead of ready when room APIs fail after repository bootstrap', () => {
    const html = renderToStaticMarkup(
      <IncidentList
        {...baseProps({
          workspace: { connected: true, name: 'VoiceOps-AI-OnCall-Engineer' },
          incidents: [],
          roomStatus: { state: 'issue', message: 'Join room failed' },
        })}
      />,
    )

    expect(html).toContain('Team room')
    expect(html).toContain('sync issue')
    expect(html).toContain('Room sync issue')
    expect(html).toContain('Room data is unavailable. Check backend sync before approving work.')
    expect(html).toContain('<small title="Join room failed">sync issue</small>')
    expect(html).not.toContain('No tracked work yet')
  })

  it('keeps stable team room state quiet when dashboard activity exists', () => {
    expect(sessionSetupDetail({ known: true, connected: true, hasActivity: true }))
      .toBe('Activity, approvals, and handoff are being tracked.')
    expect(workDashboardLeftRailStatus({
      hasActivity: true,
      openQueue: 8,
      approvals: 1,
      openItems: 2,
      actionCount: 1,
    })).toEqual({
      detail: 'approval needed',
      title: '8 queued · 1 approval · 2 open items',
      ready: false,
    })

    const html = renderToStaticMarkup(
      <IncidentList
        {...baseProps({
          workspace: { connected: true, name: 'VoiceOps-AI-OnCall-Engineer' },
          incidents: [],
          workDashboard: {
            approvals: [{ id: 'act-1' }],
            open_items: [{ id: 'risk-1' }, { id: 'task-1' }],
            queue_health: [
              { label: 'Open', value: 8, tone: 'info' },
            ],
          },
          actions: [{ id: 'act-2' }],
          agentAssignments: [],
        })}
      />,
    )

    expect(html).toContain('Team room')
    expect(html).toContain('<span class="count">0 online</span>')
    expect(html).toContain('rail--left-no-body')
    expect(html).not.toContain('<div class="scroll"')
    expect(html).not.toContain('Session active')
    expect(html).not.toContain('Activity, approvals, and handoff are being tracked.')
    expect(html).not.toContain('<small title="VoiceOps-AI-OnCall-Engineer">connected</small>')
    expect(html).not.toContain('Queued work, approvals, and handoff context are tracked in the Work dashboard.')
    expect(html).not.toContain('Work tracking')
    expect(html).not.toContain('<small title="8 queued · 1 approval · 2 open items">approval needed</small>')
    expect(html).not.toContain('No tracked work yet')
    expect(html).not.toContain('assign focused work')
  })

  it('keeps the left rail status body when input recovery needs attention', () => {
    const html = renderToStaticMarkup(
      <IncidentList
        {...baseProps({
          workspace: { connected: true, name: 'voiceops' },
          workDashboard: {
            queue_health: [{ label: 'Open', value: 1, tone: 'info' }],
          },
          inputStatus: {
            detail: 'text only',
            title: 'Voice capture is unavailable. Typed commands still work.',
            ready: false,
          },
        })}
      />,
    )

    expect(html).not.toContain('rail--left-no-body')
    expect(html).toContain('<div class="scroll"')
    expect(html).toContain('Input')
    expect(html).toContain('<small title="Voice capture is unavailable. Typed commands still work.">text only</small>')
  })

  it('does not repeat team room title in the people block', () => {
    const html = renderToStaticMarkup(
      <IncidentList
        {...baseProps({
          workspace: { connected: true, name: 'VoiceOps-AI-OnCall-Engineer' },
          participants: [
            { id: 'ada', name: 'Ada', initials: 'AD', online: true },
            { id: 'priya', name: 'Priya Nair', initials: 'PN', online: true },
          ],
        })}
      />,
    )

    expect(html).toContain('<h2>Team room</h2>')
    expect(html).not.toContain('<div class="team-title"><span>Team room</span>')
    expect(html).toContain('aria-label="Team room summary: 2 teammates, 2 online"')
  })

  it('does not label memory items as patch review in the left rail', () => {
    expect(workDashboardLeftRailStatus({
      hasActivity: true,
      openQueue: 0,
      approvals: 0,
      openItems: 2,
      actionCount: 0,
    })).toEqual({
      detail: 'open items',
      title: '2 open items',
      ready: false,
    })
  })

  it('keeps left rail dashboard summary state-oriented when queue activity exists', () => {
    expect(workDashboardLeftRailStatus({
      hasActivity: true,
      openQueue: 8,
      approvals: 0,
      openItems: 0,
      actionCount: 0,
    })).toEqual({
      detail: 'activity active',
      title: '8 queued',
      ready: true,
    })
    expect(workDashboardLeftRailStatus({
      hasActivity: true,
      openQueue: 0,
      approvals: 0,
      openItems: 0,
      actionCount: 3,
    })).toEqual({
      detail: 'activity logged',
      title: '3 actions',
      ready: true,
    })
  })

  it('uses work item language only when tracked work exists', () => {
    const html = renderToStaticMarkup(
      <IncidentList
        {...baseProps({
          workspace: { connected: true, name: 'voiceops' },
          incidents: [
            {
              id: 'task-1',
              title: 'Review pending patch',
              status: 'open',
              service: 'workspace',
              severity: 'task',
            },
          ],
        })}
      />,
    )

    expect(html).toContain('Work items')
    expect(html).toContain('1 open')
    expect(html).toContain('Review pending patch')
    expect(html).toContain('role="list"')
    expect(html).toContain('aria-label="Tracked work items"')
    expect(html).toContain('role="listitem"')
    expect(html).not.toContain('<button class="inc')
    expect(html).not.toContain('Session setup')
  })

  it('opens team settings when speaker mapping needs attention', () => {
    const html = renderToStaticMarkup(
      <IncidentList
        {...baseProps({
          speakerValidation: {
            ready: false,
            unknown_speaker_count: 1,
            mapped_speaker_count: 0,
            verification_status: 'needs_mapping',
          },
        })}
      />,
    )

    expect(html).toContain('<details class="left-rail-details" open="">')
    expect(html).toContain('map speakers')
    expect(html).toContain('Team settings')
    expect(html).toContain('Speakers')
  })

  it('announces left rail speaker and agent setup errors', () => {
    const html = renderToStaticMarkup(
      <IncidentList
        {...baseProps({
          speakerError: 'Speaker state unavailable',
          agentSettingsError: 'Agent settings failed',
        })}
      />,
    )

    expect(html).toContain('<div class="agent-settings-error" role="alert">Agent settings failed</div>')
    expect(html).toContain('<div class="speaker-error" role="alert">Speaker state unavailable</div>')
  })
})
