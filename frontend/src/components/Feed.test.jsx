import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import Feed, { compactFeedDisplayText, compactFeedMessages, speakerIdentityLine } from './Feed.jsx'

function message(index) {
  return {
    id: `m-${index}`,
    role: index % 2 ? 'user' : 'agent',
    name: index % 2 ? 'Priya Nair' : 'VoiceOps',
    role2: index % 2 ? 'teammate' : 'AI teammate',
    when: '10:24 AM',
    text: `message ${index}`,
  }
}

describe('Feed', () => {
  it('does not ask for repository setup while workspace status is loading', () => {
    const html = renderToStaticMarkup(
      <Feed
        messages={[]}
        thinking=""
        workspace={undefined}
        agentName="Ada"
      />,
    )

    expect(html).toContain('Preparing session')
    expect(html).toContain('Checking signed-in session, local workspace, and shared room.')
    expect(html).toContain('Verify session')
    expect(html).toContain('Load workspace')
    expect(html).toContain('Join room')
    expect(html).not.toContain('Join team room')
    expect(html).not.toContain('Typed commands stay available while repo and room status load.')
    expect(html).not.toContain('Live meeting checks mic')
    expect(html).not.toContain('Repo status loading')
    expect(html).not.toContain('Live meeting needs mic access')
    expect(html).not.toContain('Connect repo for patches')
    expect(html).not.toContain('Loading room, repository')
    expect(html).not.toContain('Code actions wait for repo check')
    expect(html).not.toContain('You can talk to Ada now')
  })

  it('shows team room sync separately from repository loading', () => {
    const html = renderToStaticMarkup(
      <Feed
        messages={[]}
        thinking=""
        workspace={{ connected: true, name: 'voiceops' }}
        roomStatus={{ state: 'checking', message: 'timeline syncing' }}
        agentName="Ada"
      />,
    )

    expect(html).toContain('Joining team room')
    expect(html).toContain('timeline syncing')
    expect(html).toContain('Load timeline')
    expect(html).toContain('Sync approvals')
    expect(html).toContain('Prepare handoff')
    expect(html).not.toContain('Start the room with Ada')
  })

  it('shows dashboard-only sync as a usable room state', () => {
    const html = renderToStaticMarkup(
      <Feed
        messages={[]}
        thinking=""
        workspace={{ connected: true, name: 'voiceops' }}
        roomStatus={{ state: 'ready', message: 'work dashboard syncing', dashboardSyncing: true }}
        agentName="Ada"
      />,
    )

    expect(html).toContain('Start the room with Ada')
    expect(html).toContain('Timeline is ready. Work queue and dashboard status are still syncing.')
    expect(html).toContain('Queue work after sync')
    expect(html).not.toContain('Joining team room')
  })

  it('shows room sync errors before inviting approval work', () => {
    const html = renderToStaticMarkup(
      <Feed
        messages={[]}
        thinking=""
        workspace={{ connected: true, name: 'voiceops' }}
        roomStatus={{ state: 'issue', message: 'Join room failed' }}
        agentName="Ada"
      />,
    )

    expect(html).toContain('Room sync issue')
    expect(html).toContain('Join room failed')
    expect(html).toContain('Check backend')
    expect(html).toContain('Keep approvals paused')
    expect(html).not.toContain('Request approval-first patch')
  })

  it('keeps the empty room usable before a repository is connected', () => {
    const html = renderToStaticMarkup(
      <Feed
        messages={[]}
        thinking=""
        workspace={{ connected: false }}
        agentName="Ada"
      />,
    )

    expect(html).toContain('Start with text or live meeting')
    expect(html).toContain('You can talk to Ada now')
    expect(html).toContain('Connect a local repo when patches need approval.')
    expect(html).toContain('Type a command')
    expect(html).toContain('Start live meeting')
    expect(html).toContain('Connect local repo')
    expect(html).not.toContain('Connect repo for patches')
    expect(html).not.toContain('Start live meeting when mic works')
    expect(html).not.toContain('Connect a repository, then hold space')
  })

  it('suggests approval-first coding actions when a repository is connected', () => {
    const html = renderToStaticMarkup(
      <Feed
        messages={[]}
        thinking=""
        workspace={{ connected: true }}
        agentName="Ada"
      />,
    )

    expect(html).toContain('Start the room with Ada')
    expect(html).toContain('request an approval-first patch')
    expect(html).toContain('Request approval-first patch')
  })

  it('uses a generic teammate label in empty states until a custom agent name is known', () => {
    const connectedHtml = renderToStaticMarkup(
      <Feed
        messages={[]}
        thinking=""
        workspace={{ connected: true }}
      />,
    )
    const disconnectedHtml = renderToStaticMarkup(
      <Feed
        messages={[]}
        thinking=""
        workspace={{ connected: false }}
      />,
    )

    expect(connectedHtml).toContain('Start the room with your AI teammate')
    expect(disconnectedHtml).toContain('You can talk to your AI teammate now')
    expect(connectedHtml).not.toContain('Start the room with VoiceOps')
    expect(disconnectedHtml).not.toContain('You can talk to VoiceOps now')
  })

  it('uses the current AI teammate name for legacy internal agent messages', () => {
    const html = renderToStaticMarkup(
      <Feed
        messages={[
          {
            id: 'agent-legacy',
            role: 'agent',
            name: 'VoiceOps',
            role2: 'AI teammate',
            when: '10:24 AM',
            text: 'Priya assigned VoiceOps to inspect VoiceOps-AI-OnCall-Engineer.',
          },
        ]}
        thinking=""
        workspace={{ connected: true }}
        agentName="Ada"
      />,
    )

    expect(html).toContain('<div class="msg-head"><b class="name">Ada</b><span class="role">AI teammate</span><span class="when">10:24 AM</span></div>')
    expect(html).toContain('msg agent ai-teammate')
    expect(html).toContain('Priya assigned Ada to inspect VoiceOps-AI-OnCall-Engineer.')
    expect(html).not.toContain('VoiceOps<span class="role">AI teammate</span>')
    expect(html).not.toContain('assigned VoiceOps to')
  })

  it('keeps feed agent status updates concise while preserving audit titles', () => {
    expect(compactFeedDisplayText('I investigated VoiceOps-AI-OnCall-Engineer in the VoiceOps-AI-OnCall-Engineer workspace.'))
      .toBe('I investigated the VoiceOps-AI-OnCall-Engineer workspace.')
    expect(compactFeedDisplayText('I investigated the VoiceOps-AI-OnCall-Engineer workspace. Key files: README.md. All 1 tests pass.'))
      .toBe('Checked VoiceOps-AI-OnCall-Engineer · files: README.md · tests pass.')
    expect(compactFeedDisplayText('Priya asked Ada to status')).toBe('Priya asked Ada for status')

    const raw = 'I investigated VoiceOps-AI-OnCall-Engineer in the VoiceOps-AI-OnCall-Engineer workspace. Key files: README.md.'
    const html = renderToStaticMarkup(
      <Feed
        messages={[
          {
            id: 'agent-status',
            role: 'agent',
            name: 'Ada',
            role2: 'AI teammate',
            when: '10:24 AM',
            text: raw,
          },
        ]}
        thinking=""
        workspace={{ connected: true }}
      />,
    )

    expect(html).toContain('Checked VoiceOps-AI-OnCall-Engineer · files: README.md.')
    expect(html).not.toContain('I investigated VoiceOps-AI-OnCall-Engineer in the VoiceOps-AI-OnCall-Engineer workspace')
  })

  it('compacts repeated agent details without changing raw audit titles', () => {
    const raw = 'I investigated VoiceOps-AI-OnCall-Engineer in the VoiceOps-AI-OnCall-Engineer workspace. Key files: README.md.'
    const html = renderToStaticMarkup(
      <Feed
        messages={[
          { id: 'repeat-a', role: 'agent', name: 'Ada', role2: 'AI teammate', when: '10:24 AM', text: raw },
          { id: 'repeat-b', role: 'agent', name: 'Ada', role2: 'AI teammate', when: '10:25 AM', text: raw },
          { id: 'repeat-c', role: 'agent', name: 'Ada', role2: 'AI teammate', when: '10:26 AM', text: raw },
        ]}
        thinking=""
        workspace={{ connected: true }}
      />,
    )

    expect(html).toContain('Checked VoiceOps-AI-OnCall-Engineer · files: README.md.')
    expect(html).toContain('title="I investigated VoiceOps-AI-OnCall-Engineer in the VoiceOps-AI-OnCall-Engineer workspace. Key files: README.md."')
  })

  it('folds older activity when the room history is long', () => {
    const html = renderToStaticMarkup(
      <Feed
        messages={Array.from({ length: 45 }, (_, index) => message(index + 1))}
        thinking=""
        workspace={{ connected: true }}
      />,
    )

    expect(html).toContain('Earlier activity')
    expect(html).toContain('21 messages folded')
    expect(html).toContain('aria-label="Earlier activity, 21 messages folded"')
    expect(html).toContain('Recent activity')
    expect(html).toContain('last 24 shown')
    expect(html).toContain('message 1')
    expect(html).toContain('message 45')
    expect(html.indexOf('Earlier activity')).toBeLessThan(html.indexOf('Recent activity'))
    expect(html.indexOf('Recent activity')).toBeLessThan(html.indexOf('message 22'))
  })

  it('does not show history controls for short conversations', () => {
    const html = renderToStaticMarkup(
      <Feed
        messages={Array.from({ length: 4 }, (_, index) => message(index + 1))}
        thinking=""
        workspace={{ connected: true }}
      />,
    )

    expect(html).not.toContain('Earlier activity')
    expect(html).toContain('message 1')
    expect(html).toContain('message 4')
  })

  it('compacts adjacent repeated timeline messages', () => {
    const repeated = {
      id: 'repeat-1',
      role: 'agent',
      name: 'Ada',
      role2: 'AI teammate',
      when: '10:24 AM',
      text: 'I checked API status. All tests pass.',
    }
    const messages = compactFeedMessages([
      repeated,
      { ...repeated, id: 'repeat-2', when: '10:25 AM' },
      { ...repeated, id: 'repeat-3', when: '10:26 AM' },
      { id: 'next', role: 'user', name: 'Bob', role2: 'teammate', when: '10:27 AM', text: 'next topic' },
    ])

    expect(messages).toHaveLength(2)
    expect(messages[0].duplicateCount).toBe(3)
    expect(messages[0].duplicateMessages.map((m) => m.id)).toEqual(['repeat-1', 'repeat-2', 'repeat-3'])
  })

  it('compacts frequent exact repeats even when other messages appear between them', () => {
    const repeated = {
      id: 'repeat-1',
      role: 'agent',
      name: 'Ada',
      role2: 'AI teammate',
      when: '10:24 AM',
      text: 'I checked API status. All tests pass.',
    }
    const messages = compactFeedMessages([
      repeated,
      { id: 'u-1', role: 'user', name: 'Priya', role2: 'teammate', when: '10:25 AM', text: 'next' },
      { ...repeated, id: 'repeat-2', when: '10:26 AM' },
      { id: 'u-2', role: 'user', name: 'Priya', role2: 'teammate', when: '10:27 AM', text: 'another' },
      { ...repeated, id: 'repeat-3', when: '10:28 AM' },
    ])

    expect(messages).toHaveLength(3)
    expect(messages[0].duplicateCount).toBe(3)
    expect(messages[0].duplicateMessages.map((m) => m.id)).toEqual(['repeat-1', 'repeat-2', 'repeat-3'])
    expect(messages.map((m) => m.id)).toEqual(['repeat-1', 'u-1', 'u-2'])
  })

  it('does not compact two non-adjacent repeats because that can hide normal dialogue', () => {
    const repeated = {
      id: 'repeat-1',
      role: 'user',
      name: 'Priya',
      role2: 'teammate',
      when: '10:24 AM',
      text: 'check api status',
    }
    const messages = compactFeedMessages([
      repeated,
      { id: 'agent-1', role: 'agent', name: 'Ada', role2: 'AI teammate', when: '10:25 AM', text: 'done' },
      { ...repeated, id: 'repeat-2', when: '10:26 AM' },
    ])

    expect(messages).toHaveLength(3)
    expect(messages[0].duplicateCount).toBeUndefined()
    expect(messages[2].duplicateCount).toBeUndefined()
  })

  it('keeps voice and cited messages separate while compacting plain repeats', () => {
    const plain = {
      id: 'plain-1',
      role: 'user',
      name: 'Priya Nair',
      role2: 'teammate',
      when: '10:24 AM',
      text: 'Review the dashboard health queue',
    }
    const messages = compactFeedMessages([
      { ...plain, speakerLabel: 'SPEAKER_00' },
      { ...plain, id: 'voice-2', speakerLabel: 'SPEAKER_01' },
      plain,
      { ...plain, id: 'plain-2', when: '10:25 AM' },
      { ...plain, id: 'cited-1', citations: [{ title: 'Runtime memory', kind: 'memory' }] },
      { ...plain, id: 'cited-2', citations: [{ title: 'Runtime memory', kind: 'memory' }] },
    ])

    expect(messages).toHaveLength(5)
    expect(messages[0].duplicateCount).toBeUndefined()
    expect(messages[1].duplicateCount).toBeUndefined()
    expect(messages[2].duplicateCount).toBe(2)
    expect(messages[3].duplicateCount).toBeUndefined()
    expect(messages[4].duplicateCount).toBeUndefined()
  })

  it('shows whether live speaker attribution is provisional or finalized', () => {
    expect(speakerIdentityLine({
      role: 'user',
      fromVoice: true,
      speakerLabel: 'SPEAKER_01',
      confidence: 0.72,
    })).toEqual({
      tone: 'unknown',
      label: 'Needs speaker mapping',
      detail: 'SPEAKER_01 · 72%',
    })
    expect(speakerIdentityLine({
      role: 'user',
      fromVoice: true,
      speakerLabel: 'SPEAKER_00',
      confidence: 0.87,
      identitySource: 'manual',
    })).toEqual({
      tone: 'mapped',
      label: 'Mapped voice',
      detail: 'SPEAKER_00 · 87%',
    })

    const html = renderToStaticMarkup(
      <Feed
        messages={[
          {
            id: 'draft-live',
            role: 'user',
            name: 'Live captions',
            role2: 'instant transcript',
            when: '10:24 AM',
            text: 'draft caption',
            fromVoice: true,
            speakerLabel: 'local captions',
            draft: true,
            provisional: true,
          },
          {
            id: 'final-live',
            role: 'user',
            name: 'Priya Nair',
            role2: 'identified speaker',
            when: '10:25 AM',
            text: 'final attribution',
            fromVoice: true,
            speakerLabel: 'SPEAKER_00',
            confidence: 0.87,
            identityConfidence: 0.96,
            identitySource: 'manual',
            corrected: true,
          },
          {
            id: 'unknown-live',
            role: 'user',
            name: 'Unknown speaker',
            role2: 'unassigned speaker',
            when: '10:26 AM',
            text: 'unknown attribution',
            fromVoice: true,
            speakerLabel: 'SPEAKER_01',
            confidence: 0.72,
          },
        ]}
        thinking=""
        workspace={{ connected: true }}
      />,
    )

    expect(html).toContain('msg user voice provisional-speaker draft')
    expect(html).toContain('msg user voice mapped-speaker')
    expect(html).toContain('msg user voice unknown-speaker')
    expect(html).toContain('aria-label="Speaker identity: Live caption. local captions"')
    expect(html).toContain('aria-label="Speaker identity: Mapped voice. SPEAKER_00 · 87%"')
    expect(html).toContain('aria-label="Speaker identity: Needs speaker mapping. SPEAKER_01 · 72%"')
    expect(html).toContain('<span>Live caption</span><small>local captions</small>')
    expect(html).toContain('<span>Mapped voice</span><small>SPEAKER_00 · 87%</small>')
    expect(html).toContain('<span>Needs speaker mapping</span><small>SPEAKER_01 · 72%</small>')
    expect(html).toContain('provisional · local captions')
    expect(html).toContain('speaker confirmed · SPEAKER_00 · 87% · mapped')
    expect(html).toContain('voice · SPEAKER_01 · 72% · unassigned')
    expect(html).toContain('aria-label="Voice attribution: speaker confirmed · SPEAKER_00 · 87% · source manual · 96% identity"')
    expect(html).not.toContain('final attribution / SPEAKER_00 / 87% / manual / 96% identity')
    expect(html).toContain('voicequote pending')
    expect(html).toContain('voicequote corrected')
  })

  it('deduplicates RAG citation chips in agent timeline messages', () => {
    const html = renderToStaticMarkup(
      <Feed
        messages={[
          {
            id: 'agent-rag',
            role: 'agent',
            name: 'Ada',
            role2: 'AI teammate',
            when: '10:24 AM',
            text: 'Files mentioned: app.py.',
            citations: [
              { source: 'memory', title: 'Decision', actor_name: 'Priya Nair', excerpt: 'Use app.py.' },
              { source: 'timeline', title: 'Timeline note', actor_name: 'Priya Nair', excerpt: 'Use app.py.' },
              { source: 'ontology', title: 'file app.py', excerpt: 'file app.py is linked by mentions_file.' },
            ],
          },
        ]}
        thinking=""
        workspace={{ connected: true }}
      />,
    )

    expect(html.match(/Use app\.py\./g)).toHaveLength(1)
    expect(html).toContain('file app.py is linked by mentions_file.')
    expect(html).not.toContain('Timeline note')
  })

  it('compacts adjacent assignment queue activity without hiding audit rows', () => {
    const messages = compactFeedMessages([
      {
        id: 'queue-1',
        role: 'agent',
        name: 'Ada',
        role2: 'AI teammate',
        when: '10:24 AM',
        text: 'Priya Nair assigned Ada to phase 43 audit chips 1781862844299',
      },
      {
        id: 'queue-2',
        role: 'agent',
        name: 'Ada',
        role2: 'AI teammate',
        when: '10:25 AM',
        text: 'Priya Nair cancelled Ada assignment: phase 43 audit chips 1781862844299',
      },
      {
        id: 'queue-3',
        role: 'agent',
        name: 'Ada',
        role2: 'AI teammate',
        when: '10:26 AM',
        text: 'Priya Nair retried Ada assignment: phase 43 audit chips 1781862844299',
      },
      {
        id: 'next',
        role: 'user',
        name: 'Bob',
        role2: 'teammate',
        when: '10:27 AM',
        text: 'next topic',
      },
    ])

    expect(messages).toHaveLength(2)
    expect(messages[0]).toMatchObject({
      id: 'queue-1',
      activityCount: 3,
      activityLabel: 'Queue updates',
      text: 'Priya Nair had 3 assignment queue updates.',
    })
    expect(messages[0].activityMessages.map((m) => m.id)).toEqual(['queue-1', 'queue-2', 'queue-3'])
  })

  it('does not compact short assignment queue activity runs', () => {
    const messages = compactFeedMessages([
      {
        id: 'queue-1',
        role: 'agent',
        name: 'Ada',
        role2: 'AI teammate',
        when: '10:24 AM',
        text: 'Priya Nair assigned Ada to review the dashboard',
      },
      {
        id: 'queue-2',
        role: 'agent',
        name: 'Ada',
        role2: 'AI teammate',
        when: '10:25 AM',
        text: 'Priya Nair cancelled Ada assignment: review the dashboard',
      },
    ])

    expect(messages).toHaveLength(2)
    expect(messages[0].activityCount).toBeUndefined()
    expect(messages[1].activityCount).toBeUndefined()
  })

  it('renders repeated messages as one compact row with audit details', () => {
    const html = renderToStaticMarkup(
      <Feed
        messages={[
          {
            id: 'repeat-1',
            role: 'agent',
            name: 'Ada',
            role2: 'AI teammate',
            when: '10:24 AM',
            text: 'I checked API status. All tests pass.',
          },
          {
            id: 'repeat-2',
            role: 'agent',
            name: 'Ada',
            role2: 'AI teammate',
            when: '10:25 AM',
            text: 'I checked API status. All tests pass.',
          },
          {
            id: 'repeat-3',
            role: 'agent',
            name: 'Ada',
            role2: 'AI teammate',
            when: '10:26 AM',
            text: 'I checked API status. All tests pass.',
          },
        ]}
        thinking=""
        workspace={{ connected: true }}
      />,
    )

    expect(html).toContain('3 repeats · Ada')
    expect(html).toContain('aria-label="Ada repeated 3 times, 10:24 AM - 10:26 AM"')
    expect(html).toContain('title="Ada repeated 3 times, 10:24 AM - 10:26 AM"')
    expect(html).not.toContain('<small> · 10:24 AM - 10:26 AM</small>')
    expect(html).toContain('I checked API status. All tests pass.')
    expect(html).not.toContain('Ada · Repeated 3 times')
    expect(html).not.toContain('repeat-1')
    expect(html).not.toContain('repeat-3')
  })

  it('disambiguates repeated groups that share the same count and time range', () => {
    const html = renderToStaticMarkup(
      <Feed
        messages={[
          { id: 'user-1', role: 'user', name: 'Priya Nair', role2: 'teammate', when: '10:24 AM', text: 'check api status' },
          { id: 'agent-1', role: 'agent', name: 'Ada', role2: 'AI teammate', when: '10:24 AM', text: 'I checked API status. All tests pass.' },
          { id: 'user-2', role: 'user', name: 'Priya Nair', role2: 'teammate', when: '10:25 AM', text: 'check api status' },
          { id: 'agent-2', role: 'agent', name: 'Ada', role2: 'AI teammate', when: '10:25 AM', text: 'I checked API status. All tests pass.' },
          { id: 'user-3', role: 'user', name: 'Priya Nair', role2: 'teammate', when: '10:26 AM', text: 'check api status' },
          { id: 'agent-3', role: 'agent', name: 'Ada', role2: 'AI teammate', when: '10:26 AM', text: 'I checked API status. All tests pass.' },
        ]}
        thinking=""
        workspace={{ connected: true }}
      />,
    )

    expect(html).toContain('3 repeats · Priya Nair')
    expect(html).toContain('3 repeats · Ada')
    expect(html).not.toContain('>Repeated 3 times<')
  })

  it('uses the current AI teammate name inside repeated agent details', () => {
    const html = renderToStaticMarkup(
      <Feed
        messages={[
          {
            id: 'repeat-1',
            role: 'agent',
            name: 'VoiceOps',
            role2: 'AI teammate',
            when: '10:24 AM',
            text: 'I checked API status. All tests pass.',
          },
          {
            id: 'repeat-2',
            role: 'agent',
            name: 'VoiceOps',
            role2: 'AI teammate',
            when: '10:25 AM',
            text: 'I checked API status. All tests pass.',
          },
          {
            id: 'repeat-3',
            role: 'agent',
            name: 'VoiceOps',
            role2: 'AI teammate',
            when: '10:26 AM',
            text: 'I checked API status. All tests pass.',
          },
        ]}
        thinking=""
        workspace={{ connected: true }}
        agentName="Ada"
      />,
    )

    expect(html).toContain('<div class="msg-head"><b class="name">Ada</b><span class="role">AI teammate</span>')
    expect(html).toContain('<b>Ada</b>')
    expect(html).not.toContain('<b>VoiceOps</b>')
  })

  it('keeps message name, role, and time as separated header tokens', () => {
    const html = renderToStaticMarkup(
      <Feed
        messages={[
          {
            id: 'mobile-head',
            role: 'agent',
            name: 'Ada',
            role2: 'AI teammate',
            when: '03:15 AM',
            text: 'Queued dashboard review.',
          },
        ]}
        thinking=""
        workspace={{ connected: true }}
      />,
    )

    expect(html).toContain('<div class="msg-head"><b class="name">Ada</b><span class="role">AI teammate</span><span class="when">03:15 AM</span></div>')
    expect(html).not.toContain('AdaAI teammate03:15 AM')
  })

  it('renders assignment queue activity as one compact row with audit details', () => {
    const html = renderToStaticMarkup(
      <Feed
        messages={[
          {
            id: 'queue-1',
            role: 'agent',
            name: 'Ada',
            role2: 'AI teammate',
            when: '10:24 AM',
            text: 'Priya Nair assigned Ada to phase 43 audit chips 1781862844299',
          },
          {
            id: 'queue-2',
            role: 'agent',
            name: 'Ada',
            role2: 'AI teammate',
            when: '10:25 AM',
            text: 'Priya Nair cancelled Ada assignment: phase 43 audit chips 1781862844299',
          },
          {
            id: 'queue-3',
            role: 'agent',
            name: 'Ada',
            role2: 'AI teammate',
            when: '10:26 AM',
            text: 'Priya Nair retried Ada assignment: phase 43 audit chips 1781862844299',
          },
        ]}
        thinking=""
        workspace={{ connected: true }}
      />,
    )

    expect(html).toContain('Priya Nair had 3 assignment queue updates.')
    expect(html).toContain('3 updates · Queue updates')
    expect(html).toContain('aria-label="Queue updates, 3 updates, 10:24 AM - 10:26 AM"')
    expect(html).not.toContain('<small> · 10:24 AM - 10:26 AM</small>')
    expect(html).toContain('Priya Nair assigned Ada to phase 43 audit chips 1781862844299')
    expect(html).toContain('Priya Nair retried Ada assignment: phase 43 audit chips 1781862844299')
  })
})
