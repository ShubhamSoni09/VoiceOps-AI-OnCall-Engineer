import { readFileSync } from 'node:fs'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import RightRail, {
  actionAuditSummary,
  aiCoworkerPresence,
  agentLLMModelPlaceholder,
  agentRoutingFlowCopy,
  agentSetupSummary,
  assignmentReadinessMessage,
  AgentLLMRoutingPanel,
  DeploymentHardeningPanel,
  deploymentHardeningFlowCopy,
  compactQueueAssignments,
  diagnosticsErrorLabel,
  diagnosticsEventLabel,
  EventStoreReadinessPanel,
  compactHandoffLines,
  commandFallbackCopy,
  cutoverActionSteps,
  CitationList,
  externalAgentCapabilityBoundary,
  externalAgentPanelBoundary,
  externalAgentPanelSummary,
  MemoryHealthStrip,
  memoryAnswerDisplayText,
  memoryAnswerSourceSummary,
  mobileMemoryHint,
  handoffDisplay,
  memoryEmptyText,
  memoryDetailsTitle,
  memoryRouteLabel,
  normalizeExternalAgentProviders,
  operatorDiagnosticsMeta,
  OperatorAcceptancePanel,
  partialQueueSummary,
  projectAccessMeta,
  PullRequestReadiness,
  pullRequestPlanState,
  pullRequestReadinessFacts,
  repositoryAuthBoundary,
  repositoryBindingSummary,
  repoSetupCommands,
  repositoryFlowCopy,
  roomInviteUrl,
  repositoryNextSteps,
  runtimeDiagnosticsMeta,
  setupActionStripItems,
  setupRunwaySummary,
  shouldShowMemoryEmptyState,
  shouldShowQueueHealth,
  splitActionRows,
  systemDetailsMeta,
  TargetReadinessPanel,
  visibleCitationSummary,
  liveSetupDiagnosticItems,
  liveDiagnosticChecks,
  LLMCredentialsPanel,
  llmProviderSummary,
  visibleMemoryItems,
  WorkDashboardPanel,
  workDashboardDelegateMeta,
  workDashboardDelegateSummaryMeta,
  workDashboardNextStep,
} from './RightRail.jsx'
import { compactDisplayText, displayActorName, displayText } from '../displayText.js'
import {
  actionLifecycleFacts,
  actionSummaryLabel,
  compactRecentActions,
  gitStatusAfterLabel,
  pendingApprovalBrief,
} from '../actionLogModel.js'

function renderRightRail(overrides = {}) {
  return renderToStaticMarkup(
    <RightRail
      workspace={{ name: 'Test workspace' }}
      actionDone=""
      artifacts={[]}
      metrics={[]}
      handoff={{ lines: [] }}
      workDashboard={null}
      actions={[]}
      agentRuns={[]}
      agentAssignments={[]}
      externalAgentProviders={[]}
      messages={[]}
      auditEvents={[]}
      memory={[]}
      liveDiagnostics={{}}
      user={{ permissions: ['admin:manage', 'agent:approve', 'agent:run', 'credentials:manage_own', 'voice:use'] }}
      onApproveAction={async () => {}}
      onRejectAction={async () => {}}
      onCommitAction={async () => {}}
      onCreatePullRequest={async () => {}}
      onStartExternalAgentOAuth={async () => {}}
      onConnectExternalAgentLocalCli={async () => {}}
      onRunExternalAgent={async () => {}}
      onRecommendExternalAgent={async () => {}}
      onUpdateAgentLLMRoute={async () => {}}
      onPreflightAgentLLMRoute={async () => {}}
      onConnectLLMProvider={async () => {}}
      onPreflightLLMProvider={async () => {}}
      onDisconnectLLMProvider={async () => {}}
      onCreateAgentAssignment={async () => {}}
      onDispatchAgentAssignment={async () => {}}
      onCancelAgentAssignment={async () => {}}
      onRetryAgentAssignment={async () => {}}
      onClearCompletedAgentAssignments={async () => {}}
      {...overrides}
    />,
  )
}

function externalProvider(overrides = {}) {
  const provider = overrides.provider || 'codex'
  const label = overrides.label || 'OpenAI Codex'
  const supportedModels = overrides.supported_models || ['provider-default']
  return {
    provider,
    label,
    connected: true,
    auth_method: 'local_cli',
    supported_models: supportedModels,
    default_model: overrides.default_model || supportedModels[0],
    mode_readiness: {
      review: { ready: true, detail: 'ready' },
      patch: { ready: true, detail: 'ready' },
      test: { ready: true, detail: 'ready' },
      explain: { ready: true, detail: 'ready' },
      memory: { ready: false, detail: 'memory uses internal agent' },
      ...(overrides.mode_readiness || {}),
    },
    ...overrides,
  }
}

const workDashboardHandlers = {
  onCreateAgentAssignment: async () => {},
  onDispatchAgentAssignment: async () => {},
  onCancelAgentAssignment: async () => {},
  onRetryAgentAssignment: async () => {},
  onClearCompletedAgentAssignments: async () => {},
}

describe('RightRail work dashboard', () => {
  it('maps system diagnostics to user-facing rail meta', () => {
    expect(systemDetailsMeta({ connected: true }, { lastEvent: 'captions_stopped' })).toBe('ready')
    expect(systemDetailsMeta({ connected: false }, { lastEvent: 'idle' })).toBe('repo not connected')
    expect(systemDetailsMeta({ connected: true }, { active: true, phase: 'live' })).toBe('live active')
    expect(systemDetailsMeta({ connected: true }, { lastErrorKind: 'NotAllowedError' })).toBe('mic issue')
    expect(systemDetailsMeta({ connected: true }, { lastErrorKind: 'NotSupportedError' })).toBe('audio session issue')
    expect(systemDetailsMeta({ connected: true }, { lastErrorKind: 'api-runtime-unavailable' })).toBe('api issue')
    expect(systemDetailsMeta(undefined, {})).toBe('checking')
    expect(runtimeDiagnosticsMeta({ connected: true }, {})).toBe('details')
    expect(runtimeDiagnosticsMeta({ connected: false }, {})).toBe('repo setup')
    expect(runtimeDiagnosticsMeta({ connected: true }, { active: true })).toBe('live active')
    expect(runtimeDiagnosticsMeta({ connected: true }, { lastErrorKind: 'socket_error' })).toBe('socket issue')
    expect(operatorDiagnosticsMeta()).toBe('checking')
    expect(operatorDiagnosticsMeta({
      operatorAcceptance: { accepted: true },
      eventStoreReadiness: { ready: true },
      systemReadiness: { ready: true },
      runtimeStatus: { warnings: [] },
    })).toBe('ready')
    expect(operatorDiagnosticsMeta({
      operatorAcceptance: { accepted: false },
      eventStoreReadiness: { ready: false },
      systemReadiness: { ready: true },
      runtimeStatus: { warnings: ['JWT secret is default.'] },
    })).toBe('3 blockers')
  })

  it('keeps memory health focused on actionable counts', () => {
    const html = renderToStaticMarkup(
      <MemoryHealthStrip
        health={{
          status: 'needs_review',
          total_items: 22,
          status_counts: { open: 10 },
          stale_open_count: 0,
          source_coverage: { message: 14, action: 8 },
          warnings: ['10 open memory item(s) need review.'],
        }}
      />,
    )

    expect(html).toContain('open items')
    expect(html).toContain('22 items')
    expect(html).toContain('10 open')
    expect(html).toContain('aria-label="Meeting memory health: open items. 22 items · 10 open. Review 10 open memory items before handoff."')
    expect(html).toContain('title="Review 10 open memory items before handoff."')
    expect(html).not.toContain('<p>Review 10 open memory items before handoff.</p>')
    expect(html).not.toContain('review needed')
    expect(html).not.toContain('0 stale')
    expect(html).not.toContain('memory-health-metrics')
    expect(html).not.toContain('14 msg')
    expect(html).not.toContain('8 action')
  })

  it('keeps memory health readable when the backend omits optional fields', () => {
    const html = renderToStaticMarkup(<MemoryHealthStrip health={{}} />)

    expect(html).toContain('checking')
    expect(html).toContain('0 items')
    expect(html).toContain('no open items')
    expect(html).toContain('aria-label="Meeting memory health: checking. 0 items · no open items. Memory is ready for handoff."')
    expect(html).not.toContain('<p>Memory is ready for handoff.</p>')
  })

  it('summarizes mobile meeting memory without exposing the full memory list', () => {
    expect(mobileMemoryHint({
      health: { total_items: 14, status_counts: { open: 3 } },
      hasIndexedMemory: true,
      loading: false,
    })).toBe('3 open items')
    expect(mobileMemoryHint({
      health: { total_items: 1, status_counts: { open: 0 } },
      hasIndexedMemory: true,
      loading: false,
    })).toBe('1 indexed item')
    expect(mobileMemoryHint({ health: null, hasIndexedMemory: false, loading: false })).toBe('Ask what changed')
    expect(mobileMemoryHint({ health: null, hasIndexedMemory: false, loading: true })).toBe('Checking memory')
  })

  it('cleans lightweight markdown from user-facing summary text', () => {
    expect(displayText('I checked **VoiceOps-AI-OnCall-Engineer** and `README.md`.'))
      .toBe('I checked VoiceOps-AI-OnCall-Engineer and README.md.')
    expect(displayText('Review __health route__ before _approval_.'))
      .toBe('Review health route before approval.')
  })

  it('summarizes folded action audit details with concrete detail types', () => {
    expect(actionAuditSummary([
      { key: 'route' },
      { key: 'files' },
      { key: 'verification' },
    ])).toBe('routing · files · tests')
  })

  it('keeps setup and action status visible outside deep setup details', () => {
    const items = setupActionStripItems({
      workspace: {
        connected: true,
        branch: 'main',
        remote_url: 'git@github.com:team/app.git',
      },
      providers: [externalProvider({
        provider: 'claude',
        label: 'Claude Code',
        mode_readiness: {
          review: { ready: true, detail: 'ready' },
          patch: { ready: false, detail: 'Local CLI not connected.' },
          test: { ready: true, detail: 'ready' },
          explain: { ready: true, detail: 'ready' },
        },
      })],
      actions: [{
        id: 'act-1',
        action: 'patch',
        status: 'pending_approval',
        summary: 'Fix app.py health route.',
      }],
      agentName: 'Ada',
    })

    expect(items).toEqual([
      expect.objectContaining({ key: 'repo', tone: 'ready', value: 'GitHub repo connected' }),
      expect.objectContaining({ key: 'agents', tone: 'attention', value: 'Patch agent needed' }),
      expect.objectContaining({ key: 'actions', tone: 'attention', value: '1 approval' }),
    ])
    expect(items[2].detail).toContain('Fix app.py health route')
  })

  it('surfaces production hardening blockers in the top setup strip', () => {
    const hardening = {
      status: 'needs_attention',
      ready: false,
      environment: 'production',
      score: 72,
      ready_count: 8,
      total_count: 11,
      checks: [
        {
          id: 'jwt_secret',
          label: 'JWT secret is non-default and strong',
          ready: false,
          status: 'needs_attention',
          severity: 'critical',
          detail: 'JWT secret is default.',
          action: 'Rotate JWT_SECRET with a generated secret.',
        },
      ],
    }

    expect(deploymentHardeningFlowCopy(hardening)).toMatchObject({
      tone: 'blocked',
      title: 'Production blocked',
      meta: 'Rotate JWT_SECRET with a generated secret.',
      detail: 'JWT secret is non-default and strong',
    })

    const items = setupActionStripItems({
      workspace: { connected: true, branch: 'main', remote_url: 'https://github.com/team/app.git' },
      providers: [externalProvider({ provider: 'claude', label: 'Claude Code' })],
      actions: [],
      deploymentHardening: hardening,
    })

    expect(items).toEqual([
      expect.objectContaining({ key: 'repo', tone: 'ready' }),
      expect.objectContaining({ key: 'agents', tone: 'ready' }),
      expect.objectContaining({ key: 'deploy', tone: 'blocked', value: 'Production blocked' }),
      expect.objectContaining({ key: 'actions', tone: 'ready' }),
    ])
    expect(setupRunwaySummary({
      workspace: { connected: true, branch: 'main', remote_url: 'https://github.com/team/app.git' },
      providers: [externalProvider({ provider: 'claude', label: 'Claude Code' })],
      actions: [],
      deploymentHardening: hardening,
    })).toMatchObject({
      tone: 'blocked',
      label: 'Production blocker',
      title: 'Production blocked',
      detail: 'Rotate JWT_SECRET with a generated secret.',
    })
  })

  it('collapses repo, agent, and approval readiness into one next setup runway', () => {
    expect(setupRunwaySummary({
      workspace: { connected: false },
      providers: [],
      actions: [],
      agentName: 'Ada',
    })).toMatchObject({
      tone: 'blocked',
      label: 'Next setup',
      title: 'Connect local repo',
      actionLabel: 'Setup',
      targetId: 'agent-setup',
    })

    expect(setupRunwaySummary({
      workspace: { connected: true, remote_url: 'https://github.com/team/app.git', branch: 'main' },
      providers: [externalProvider({ provider: 'claude', label: 'Claude Code' })],
      actions: [{
        id: 'act-1',
        action: 'patch',
        status: 'pending_approval',
        summary: 'Fix app.py health route.',
      }],
      agentName: 'Ada',
    })).toMatchObject({
      tone: 'attention',
      label: 'Next decision',
      title: 'Review pending patch',
      detail: 'Fix app.py health route.',
      actionLabel: 'Review',
      targetId: 'agent-actions',
    })

    expect(setupRunwaySummary({
      workspace: { connected: true, remote_url: 'https://github.com/team/app.git', branch: 'main' },
      providers: null,
      actions: [],
      agentName: 'Ada',
    })).toMatchObject({
      tone: 'attention',
      label: 'Next setup',
      title: 'Checking coding agents',
      detail: 'Repo, memory, and typed commands work now. Agent assignment unlocks when provider routes finish loading.',
      actionLabel: 'Setup',
      targetId: 'agent-setup',
    })

    expect(setupRunwaySummary({
      workspace: { connected: true, remote_url: 'https://github.com/team/app.git', branch: 'main' },
      providers: [externalProvider({ provider: 'claude', label: 'Claude Code' })],
      actions: [],
      agentName: 'Ada',
    })).toMatchObject({
      tone: 'ready',
      label: 'Ready',
      title: 'Code collaboration ready',
    })
  })

  it('prioritizes the AI coworker state by the next human decision', () => {
    expect(aiCoworkerPresence({
      agentName: 'Ada',
      workspace: { connected: true },
      liveDiagnostics: { active: true, provider: 'whisperx' },
      actions: [{
        id: 'act-1',
        action: 'patch',
        status: 'pending_approval',
        summary: 'Review the health endpoint patch.',
      }],
    })).toMatchObject({
      tone: 'attention',
      status: 'Waiting approval',
      title: 'Ada proposed a patch',
      detail: 'Review the health endpoint patch.',
      chips: ['1 approval', 'preview first'],
    })

    expect(aiCoworkerPresence({
      agentName: 'Ada',
      workspace: { connected: true },
      agentRuns: [{
        id: 'run-1',
        status: 'running',
        route: 'patch_request',
        summary: 'Code agent is preparing a proposal.',
        steps: [{ role: 'code' }, { role: 'review' }],
      }],
    })).toMatchObject({
      tone: 'active',
      status: 'Working',
      title: 'Ada is coordinating agents',
      chips: ['2 steps', 'patch request'],
    })

    expect(aiCoworkerPresence({
      agentName: 'Ada',
      workspace: { connected: false },
    })).toMatchObject({
      tone: 'attention',
      status: 'Ready, repo needed',
      title: 'Ada is online',
      chips: ['memory ready', 'patches blocked'],
    })
  })

  it('uses the custom AI teammate name for legacy agent actors', () => {
    expect(displayActorName('VoiceOps', 'Ada')).toBe('Ada')
    expect(displayActorName('voiceops', 'Ada')).toBe('Ada')
    expect(displayActorName('Priya Nair', 'Ada')).toBe('Priya Nair')
    expect(displayActorName('', 'Ada', 'system')).toBe('system')
  })

  it('compacts repetitive workspace and status phrasing for visible summaries', () => {
    expect(compactDisplayText(
      'Priya Nair asked Ada to status. I investigated **VoiceOps-AI-OnCall-Engineer** in the VoiceOps-AI-OnCall-Engineer workspace. Key files: `README.md`.',
    )).toBe(
      'Priya Nair asked Ada for status. Checked VoiceOps-AI-OnCall-Engineer · files: README.md.',
    )
    expect(compactDisplayText(
      'I investigated the VoiceOps-AI-OnCall-Engineer workspace. Key files: .gitignore, PRODUCT.md, README.md. All 1 tests pass.',
    )).toBe(
      'Checked VoiceOps-AI-OnCall-Engineer · files: .gitignore, PRODUCT.md, README.md · tests pass.',
    )
  })

  it('uses honest empty copy when indexed memory is not visible yet', () => {
    expect(memoryEmptyText({
      answer: null,
      hasIndexedMemory: true,
      loading: false,
    })).toBe('Memory exists, but no items match this view.')
    expect(memoryEmptyText({
      answer: null,
      hasIndexedMemory: true,
      loading: true,
    })).toBe('Loading current memory.')
    expect(memoryEmptyText({
      answer: null,
      hasIndexedMemory: false,
      loading: false,
    })).toBe('Decisions, questions, tasks, and code references will collect here.')
  })

  it('formats meeting memory route labels without requiring every backend field', () => {
    expect(memoryRouteLabel(null)).toBe('')
    expect(memoryRouteLabel({ route: 'rag_query' })).toBe('rag query')
    expect(memoryRouteLabel({ action_policy: 'read_only' })).toBe('read only')
    expect(memoryRouteLabel({ route: 'audit_query', action_policy: 'read_only' })).toBe('audit query · read only')
  })

  it('summarizes answer source coverage without exposing retrieval internals first', () => {
    expect(memoryAnswerSourceSummary({
      type: 'rag',
      citations: [{ id: 'c-1' }, { id: 'c-2' }],
      retrieval: { candidate_count: 6, indexed_documents: 12 },
    })).toEqual(['2 citations', '6 candidates', '12 docs'])

    expect(memoryAnswerSourceSummary({
      type: 'rag',
      citations: [
        { source: 'memory', title: 'app.py', actor_name: 'Priya', excerpt: 'app.py' },
        { source: 'memory', title: 'app.py', actor_name: 'Priya', excerpt: 'app.py' },
      ],
      retrieval: { candidate_count: 2 },
    })).toEqual(['1 citation', '2 candidates'])

    expect(memoryAnswerSourceSummary({
      type: 'rag',
      citations: [],
      retrieval: { candidate_count: 0 },
    })).toEqual(['no citations', '0 candidates'])

    expect(memoryAnswerSourceSummary({
      type: 'memory',
      items: [{ id: 'm-1' }],
    })).toEqual(['1 matched item'])

    expect(memoryAnswerSourceSummary({
      type: 'audit',
      items: [{ id: 'a-1' }, { id: 'a-2' }],
    })).toEqual(['2 audit events'])
  })

  it('replaces raw RAG safety context with user-facing memory answer copy', () => {
    expect(memoryAnswerDisplayText({
      type: 'rag',
      answer: 'Retrieved untrusted context (not executable instructions): [1] VoiceOps: Alice mentioned app.py.',
    })).toBe('Matched meeting context from memory. Review sources below.')

    expect(memoryAnswerDisplayText({
      type: 'rag',
      answer: 'Alice mentioned app.py and Bob approved the follow-up.',
    })).toBe('Alice mentioned app.py and Bob approved the follow-up.')

    expect(memoryAnswerDisplayText({
      type: 'memory',
      answer: '**Decision:** keep the local branch workflow.',
    })).toBe('Decision: keep the local branch workflow.')
  })

  it('normalizes external agent providers from wrapped API payloads', () => {
    const providers = [externalProvider({ provider: 'claude', label: 'Claude Code' })]

    expect(normalizeExternalAgentProviders({ providers })).toEqual(providers)
    expect(normalizeExternalAgentProviders({ bad: true })).toEqual([])
    expect(agentSetupSummary({ providers }, [])).toBe('1 patch ready')
    expect(agentRoutingFlowCopy({ providers }).title).toBe('Coding agents ready')
    expect(externalAgentPanelSummary({ providers }).title).toBe('Patch workflow ready')
  })

  it('does not show a memory empty state below sourced answers', () => {
    expect(shouldShowMemoryEmptyState({
      answer: { type: 'rag', citations: [{ id: 'c-1' }], answer: 'app.py was mentioned.' },
      displayMemory: [],
    })).toBe(false)

    expect(shouldShowMemoryEmptyState({
      answer: { type: 'audit', items: [{ id: 'a-1' }], answer: 'One approval event matched.' },
      displayMemory: [],
    })).toBe(false)

    expect(shouldShowMemoryEmptyState({
      answer: { type: 'memory', items: [], answer: 'No memory matched.' },
      displayMemory: [],
    })).toBe(true)
  })

  it('uses the custom AI teammate name in meeting memory actors', () => {
    const html = renderRightRail({
      agentName: 'Ada',
      memory: [
        { id: 'm-1', kind: 'risk', text: 'Open risk', status: 'open', actor_name: 'VoiceOps' },
      ],
    })

    expect(html).toContain('Ada - open')
    expect(html).not.toContain('VoiceOps - open')
  })

  it('keeps RAG citations compact while preserving extra sources', () => {
    const citations = [
      { source: 'code', title: 'app.py', excerpt: 'Health route returns app.py status.', score: 12 },
      { source: 'memory', title: 'Priya mentioned app.py', actor_name: 'Priya Nair', excerpt: 'app.py' },
      { source: 'ontology', title: 'Health endpoint', excerpt: 'Service health decision.' },
      { source: 'message', title: 'Timeline note', actor_name: 'Bob' },
      { source: 'action', title: 'Approved patch', excerpt: 'Bob approved the app.py patch.' },
    ]

    expect(visibleCitationSummary(citations)).toMatchObject({
      visible: citations.slice(0, 3),
      hidden: citations.slice(3),
      total: 5,
    })

    const html = renderToStaticMarkup(<CitationList citations={citations} />)
    expect(html).toContain('Sources')
    expect(html).toContain('3/5 shown')
    expect(html).toContain('<details class="citation-extra">')
    expect(html).not.toContain('<details class="citation-extra" open="">')
    expect(html).toContain('2 more sources')
    expect(html).toContain('Priya Nair')
    expect(html).toContain('Approved patch')
    expect(html.indexOf('app.py')).toBeLessThan(html.indexOf('2 more sources'))
  })

  it('deduplicates repeated RAG citations before showing source counts', () => {
    const citations = [
      { source: 'memory', title: 'Priya mentioned app.py', actor_name: 'Priya Nair', excerpt: 'app.py' },
      { source: 'memory', title: 'Priya mentioned app.py', actor_name: 'Priya Nair', excerpt: 'app.py' },
      { source: 'memory', source_id: 'm-2', title: 'Rollback question', actor_name: 'Sam Ortiz', excerpt: 'Need follow up.' },
      { source: 'memory', source_id: 'm-2', title: 'Rollback question duplicate', actor_name: 'Sam Ortiz', excerpt: 'Need follow up.' },
    ]

    expect(visibleCitationSummary(citations)).toMatchObject({
      visible: [citations[0], citations[2]],
      hidden: [],
      total: 2,
    })

    const html = renderToStaticMarkup(<CitationList citations={citations} />)
    expect(html).toContain('2 sources')
    expect(html).not.toContain('4 sources')
    expect(html).not.toContain('duplicate')
  })

  it('prioritizes open memory when handoff has open items', () => {
    const items = [
      { id: 'm-1', kind: 'decision', text: 'Decision', status: 'noted' },
      { id: 'm-2', kind: 'risk', text: 'Risk', status: 'open' },
      { id: 'm-3', kind: 'question', text: 'Question', status: 'open' },
      { id: 'm-4', kind: 'task', text: 'Task', status: 'noted' },
    ]

    expect(visibleMemoryItems(items, '', true, 3).map((item) => item.id)).toEqual(['m-3', 'm-2', 'm-4'])
    expect(memoryDetailsTitle({ preferOpen: true, displayCount: 3 })).toEqual({
      title: 'Open memory',
      meta: '3 shown',
    })
  })

  it('keeps memory query and health primary while filters stay inside folded memory details', () => {
    const html = renderRightRail({
      memory: [
        { id: 'm-1', kind: 'task', text: 'Fix app.py health route', status: 'open', actor_name: 'Alice' },
        { id: 'm-2', kind: 'decision', text: 'Use approval-first patches', status: 'noted', actor_name: 'Bob' },
      ],
    })

    expect(html).toContain('Search meeting memory and audit')
    expect(html).toContain('placeholder="Search or ask memory"')
    expect(html).toContain('aria-label="Search meeting memory"')
    expect(html).toContain('title="Search meeting memory"')
    expect(html).toContain('id="meeting-memory"')
    expect(html).toContain('class="memory-mobile-hint" href="#meeting-memory"')
    expect(html).toContain('class="memory-query-submit"')
    expect(html).not.toContain('>Search</button>')
    expect(html).not.toContain('Submit meeting memory question')
    expect(html).toContain('<details class="memory-details">')
    expect(html).toContain('Recent memory')
    expect(html).toContain('aria-label="Memory options: All types"')
    expect(html).toContain('aria-label="Memory filters"')
    expect(html).not.toContain('<details class="memory-controls">')
    expect(html).not.toContain('Memory options</span>')
    expect(html.indexOf('Search meeting memory and audit')).toBeLessThan(html.indexOf('Recent memory'))
    expect(html.indexOf('Recent memory')).toBeLessThan(html.indexOf('Memory filters'))
    expect(html).toContain('Fix app.py health route')
  })

  it('keeps the right rail task-first with setup and diagnostics folded', () => {
    const html = renderRightRail({
      workspace: { name: 'VoiceOps-AI-OnCall-Engineer', connected: false },
      workDashboard: {
        metrics: [],
        agents: [],
        approvals: [],
        open_items: [],
        recent_actions: [],
        recent_decisions: [],
      },
      handoff: { lines: ['Alice proposed one patch.'] },
      actions: [],
      agentRuns: [],
      externalAgentProviders: [],
      liveDiagnostics: {
        getUserMedia: true,
        mediaRecorder: true,
        instantCaptions: true,
        phase: 'idle',
        lastEvent: 'idle',
      },
    })

    expect(html.indexOf('AI coworker')).toBeLessThan(html.indexOf('Setup and action status'))
    expect(html.indexOf('Setup and action status')).toBeLessThan(html.indexOf('Work dashboard'))
    expect(html).toContain('AI coworker status:')
    expect(html).toContain('class="setup-action-strip blocked"')
    expect(html).toContain('aria-label="Setup and action status: Connect local repo.')
    expect(html).toContain('<a class="setup-runway-link" href="#agent-setup">Setup</a>')
    expect(html).toContain('<details class="setup-action-details" aria-label="Setup status details">')
    expect(html).not.toContain('<details class="setup-action-details" aria-label="Setup status details" open="">')
    expect(html).toContain('<span>Status details</span>')
    expect(html.indexOf('Team room')).toBeLessThan(html.indexOf('Work dashboard'))
    expect(html.indexOf('Work dashboard')).toBeLessThan(html.indexOf('Approval'))
    expect(html.indexOf('Approval')).toBeLessThan(html.indexOf('<span>Agent setup</span>'))
    expect(html.indexOf('<span>Agent setup</span>')).toBeLessThan(html.indexOf('<span>System details</span>'))
    expect(html.indexOf('Approval')).toBeLessThan(html.indexOf('Repo not connected'))
    expect(html).toContain('id="agent-actions"')
    expect(html).toContain('id="agent-setup"')
    expect(html.indexOf('Agent assignment off')).toBeLessThan(html.indexOf('Work dashboard'))
    expect(html.indexOf('Repository setup')).toBeGreaterThan(html.indexOf('<span>Agent setup</span>'))
    expect(html.indexOf('Repository setup')).toBeLessThan(html.indexOf('<span>System details</span>'))
    expect(html).toContain('<details class="rail-section-details">')
    expect(html).toContain('<span>Agent setup</span>')
    expect(html).toContain('<small>repo not connected · connect coding agent</small>')
    expect(html).not.toContain('0 runs · 0 providers')
    expect(html).toContain('<span>System details</span>')
    expect(html).toContain('repo not connected')
    expect(html).not.toContain('<span>Operator diagnostics</span>')
    expect(html).toContain('Team invite')
    expect(html).toContain('Set VOICEOPS_WORKSPACE to a GitHub repository URL or a cloned local folder before code actions can propose patches.')
    expect(html).toContain('connect coding agent')
    expect(html).toContain('Connect a coding agent before assigning review, test, or patch work.')
    expect(html).not.toContain('readiness · idle')
    expect(html).toContain('System summary')
    expect(html).toContain('<details class="diagnostic-details">')
    expect(html).not.toContain('<details class="diagnostic-details" open="">')
  })

  it('marks right rail sections so phone layouts can expose approvals only', () => {
    const html = renderRightRail({
      actions: [
        {
          id: 'act-pending-mobile',
          action: 'patch',
          status: 'pending_approval',
          summary: 'Fix app.py health route',
          requested_by_name: 'Priya Nair',
          files_changed: ['app.py'],
          approval: { diff: 'diff --git a/app.py b/app.py' },
        },
      ],
    })

    expect(html).toContain('rail-section rail-section--workdash')
    expect(html).toContain('rail-section rail-section--actions')
    expect(html).toContain('rail-section rail-section--memory')
    expect(html).toContain('rail-section rail-section--handoff')
    expect(html).toContain('Fix app.py health route')
    expect(html).toContain('<a class="setup-runway-link" href="#agent-actions">Review</a>')
  })

  it('shows setup runway only for actionable setup states', () => {
    const blocked = renderRightRail({
      workspace: { connected: false, name: '' },
      externalAgentProviders: [],
    })
    const ready = renderRightRail({
      workspace: { connected: true, name: 'voiceops', remote_url: 'https://github.com/team/voiceops.git' },
      externalAgentProviders: [
        externalProvider({
          provider: 'local',
          label: 'Local Open Agent',
          connected: true,
          mode_readiness: {
            patch: { ready: true, detail: 'ready' },
            review: { ready: true, detail: 'ready' },
            test: { ready: true, detail: 'ready' },
          },
        }),
      ],
    })

    expect(blocked).toContain('class="setup-action-strip blocked"')
    expect(blocked).toContain('Setup runway: Connect local repo.')
    expect(ready).not.toContain('class="setup-action-strip ready"')
    expect(ready).not.toContain('Setup runway: Code collaboration ready.')
  })

  it('keeps advanced agent setup controls folded inside agent setup', () => {
    const html = renderRightRail({
      agentLLMRouting: {
        room_id: 'main',
        routes: [
          { role: 'code', provider: 'openai_compatible', model: 'glm-5.2' },
          { role: 'review', provider: 'mock', model: 'mock-deterministic' },
        ],
      },
      externalAgentProviders: [
        externalProvider({ provider: 'claude', label: 'Claude Code', connected: true }),
        externalProvider({ provider: 'codex', label: 'OpenAI Codex', connected: false }),
      ],
    })

    expect(html).toContain('<span>Agent setup</span>')
    expect(html).toContain('aria-label="LLM routing: 2 roles · credentials"')
    expect(html).toContain('aria-label="Coding agents: 1 connected · 1 to set up"')
    expect(html).toContain('<details class="diagnostic-details agent-setup-subdetails">')
    expect(html).toContain('<details class="diagnostic-details agent-setup-subdetails" open=""><summary aria-label="Coding agents: 1 connected · 1 to set up"')
    expect(html).not.toContain('<details class="diagnostic-details agent-setup-subdetails" open=""><summary aria-label="LLM routing')
    expect(html.indexOf('Agent team')).toBeLessThan(html.indexOf('LLM routing'))
    expect(html.indexOf('aria-label="LLM routing: 2 roles · credentials"')).toBeLessThan(
      html.indexOf('aria-label="Coding agents: 1 connected · 1 to set up"'),
    )
  })

  it('renders artifacts as non-interactive evidence rows', () => {
    const html = renderRightRail({
      workspace: { name: 'VoiceOps-AI-OnCall-Engineer', connected: true },
      artifacts: [
        { type: 'logs', title: 'Approval log', subtitle: 'branch voiceops/act-1-fix' },
      ],
      liveDiagnostics: {
        active: true,
        phase: 'live',
      },
    })

    expect(html).toContain('aria-label="Recorded artifacts"')
    expect(html).toContain('role="listitem"')
    expect(html).toContain('Approval log')
    expect(html).toContain('recorded')
    expect(html).not.toContain('<button class="artifact"')
  })

  it('summarizes agent setup by connection and actionable setup state', () => {
    expect(agentSetupSummary(null, [])).toBe('checking code-writing')
    expect(agentSetupSummary([], [])).toBe('connect coding agent')
    expect(agentSetupSummary([
      externalProvider({ provider: 'claude', connected: false }),
      externalProvider({ provider: 'codex', connected: false }),
    ], [])).toBe('connect coding agent')
    expect(agentSetupSummary([
      externalProvider({ provider: 'claude', connected: true }),
      externalProvider({ provider: 'codex', connected: false }),
    ], [])).toBe('1 patch ready · 1 needs setup')
    expect(agentSetupSummary([
      externalProvider({
        provider: 'claude',
        connected: true,
        mode_readiness: {
          patch: { ready: false, reason: 'cli_missing', detail: 'Local CLI command is not installed.' },
        },
      }),
      externalProvider({ provider: 'codex', connected: false }),
    ], [])).toBe('connect local CLI · 1 needs setup')
    expect(agentSetupSummary([
      externalProvider({
        provider: 'codex',
        connected: true,
        mode_readiness: {
          patch: { ready: false, reason: 'cli_missing', detail: 'Local CLI command is not installed.' },
        },
      }),
    ], [])).toBe('connect local CLI')
    expect(agentSetupSummary([
      externalProvider({ provider: 'claude', connected: true }),
      externalProvider({ provider: 'codex', connected: false }),
    ], [
      { id: 'run-1', status: 'running' },
      { id: 'run-2', status: 'completed' },
    ])).toBe('1 running · 1 patch ready · 1 needs setup')
  })

  it('keeps multi-agent role traces folded behind the run summary', () => {
    const html = renderRightRail({
      agentRuns: [
        {
          id: 'run-1',
          route: 'meeting_patch_closure',
          status: 'running',
          summary: 'Review Agent is checking the patch proposal.',
          metadata: {
            control: { max_steps: 20, timeout_seconds: 60 },
          },
          steps: [
            { role: 'coordinator', status: 'completed' },
            { role: 'meeting', status: 'running' },
          ],
          exchanges: [
            { id: 'ex-1', from_role: 'meeting', to_role: 'code', title: 'Context handed to code' },
          ],
        },
      ],
    })

    expect(html).toContain('meeting patch closure')
    expect(html).toContain('budget 2/20')
    expect(html).toContain('timeout 60s')
    expect(html).toContain('<details class="agent-trace-details">')
    expect(html).not.toContain('<details class="agent-trace-details" open="">')
    expect(html).toContain('Agent trace')
    expect(html).toContain('2 roles · 1 handoff')
  })

  it('does not show zero-state queue counts while the work dashboard is loading', () => {
    const html = renderToStaticMarkup(
      <WorkDashboardPanel
        {...workDashboardHandlers}
        dashboard={null}
        agentAssignments={[]}
        externalAgentProviders={[]}
      />,
    )

    expect(html).toContain('Queue')
    expect(html).toContain('Syncing')
    expect(html).toContain('room updates')
    expect(html).toContain('Now')
    expect(html).toContain('Syncing dashboard')
    expect(html).toContain('Syncing room, queue, and agents.')
    expect(html).toContain('Checking')
    expect(html).toContain('No patch')
    expect(html).toContain('approval clear')
    expect(html).toContain('class="workdash-status-strip"')
    expect(html).not.toContain('Room sync in progress')
    expect(html).not.toContain('0 open')
    expect(html).not.toContain('No coding agents')
    expect(html).not.toContain('Commands ready · assignment off')
    expect(html).not.toContain('<b>Clear</b>')
  })

  it('does not treat an unloaded handoff as an empty handoff', () => {
    expect(handoffDisplay(null)).toEqual({
      title: 'Loading handoff',
      lines: ['Loading room handoff and review items.'],
      reviewItems: [],
    })
    expect(handoffDisplay({ lines: [] })).toEqual({
      title: 'Room memory',
      lines: ['No teammate activity to catch up on yet.'],
      reviewItems: [],
    })
    expect(handoffDisplay({ lines: [] }, { actionCount: 3 })).toEqual({
      title: 'Room memory',
      lines: ['No handoff summary yet. Review recent agent actions below.'],
      reviewItems: [],
    })
    expect(handoffDisplay({ lines: ['No teammate activity to catch up on yet.'] }, { actionCount: 3 })).toEqual({
      title: 'Room memory',
      lines: ['No handoff summary yet. Review recent agent actions below.'],
      reviewItems: [],
    })
  })

  it('deduplicates and folds long handoff summaries', () => {
    expect(compactHandoffLines([
      'Alice asked for status.',
      'Alice asked for status.',
      'Bob approved patch.',
      'Tests passed.',
      'Branch created.',
      'Review risk remains.',
    ])).toEqual({
      visible: [
        { raw: 'Alice asked for status.', text: 'Alice asked for status.' },
        { raw: 'Bob approved patch.', text: 'Bob approved patch.' },
      ],
      hidden: [
        { raw: 'Tests passed.', text: 'Tests passed.' },
        { raw: 'Branch created.', text: 'Branch created.' },
        { raw: 'Review risk remains.', text: 'Review risk remains.' },
      ],
      duplicateCount: 1,
    })
  })

  it('deduplicates near-identical handoff lines with different actor prefixes', () => {
    expect(compactHandoffLines([
      'Priya Nair asked VoiceOps to check status. I investigated **VoiceOps-AI-OnCall-Engineer** and all tests pass. Route: agent pipeline because no read-only route matched.',
      'VoiceOps: I investigated VoiceOps-AI-OnCall-Engineer and all tests pass.',
      'Bob approved the patch.',
    ])).toEqual({
      visible: [
        {
          raw: 'Priya Nair asked VoiceOps to check status. I investigated **VoiceOps-AI-OnCall-Engineer** and all tests pass. Route: agent pipeline because no read-only route matched.',
          text: 'Priya Nair asked VoiceOps to check status. I investigated VoiceOps-AI-OnCall-Engineer and all tests pass.',
        },
        { raw: 'Bob approved the patch.', text: 'Bob approved the patch.' },
      ],
      hidden: [],
      duplicateCount: 1,
    })
  })

  it('keeps handoff open items focused on the latest item with older items folded', () => {
    const html = renderRightRail({
      handoff: {
        lines: ['Alice requested a patch.'],
        open_review_items: [
          { id: 'old', kind: 'task', title: 'Old review item', detail: 'old detail', actor_name: 'Alice', status: 'open' },
          { id: 'mid', kind: 'risk', title: 'Middle review item', detail: 'middle detail', actor_name: 'Bob', status: 'open' },
          { id: 'new', kind: 'question', title: 'Newest review item', detail: 'new detail', actor_name: 'VoiceOps', status: 'open' },
        ],
      },
      actions: [],
      agentName: 'Ada',
    })

    expect(html).toContain('Handoff</span><small>3 open items</small>')
    expect(html).toContain('Open review')
    expect(html).toContain('Newest review item')
    expect(html).toContain('Ada · open')
    expect(html).not.toContain('VoiceOps · open')
    expect(html).toContain('title="Newest review item - new detail"')
    expect(html).not.toContain('<p title="new detail">new detail</p>')
    expect(html).toContain('2 more open items')
    expect(html).toContain('Middle review item')
    expect(html).toContain('Old review item')
    expect(html.indexOf('Newest review item')).toBeLessThan(html.indexOf('2 more open items'))
    expect(html.indexOf('2 more open items')).toBeLessThan(html.indexOf('Middle review item'))
  })

  it('keeps full handoff text available when visible lines are visually clamped', () => {
    const longLine = 'Alice asked VoiceOps to inspect a long approval chain with repository, branch, test, and review context.'
    const html = renderRightRail({
      handoff: { lines: [longLine] },
      actions: [],
    })

    expect(html).toContain(`title="${longLine}"`)
    expect(html).toContain(longLine)
  })

  it('keeps the right-rail handoff summary to one visible line by default', () => {
    const html = renderRightRail({
      handoff: {
        lines: [
          'Alice requested a patch for app.py.',
          'Bob approved the patch and tests passed.',
          'Priya noted one risk remains open.',
        ],
      },
      actions: [],
    })

    expect(html).toContain('Alice requested a patch for app.py.')
    expect(html).toContain('2 more handoff lines')
    expect(html.indexOf('Alice requested a patch for app.py.')).toBeLessThan(html.indexOf('2 more handoff lines'))
    expect(html.indexOf('2 more handoff lines')).toBeLessThan(html.indexOf('Bob approved the patch and tests passed.'))
  })

  it('displays cleaned markdown while preserving raw audit text in titles', () => {
    const html = renderRightRail({
      handoff: {
        lines: [
          'Priya asked VoiceOps to inspect **VoiceOps-AI-OnCall-Engineer** and `README.md`.',
        ],
        open_review_items: [
          {
            id: 'review-1',
            kind: 'risk',
            title: 'Review **health route**',
            detail: 'Check `app.py` before approval.',
            actor_name: 'Priya',
            status: 'open',
          },
        ],
      },
      actions: [
        {
          id: 'act-markdown-1',
          action: 'status',
          status: 'completed',
          requested_by_name: 'Priya Nair',
          summary: 'I investigated **VoiceOps-AI-OnCall-Engineer** and `README.md`.',
          files_changed: [],
        },
      ],
      memory: [
        {
          id: 'mem-markdown-1',
          kind: 'task',
          text: 'Fix **health route** in `app.py`.',
          actor_name: 'Priya',
          status: 'open',
        },
      ],
      workDashboard: {
        metrics: [],
        agents: [],
        approvals: [],
        open_items: [
          {
            id: 'open-1',
            kind: 'task',
            title: 'Fix **health route** in `app.py`.',
            actor_name: 'Priya',
            status: 'open',
          },
        ],
        recent_actions: [],
        recent_decisions: [],
      },
    })

    expect(html).toContain('Status checked')
    expect(html).not.toContain('I investigated VoiceOps-AI-OnCall-Engineer and README.md.</p>')
    expect(html).toContain('Fix health route in app.py.')
    expect(html).toContain('Review health route')
    expect(html).toContain('Check app.py before approval.')
    expect(html).toContain('title="I investigated **VoiceOps-AI-OnCall-Engineer** and `README.md`."')
    expect(html).toContain('title="Priya asked VoiceOps to inspect **VoiceOps-AI-OnCall-Engineer** and `README.md`."')
  })

  it('compacts noisy action and handoff copy without changing raw audit titles', () => {
    const raw = 'Priya Nair asked Ada to status. I investigated **VoiceOps-AI-OnCall-Engineer** in the VoiceOps-AI-OnCall-Engineer workspace. Key files: `README.md`.'
    const html = renderRightRail({
      handoff: {
        lines: [raw],
      },
      actions: [
        {
          id: 'act-compact-copy',
          action: 'status',
          status: 'completed',
          requested_by_name: 'Priya Nair',
          summary: raw,
          files_changed: [],
        },
      ],
      memory: [],
    })

    expect(html).toContain('Priya Nair asked Ada for status. Checked VoiceOps-AI-OnCall-Engineer · files: README.md.')
    expect(html).toContain('title="Priya Nair asked Ada to status. I investigated **VoiceOps-AI-OnCall-Engineer** in the VoiceOps-AI-OnCall-Engineer workspace. Key files: `README.md`."')
    expect(html).not.toContain('in the VoiceOps-AI-OnCall-Engineer workspace. Key files: README.md.')
  })

  it('compacts repeated completed agent actions but keeps pending approvals separate', () => {
    const repeated = {
      action: 'status',
      status: 'completed',
      requested_by_name: 'Priya Nair',
      summary: 'I investigated the workspace.',
      approval: {
        route_trace: {
          route: 'agent_pipeline',
          reason: 'fallback route',
        },
      },
      files_changed: [],
    }
    const compacted = compactRecentActions([
      { ...repeated, id: 'act-1' },
      { ...repeated, id: 'act-2' },
      { ...repeated, id: 'act-3' },
      { ...repeated, id: 'act-4', status: 'pending_approval', action: 'patch', pending_approval: true },
      { ...repeated, id: 'act-5', status: 'pending_approval', action: 'patch', pending_approval: true },
    ])

    expect(compacted).toHaveLength(3)
    expect(compacted[0].id).toBe('act-5')
    expect(compacted[1].id).toBe('act-4')
    expect(compacted[2].duplicateCount).toBe(3)
    expect(compacted[2].duplicateIds).toEqual(['act-3', 'act-2', 'act-1'])
    expect(splitActionRows(compacted).pending.map((action) => action.id)).toEqual(['act-5', 'act-4'])
    expect(splitActionRows(compacted).history.map((action) => action.id)).toEqual(['act-3'])
  })

  it('keeps pending approvals primary and folds action history', () => {
    const html = renderRightRail({
      actions: [
        {
          id: 'act-completed-history',
          action: 'test',
          status: 'completed',
          requested_by_name: 'Bob Lee',
          summary: 'Ran pytest after approval.',
          files_changed: [],
          approval: { test_command: 'python -m pytest -q' },
        },
        {
          id: 'act-pending-primary',
          action: 'patch',
          status: 'pending_approval',
          pending_approval: true,
          requested_by_name: 'Alice Chen',
          summary: 'Proposed health route fix.',
          files_changed: ['app.py'],
          approval: {
            diff: '--- a/app.py\n+++ b/app.py',
            test_command: 'python -m pytest -q',
          },
        },
      ],
    })

    const approvalQueueIndex = html.indexOf('aria-label="Approval queue: 1 pending"')
    const historyIndex = html.indexOf('<details class="action-history-details">')
    expect(html).toContain('aria-label="Approval queue: 1 pending"')
    expect(html).toContain('<span>Needs approval</span><small>1 pending</small>')
    expect(approvalQueueIndex).toBeGreaterThan(-1)
    expect(historyIndex).toBeGreaterThan(-1)
    expect(html.indexOf('<span>Needs approval</span>', approvalQueueIndex)).toBeLessThan(
      html.indexOf('Proposed health route fix.', approvalQueueIndex),
    )
    expect(html.indexOf('Approve</button>', approvalQueueIndex)).toBeLessThan(historyIndex)
    expect(html).toContain('<details class="action-history-details">')
    expect(html).not.toContain('<details class="action-history-details" open="">')
    expect(html).toContain('<span>Action history</span><small>1 recent</small>')
    expect(approvalQueueIndex).toBeLessThan(historyIndex)
    expect(html).not.toContain('No approval waiting')
  })

  it('shows a clear approval queue before folded history when no approval is pending', () => {
    const html = renderRightRail({
      actions: [
        {
          id: 'act-completed-only',
          action: 'status',
          status: 'completed',
          requested_by_name: 'Priya Nair',
          summary: 'Checked repository status.',
          files_changed: [],
        },
      ],
    })

    expect(html).toContain('aria-label="Approval queue: clear"')
    expect(html).toContain('No approval waiting')
    expect(html).toContain('<details class="action-history-details">')
    expect(html).toContain('<span>Action history</span><small>1 recent</small>')
    expect(html.indexOf('No approval waiting')).toBeLessThan(html.indexOf('Action history'))
  })

  it('keeps completed action audit details folded while preserving full summary text', () => {
    const summary = 'I investigated VoiceOps-AI-OnCall-Engineer and found the relevant files, tests, route trace, and follow-up state.'
    const html = renderRightRail({
      actions: [
        {
          id: 'act-status-1',
          action: 'status',
          status: 'completed',
          requested_by_name: 'Priya Nair',
          summary,
          files_changed: ['README.md'],
          approval: {
            test_command: 'npm run test:unit',
            route_trace: {
              route: 'agent_pipeline',
              action_policy: 'approval_required_if_code_changing',
              reason: 'No deterministic read-only meeting route matched.',
            },
          },
        },
      ],
    })

    expect(html).toContain(`title="${summary}"`)
    expect(html).toContain('1 file changed')
    expect(html).not.toContain('found the relevant files, tests, route trace, and follow-up state.</p>')
    expect(html).toContain('<details class="action-audit-details">')
    expect(html).not.toContain('<details class="action-audit-details" open="">')
    expect(html).toContain(
      '<div class="action-title"><b>Status check</b><small class="action-requester">Requested by Priya Nair</small></div><span class="action-status completed">completed</span>',
    )
    expect(html).not.toContain('<span class="mono">STATUS</span>')
    expect(html).toContain('aria-label="Action audit: routing · files · tests"')
    expect(html).toContain('<span>Action audit</span><small>routing · files · tests</small>')
    expect(html).toContain('agent pipeline · approval required if code changing')
    expect(html).toContain('Changed README.md')
    expect(html).toContain('Verification npm run test:unit')
  })

  it('uses compact action summaries while keeping raw action text auditable', () => {
    expect(actionSummaryLabel({
      action: 'patch',
      status: 'pending_approval',
      pending_approval: true,
      files_changed: ['app.py', 'tests/test_app.py'],
      summary: 'Proposed patch for app.py and tests/test_app.py.',
    })).toBe('2 files proposed · waiting for approval')

    expect(actionSummaryLabel({
      action: 'patch',
      status: 'completed',
      files_changed: ['app.py', 'tests/test_app.py', 'README.md'],
      summary: 'Checked VoiceOps-AI-OnCall-Engineer · files: app.py, tests/test_app.py, README.md · tests pass.',
      approval: { test_command: 'pytest -q' },
    })).toBe('3 files changed · tests passed')

    expect(actionSummaryLabel({
      action: 'status',
      status: 'completed',
      summary: 'Checked repository status. All 1 tests pass.',
      files_changed: [],
    })).toBe('Status checked · tests passed')
  })

  it('summarizes patch approval lifecycle before audit details', () => {
    expect(pendingApprovalBrief({
      action: 'patch',
      status: 'pending_approval',
      approval: { test_command: 'python -m pytest -q' },
    })).toBe('Approve creates a local branch, applies this diff, then runs python -m pytest -q. Reject leaves files unchanged.')
    expect(pendingApprovalBrief({
      action: 'status',
      status: 'pending_approval',
    })).toBe('')

    expect(actionLifecycleFacts({
      action: 'patch',
      status: 'pending_approval',
      pending_approval: true,
      files_changed: ['app.py'],
      approval: { test_command: 'python -m pytest -q' },
    })).toEqual([
      { key: 'pending', tone: 'pending', label: 'Waiting for approval' },
      { key: 'files', tone: 'neutral', label: '1 file proposed' },
      { key: 'tests', tone: 'neutral', label: 'Will run python -m pytest -q' },
    ])

    expect(actionLifecycleFacts({
      action: 'patch',
      status: 'completed',
      files_changed: ['app.py', 'tests/test_app.py'],
      approval: { status: 'approved', decided_by_name: 'Bob Lee', test_command: 'python -m pytest -q' },
    })).toEqual([
      { key: 'approved', tone: 'approved', label: 'Approved by Bob Lee' },
      { key: 'tests', tone: 'passed', label: 'Tests passed · python -m pytest -q' },
      { key: 'files', tone: 'neutral', label: '2 files changed' },
    ])

    expect(actionLifecycleFacts({
      action: 'patch',
      status: 'failed',
      files_changed: ['app.py'],
      approval: { status: 'approved', decided_by_name: 'Bob Lee', test_command: 'python -m pytest -q' },
    })[1]).toEqual({ key: 'tests', tone: 'failed', label: 'Tests failed · python -m pytest -q' })

    expect(actionLifecycleFacts({
      action: 'patch',
      status: 'rejected',
      files_changed: ['app.py'],
      approval: { status: 'rejected', decided_by_name: 'Alice Chen' },
    })[0]).toEqual({ key: 'rejected', tone: 'rejected', label: 'Rejected by Alice Chen' })
  })

  it('explains exactly what approve and reject do for pending patch proposals', () => {
    const html = renderRightRail({
      actions: [
        {
          id: 'act-pending-brief',
          action: 'patch',
          status: 'pending_approval',
          pending_approval: true,
          requested_by_name: 'Alice Chen',
          summary: 'Proposed health route fix.',
          files_changed: ['app.py'],
          approval: {
            diff: '--- a/app.py\n+++ b/app.py',
            test_command: 'python -m pytest -q',
            external_agent: {
              label: 'Claude Code',
              provider: 'claude',
            },
          },
        },
      ],
    })

    expect(html).toContain('Requested by Alice Chen')
    expect(html).toContain('Proposed by Claude Code')
    expect(html).toContain('aria-label="Approval effect"')
    expect(html).toContain('Approve creates a local branch, applies this diff, then runs python -m pytest -q. Reject leaves files unchanged.')
    expect(html.indexOf('Approval effect')).toBeLessThan(html.indexOf('Diff preview'))
    expect(html.indexOf('Approve</button>')).toBeLessThan(html.indexOf('Diff preview'))
    expect(html.indexOf('Reject</button>')).toBeLessThan(html.indexOf('Diff preview'))
    expect(html).toContain('Diff preview')
    expect(html).toContain('<pre class="diff-preview" id="diff-act-pending-brief" hidden="">')
  })

  it('only enables real PR creation when gh preflight is ready', () => {
    expect(pullRequestPlanState({
      ready: true,
      mode: 'dry_run',
      preflight: {
        real_creation_ready: true,
        github_cli_authenticated: true,
      },
    })).toMatchObject({
      canCreate: true,
      title: 'GitHub PR ready',
    })

    expect(pullRequestPlanState({
      ready: true,
      mode: 'dry_run',
      preflight: {
        real_creation_enabled: true,
        real_creation_ready: false,
        github_cli_authenticated: false,
        github_cli_auth_detail: 'not logged in',
      },
    })).toEqual({
      canCreate: false,
      title: 'GitHub PR plan ready',
      detail: 'not logged in',
    })

    expect(pullRequestPlanState({
      ready: false,
      blockers: ['Commit the approved patch before opening a pull request.'],
    })).toEqual({
      canCreate: false,
      title: 'GitHub PR blocked',
      detail: 'Commit the approved patch before opening a pull request.',
    })
  })

  it('summarizes pull request readiness from preflight without hiding dry-run mode', () => {
    const plan = {
      ready: true,
      mode: 'dry_run',
      preflight: {
        head_branch_present: true,
        commit_present: true,
        remote_present: true,
        remote_is_github: true,
        github_cli_available: true,
        github_cli_authenticated: false,
        real_creation_enabled: false,
        real_creation_ready: false,
      },
    }
    const facts = pullRequestReadinessFacts(plan)
    const html = renderToStaticMarkup(<PullRequestReadiness plan={plan} />)

    expect(facts.map((fact) => fact.label)).toEqual([
      'branch ready',
      'commit ready',
      'GitHub remote',
      'gh login needed',
      'dry-run only',
    ])
    expect(html).toContain('aria-label="Pull request readiness"')
    expect(html).toContain('branch ready')
    expect(html).toContain('gh login needed')
    expect(html).toContain('dry-run only')
  })

  it('surfaces real pull request blockers before generic auth detail', () => {
    expect(pullRequestPlanState({
      ready: true,
      mode: 'dry_run',
      preflight: {
        real_creation_ready: false,
        real_creation_enabled: true,
        github_cli_auth_detail: 'not logged in',
        real_creation_blockers: ['Run gh auth login before creating a real PR.'],
      },
    })).toEqual({
      canCreate: false,
      title: 'GitHub PR plan ready',
      detail: 'Run gh auth login before creating a real PR.',
    })
  })

  it('renders approval lifecycle facts ahead of folded audit details', () => {
    const html = renderRightRail({
      actions: [
        {
          id: 'act-approved-1',
          action: 'patch',
          status: 'completed',
          requested_by_name: 'Alice Chen',
          summary: 'Bob approved the health route patch.',
          files_changed: ['app.py'],
          approval: {
            status: 'approved',
            decided_by_name: 'Bob Lee',
            test_command: 'python -m pytest -q',
            route_trace: {
              route: 'agent_pipeline',
              action_policy: 'approval_required_if_code_changing',
            },
          },
        },
      ],
    })

    expect(html).toContain('aria-label="Action lifecycle summary"')
    expect(html).toContain('Approved by Bob Lee')
    expect(html).toContain('Tests passed · python -m pytest -q')
    expect(html).toContain('1 file changed')
    expect(html.indexOf('Approved by Bob Lee')).toBeLessThan(html.indexOf('<span>Action audit</span>'))
    expect(html).toContain('<details class="action-audit-details">')
    expect(html).not.toContain('<details class="action-audit-details" open="">')
  })

  it('uses compact user-facing copy for repeated agent action groups', () => {
    const repeated = {
      action: 'status',
      status: 'completed',
      requested_by_name: 'Priya Nair',
      summary: 'I investigated the workspace.',
      files_changed: [],
    }
    const html = renderRightRail({
      actions: [
        { ...repeated, id: 'act-1' },
        { ...repeated, id: 'act-2' },
        { ...repeated, id: 'act-3' },
      ],
    })

    expect(html).toContain('3 similar actions')
    expect(html).toContain('Compacted repeated completed actions.')
    expect(html).toContain('<span>Action audit</span><small>repeated</small>')
    expect(html).not.toContain('class="action-repeat"')
    expect(html).not.toContain('Repeated 3 times')
  })

  it('keeps agent action history short while preserving pending approvals', () => {
    const completed = (index) => ({
      id: `done-${index}`,
      action: 'status',
      status: 'completed',
      requested_by_name: 'Priya Nair',
      summary: `Completed read-only action ${index}`,
      files_changed: [],
    })
    const compacted = compactRecentActions([
      completed(1),
      completed(2),
      completed(3),
      completed(4),
      {
        id: 'approval-1',
        action: 'patch',
        status: 'pending_approval',
        pending_approval: true,
        requested_by_name: 'Bob Lee',
        summary: 'Fix the failing test',
        files_changed: ['app.py'],
      },
    ])

    expect(compacted.map((item) => item.id)).toEqual(['approval-1', 'done-4', 'done-3'])
  })

  it('uses stable labels when an agent action is missing requester or summary fields', () => {
    const html = renderRightRail({
      actions: [
        {
          id: 'act-incomplete-1',
          action: 'status',
          status: 'completed',
          files_changed: [],
        },
      ],
    })

    expect(html).toContain('Unknown requester')
    expect(html).toContain('No action summary provided.')
    expect(html).toContain('title="No action summary provided."')
  })

  it('shows git status metadata only when changed files are available', () => {
    expect(gitStatusAfterLabel({ status_after: [{ path: 'app.py' }, { path: 'tests/test_app.py' }] }))
      .toBe('2 changed after approval')
    expect(gitStatusAfterLabel({ status_after: [] })).toBe('')
    expect(gitStatusAfterLabel({ status_after: 'M app.py' })).toBe('')

    const html = renderRightRail({
      actions: [
        {
          id: 'act-git-1',
          action: 'patch',
          status: 'completed',
          requested_by_name: 'Bob',
          summary: 'Approved health fix.',
          files_changed: ['app.py'],
          approval: {
            git: {
              branch_name: 'voiceops/act-git-1-health',
              status_after: [{ path: 'app.py' }],
            },
          },
        },
        {
          id: 'act-git-2',
          action: 'patch',
          status: 'completed',
          requested_by_name: 'Alice',
          summary: 'No dirty files after approval.',
          files_changed: [],
          approval: {
            git: {
              branch_name: 'voiceops/act-git-2-clean',
              status_after: [],
            },
          },
        },
      ],
    })

    expect(html).toContain('1 changed after approval')
    expect(html).not.toContain('0 files after')
  })

  it('links diff preview toggles to their diff region', () => {
    const html = renderRightRail({
      actions: [
        {
          id: 'act-patch-1',
          action: 'patch',
          status: 'pending_approval',
          requested_by_name: 'Alice Chen',
          summary: 'Proposed health route fix.',
          files_changed: ['app.py'],
          approval: {
            diff: '--- a/app.py\n+++ b/app.py',
          },
        },
      ],
    })

    expect(html).toContain('aria-label="Toggle diff preview for act-patch-1"')
    expect(html).toContain('aria-controls="diff-act-patch-1"')
    expect(html).toContain('id="diff-act-patch-1"')
  })

  it('announces inline rail errors as alerts', () => {
    const source = readFileSync(new URL('./RightRail.jsx', import.meta.url), 'utf8')

    expect(source).toContain('className="memory-error" role="alert"')
    expect(source).toContain('className="approval-error" role="alert"')
    expect(source).toContain('className="workdash-error" role="alert"')
    expect(source).toContain('className="runtime-warning" role="alert"')
  })

  it('keeps secondary loading panels hidden before the room snapshot loads', () => {
    const html = renderRightRail({
      handoff: undefined,
      actions: undefined,
      memory: undefined,
    })

    expect(html).toContain('Syncing dashboard')
    expect(html).toContain('Syncing room, queue, and agents.')
    expect(html).not.toContain('Loading current memory.')
    expect(html).not.toContain('Loading agent actions and approvals.')
    expect(html).not.toContain('Loading handoff')
    expect(html).not.toContain('Agent actions')
    expect(html).not.toContain('Meeting memory')
    expect(html).not.toContain('Decisions, questions, tasks, and code references will collect here.')
    expect(html).not.toContain('Agent edits, tests, and approvals will appear here.')
  })

  it('keeps meeting memory API calls scoped to the active room id', () => {
    const source = readFileSync(new URL('./RightRail.jsx', import.meta.url), 'utf8')

    expect(source).toContain('function MemoryPanel({ memory, agentName, roomId = \'main\' })')
    expect(source).toContain('const activeRoomId = roomId || \'main\'')
    expect(source).toContain('<MemoryPanel key={roomId || \'main\'} memory={memory} agentName={agentName} roomId={roomId} />')
    expect(source).not.toContain('fetchRoomMemoryHealth(\'main\')')
    expect(source).not.toContain('fetchRoomMemory(\'main\'')
    expect(source).not.toContain('routeRoomCommand(\'main\'')
    expect(source).not.toContain('queryRoomAudit(\'main\'')
    expect(source).not.toContain('queryRoomProvenance(\'main\'')
    expect(source).not.toContain('queryRoomMemory(\'main\'')
    expect(source).not.toContain('rebuildRoomRagIndex(\'main\'')
  })

  it('renders a clean room invite link for teammates', () => {
    expect(roomInviteUrl('alpha-team', {
      origin: 'https://voiceops.test',
      pathname: '/console',
      search: '?token=secret&theme=dark',
    })).toBe('https://voiceops.test/console?theme=dark&room=alpha-team')

    const html = renderRightRail({ roomId: 'alpha-team' })
    expect(html).toContain('Team invite')
    expect(html).toContain('Copy invite')
  })

  it('keeps recent meeting memory folded by default', () => {
    const html = renderRightRail({
      memory: [
        {
          id: 'mem-1',
          kind: 'task',
          text: 'Fix the health route',
          actor_name: 'Alice',
          status: 'open',
        },
        {
          id: 'mem-2',
          kind: 'decision',
          text: 'Keep preview-first approvals',
          actor_name: 'Bob',
          status: 'noted',
        },
      ],
    })

    expect(html).toContain('<details class="memory-details">')
    expect(html).toContain('Recent memory')
    expect(html).toContain('2 shown')
    expect(html).not.toContain('<details class="memory-details" open="">')
  })

  it('keeps system details folded while preserving microphone recovery guidance', () => {
    const html = renderRightRail({
      workspace: { connected: true, name: 'voiceops' },
      liveDiagnostics: {
        secureContext: true,
        getUserMedia: true,
        mediaRecorder: true,
        instantCaptions: true,
        phase: 'idle',
        lastEvent: 'microphone_NotAllowedError',
        lastErrorKind: 'NotAllowedError',
        lastErrorMessage: 'Microphone permission is blocked.',
        recoveryHint: 'Open browser site settings and allow microphone access for this local app.',
      },
    })

    expect(html).toContain('<span>System details</span><small>mic issue</small>')
    expect(html).not.toContain('<details class="rail-section-details" open="">')
    expect(html).toContain('aria-label="Runtime diagnostics: mic issue"')
    expect(html).toContain('<details class="diagnostic-details">')
    expect(html).not.toContain('<details class="diagnostic-details" open="">')
    expect(html).toContain('System summary')
    expect(html).toContain('mic issue')
    expect(html).toContain('Voice')
    expect(html).toContain('needs setup')
    expect(html).toContain('mic permission')
    expect(html).toContain('title="microphone_NotAllowedError"')
    expect(html).not.toContain('<b>microphone_NotAllowedError</b>')
    expect(html).toContain('Microphone permission is blocked.')
    expect(html).toContain('Open browser site settings')
    expect(html).toContain('aria-label="Primary live meeting setup issue"')
    expect(html).toContain('Live meeting setup diagnostics')
    expect(html).toContain('<summary><span>All setup checks</span><small>1 blocked</small></summary>')
    expect(html).toContain('Browser permission')
    expect(html).toContain('Allow microphone in browser site settings.')
    expect(html).toContain('Mac input')
    expect(html).toContain('Recorder')
    expect(html).toContain('Live socket')
    expect(html).toContain('Command fallback')
    expect(html).toContain('Type while voice is unavailable')
    expect(html).toContain('Code-changing requests still require approval.')
    expect(html).not.toContain('Awaiting your call')
    expect(html).not.toContain('sandbox repo')
  })

  it('separates unavailable browser microphone APIs from permission recovery', () => {
    expect(liveSetupDiagnosticItems({
      secureContext: true,
      getUserMedia: false,
      mediaRecorder: true,
      lastErrorKind: 'get-user-media-unavailable',
      lastErrorMessage: 'This browser cannot use the microphone. Type a command below.',
    })).toEqual([
      expect.objectContaining({ id: 'page', ready: true }),
      expect.objectContaining({
        id: 'browser-api',
        ready: false,
        label: 'Browser mic API',
        detail: 'Open this local console in Chrome or Safari for voice capture.',
      }),
      expect.objectContaining({ id: 'browser-permission', ready: true }),
      expect.objectContaining({ id: 'mac-input', ready: true }),
      expect.objectContaining({ id: 'recorder', ready: true }),
      expect.objectContaining({ id: 'live-socket', ready: true }),
    ])

    const html = renderRightRail({
      workspace: { connected: true, name: 'voiceops' },
      liveDiagnostics: {
        secureContext: true,
        getUserMedia: false,
        mediaRecorder: true,
        instantCaptions: true,
        phase: 'idle',
        lastEvent: 'get-user-media-unavailable',
        lastErrorKind: 'get-user-media-unavailable',
        lastErrorMessage: 'This browser cannot use the microphone. Type a command below.',
        recoveryHint: 'Typed commands stay enabled. For voice, copy this link and open it in Chrome or Safari.',
      },
    })

    expect(html).toContain('Browser mic API')
    expect(html).toContain('Open this local console in Chrome or Safari for voice capture.')
    expect(html).toContain('Typed commands stay enabled.')
    expect(html).not.toContain('browser context')
    expect(html).not.toContain('Allow microphone in browser site settings.')
  })

  it('keeps input constraint failures out of browser API recovery', () => {
    expect(liveSetupDiagnosticItems({
      secureContext: true,
      getUserMedia: true,
      mediaRecorder: true,
      lastErrorKind: 'OverconstrainedError',
      lastErrorMessage: 'The selected microphone input cannot satisfy the browser audio settings.',
    })).toEqual([
      expect.objectContaining({ id: 'page', ready: true }),
      expect.objectContaining({ id: 'browser-permission', ready: true }),
      expect.objectContaining({
        id: 'mac-input',
        ready: false,
        detail: 'Select an input or close apps using the mic.',
      }),
      expect.objectContaining({ id: 'recorder', ready: true }),
      expect.objectContaining({ id: 'live-socket', ready: true }),
    ])

    const html = renderRightRail({
      workspace: { connected: true, name: 'voiceops' },
      liveDiagnostics: {
        secureContext: true,
        getUserMedia: true,
        mediaRecorder: true,
        instantCaptions: true,
        phase: 'idle',
        lastEvent: 'microphone_NotSupportedError',
        lastErrorKind: 'NotSupportedError',
        lastErrorMessage: 'Live audio capture is not supported in this browser session.',
        recoveryHint: 'Typed commands stay enabled here. For voice, open this local app in Chrome or Safari.',
      },
    })
    expect(html).toContain('System details')
    expect(html).toContain('audio session issue')
    expect(html).toContain('<span>Voice</span><b>needs setup</b>')
    expect(html).not.toContain('<span>Voice</span><b>ready</b>')
  })

  it('keeps runtime microphone readiness aligned with setup blockers', () => {
    expect(liveDiagnosticChecks({
      secureContext: true,
      getUserMedia: true,
      mediaRecorder: true,
      instantCaptions: true,
      lastErrorKind: 'NotAllowedError',
      lastErrorMessage: 'Microphone permission is blocked.',
    })).toEqual([
      { label: 'Page', ready: true },
      { label: 'Mic', ready: false },
      { label: 'Recorder', ready: true },
      { label: 'Captions', ready: true },
    ])

    expect(liveDiagnosticChecks({
      secureContext: true,
      getUserMedia: true,
      mediaRecorder: true,
      instantCaptions: true,
      lastErrorKind: 'socket_error',
      lastErrorMessage: 'Live meeting connection interrupted',
    }).find((item) => item.label === 'Mic')).toEqual({ label: 'Mic', ready: true })
  })

  it('labels runtime API failures without blaming the microphone', () => {
    expect(diagnosticsErrorLabel('api-runtime-unavailable')).toBe('API error')
    expect(diagnosticsErrorLabel('socket_error')).toBe('Socket error')
    expect(diagnosticsErrorLabel('media-recorder-unavailable')).toBe('Recorder error')
    expect(diagnosticsErrorLabel('NotSupportedError')).toBe('Audio session error')
    expect(diagnosticsErrorLabel('NotAllowedError')).toBe('Mic error')
    expect(diagnosticsErrorLabel('OverconstrainedError')).toBe('Mic error')
    expect(diagnosticsEventLabel('microphone_NotAllowedError')).toBe('mic permission')
    expect(diagnosticsEventLabel('microphone_OverconstrainedError')).toBe('mic input')
    expect(diagnosticsEventLabel('microphone_NotSupportedError')).toBe('audio session')
    expect(diagnosticsEventLabel('socket_error')).toBe('socket')

    const html = renderRightRail({
      workspace: { connected: true, name: 'voiceops' },
      liveDiagnostics: {
        secureContext: true,
        getUserMedia: true,
        mediaRecorder: true,
        instantCaptions: true,
        phase: 'idle',
        lastEvent: 'api-runtime-unavailable',
        lastErrorKind: 'api-runtime-unavailable',
        recoveryHint: 'Open the app in Chrome, Safari, or Edge.',
      },
    })

    expect(html).toContain('API error')
    expect(html).toContain('API unavailable')
    expect(html).toContain('Runtime diagnostics need attention.')
    expect(html).not.toContain('Mic error')
    expect(html).not.toContain('Microphone capture is unavailable.')
    expect(html).not.toContain('Live meeting setup diagnostics')
    expect(html).not.toContain('Browser permission')
  })

  it('keeps command fallback copy neutral when voice is healthy', () => {
    expect(commandFallbackCopy(
      { connected: true },
      { getUserMedia: true, mediaRecorder: true, lastErrorKind: '' },
    )).toEqual({
      title: 'Typed command fallback',
      detail: 'Use typed commands for memory, tests, and approved code actions.',
    })

    const html = renderRightRail({
      workspace: { connected: true, name: 'voiceops' },
      liveDiagnostics: {
        secureContext: true,
        getUserMedia: true,
        mediaRecorder: true,
        instantCaptions: true,
        phase: 'idle',
        lastEvent: 'idle',
      },
    })

    expect(html).not.toContain('Typed command fallback')
    expect(html).not.toContain('Use typed commands for memory, tests, and approved code actions.')
    expect(html).not.toContain('Command fallback')
    expect(html).not.toContain('Runtime diagnostics')
    expect(html).not.toContain('Type while voice is unavailable')
  })

  it('treats unsupported live audio sessions as voice-unavailable fallback', () => {
    expect(commandFallbackCopy(
      { connected: true },
      {
        getUserMedia: true,
        mediaRecorder: true,
        lastErrorKind: 'NotSupportedError',
      },
    )).toEqual({
      title: 'Type while voice is unavailable',
      detail: 'Use typed commands or retry live meeting. Code-changing requests still require approval.',
    })

    const checks = liveDiagnosticChecks({
      secureContext: true,
      getUserMedia: true,
      mediaRecorder: true,
      instantCaptions: true,
      lastErrorKind: 'NotSupportedError',
      lastErrorMessage: 'Live audio capture is not supported in this browser session.',
    })
    expect(checks.find((item) => item.label === 'Mic')).toEqual({ label: 'Mic', ready: false })
    expect(checks.find((item) => item.label === 'Recorder')).toEqual({ label: 'Recorder', ready: true })
    expect(liveSetupDiagnosticItems({
      secureContext: true,
      getUserMedia: true,
      mediaRecorder: true,
      lastErrorKind: 'NotSupportedError',
      lastErrorMessage: 'Live audio capture is not supported in this browser session.',
    })).toEqual([
      expect.objectContaining({ id: 'page', ready: true }),
      expect.objectContaining({ id: 'browser-permission', ready: true }),
      expect.objectContaining({ id: 'mac-input', ready: true }),
      expect.objectContaining({
        id: 'audio-session',
        ready: false,
        detail: 'This browser session could not start live audio.',
      }),
      expect.objectContaining({ id: 'recorder', ready: true }),
      expect.objectContaining({ id: 'live-socket', ready: true }),
    ])
  })

  it('separates live socket recovery from microphone permission recovery', () => {
    expect(liveSetupDiagnosticItems({
      secureContext: true,
      getUserMedia: true,
      mediaRecorder: true,
      lastErrorKind: 'socket_error',
      lastErrorMessage: 'Live meeting connection interrupted',
    })).toEqual([
      expect.objectContaining({ id: 'page', ready: true }),
      expect.objectContaining({ id: 'browser-permission', ready: true }),
      expect.objectContaining({ id: 'mac-input', ready: true }),
      expect.objectContaining({ id: 'recorder', ready: true }),
      expect.objectContaining({
        id: 'live-socket',
        ready: false,
        detail: 'Check backend live meeting websocket.',
      }),
    ])

    const html = renderRightRail({
      workspace: { connected: true, name: 'voiceops' },
      liveDiagnostics: {
        secureContext: true,
        getUserMedia: true,
        mediaRecorder: true,
        instantCaptions: true,
        phase: 'idle',
        lastEvent: 'socket_error',
        lastErrorKind: 'socket_error',
        lastErrorMessage: 'Live meeting connection interrupted',
      },
    })

    expect(html).toContain('Socket error')
    expect(html).toContain('Live meeting setup diagnostics')
    expect(html).toContain('Live socket')
    expect(html).toContain('Check backend live meeting websocket.')
    expect(html).toContain('Type while live reconnects')
    expect(html).toContain('Use typed commands while the live meeting connection recovers.')
    expect(html).not.toContain('Allow microphone in browser site settings.')
  })

  it('summarizes connected idle readiness without expanding runtime diagnostics', () => {
    const html = renderRightRail({
      workspace: {
        connected: true,
        source: 'room',
        path: '/tmp/voiceops-room',
        name: 'voiceops',
        branch: 'main',
        remote_url: 'https://github.com/team/voiceops.git',
      },
      liveDiagnostics: {
        secureContext: true,
        getUserMedia: true,
        mediaRecorder: true,
        instantCaptions: true,
        phase: 'idle',
        lastEvent: 'idle',
      },
    })

    expect(html).toContain('GitHub repo connected')
    expect(html).toContain('repo-flow ready compact')
    expect(html).toContain('main · GitHub repo')
    expect(html).toContain('Active repository binding')
    expect(html).toContain('room repo')
    expect(html).toContain('/tmp/voiceops-room')
    expect(html).toContain('VOICEOPS_WORKSPACE points to GitHub')
    expect(html).toContain('<small>repo ok · connect coding agent</small>')
    expect(html).toContain('System summary')
    expect(html).toContain('Workspace')
    expect(html).toContain('connected')
    expect(html).toContain('Voice')
    expect(html).toContain('ready')
    expect(html).not.toContain('<details class="diagnostic-details">')
    expect(html).not.toContain('Runtime diagnostics')
    expect(html).not.toContain('Command fallback')
  })

  it('labels repository binding source without adding tenant abstractions', () => {
    expect(repositoryBindingSummary({
      connected: true,
      source: 'configured',
      path: '/repo/env',
      remote_url: 'git@github.com:team/app.git',
    })).toEqual({
      source: 'env workspace',
      path: '/repo/env',
      remote: 'git@github.com:team/app.git',
    })
    expect(repositoryBindingSummary({
      connected: true,
      source: 'room',
      configured_workspace: '/repo/room',
      remote_kind: 'github',
    })).toEqual({
      source: 'room repo',
      path: '/repo/room',
      remote: 'GitHub remote',
    })
    expect(repositoryBindingSummary({ connected: false })).toBeNull()
  })

  it('makes the folded agent setup summary identify agent counts', () => {
    const html = renderRightRail({
      workspace: { connected: true, name: 'voiceops', branch: 'main', remote_url: 'https://github.com/team/voiceops.git' },
      externalAgentProviders: [
        externalProvider({ provider: 'claude', label: 'Claude Code', connected: true }),
        externalProvider({ provider: 'codex', label: 'OpenAI Codex', connected: false }),
      ],
    })

    expect(html).toContain('<span>Agent setup</span>')
    expect(html).toContain('<small>repo ok · 1 patch ready · 1 needs setup</small>')
    expect(html).not.toContain('repo connected · 1 connected · 1 to set up')
  })

  it('keeps repository workflow honest when no GitHub remote is configured', () => {
    expect(repositoryFlowCopy({
      connected: true,
      branch: 'voiceops/test',
      remote_url: '',
    })).toEqual({
      tone: 'local',
      title: 'Local git repo connected',
      meta: 'voiceops/test · no remote',
      detail: 'Approved patches can create local branches. Add a GitHub remote later when PRs are needed.',
    })

    const html = renderRightRail({
      workspace: { connected: true, name: 'voiceops', branch: 'voiceops/test' },
    })
    expect(html).toContain('Local git repo connected')
    expect(html).toContain('repo-flow local compact')
    expect(html).toContain('voiceops/test · no remote')
    expect(html).toContain('Add a GitHub remote later when PRs are needed.')
  })

  it('shows the concrete PR path for connected repositories', () => {
    expect(repositoryAuthBoundary({
      connected: true,
      remote_kind: 'github',
      remote_url: 'https://github.com/team/voiceops.git',
    })).toEqual({
      mode: 'GitHub API',
      detail: 'VoiceOps uses GitHub sign-in, GITHUB_TOKEN, or GH_TOKEN for private repos and PR creation.',
    })
    expect(repositoryAuthBoundary({
      connected: true,
      remote_url: '',
    })).toEqual({
      mode: 'local git',
      detail: 'Approved patches stay local until you add a GitHub origin remote.',
    })

    expect(repositoryNextSteps({
      connected: true,
      branch: 'main',
      remote_kind: 'github',
      remote_url: 'https://github.com/team/voiceops.git',
    }).map((step) => step.label)).toEqual([
      'Approved patch creates branch',
      'PR opens on approval',
      'Use GitHub checks',
      'Review PR explicitly',
    ])

    expect(repositoryNextSteps({
      connected: true,
      branch: 'main',
      remote_url: 'git@example.com:team/voiceops.git',
    }).map((step) => step.label)).toEqual([
      'Approved patch creates branch',
      'Commit approved patch',
      'Add GitHub origin remote',
      'PR after GitHub remote',
    ])

    const html = renderRightRail({
      workspace: {
        connected: true,
        name: 'voiceops',
        branch: 'main',
        remote_kind: 'github',
        remote_url: 'https://github.com/team/voiceops.git',
      },
    })
    expect(html).toContain('aria-label="Repository workflow steps"')
    expect(html).toContain('PR opens on approval')
    expect(html).toContain('Use GitHub checks')
    expect(html).toContain('GitHub API')
    expect(html).toContain('GitHub sign-in')
  })

  it('does not present a plain local folder as branch-ready', () => {
    expect(repositoryFlowCopy({
      connected: true,
      is_git_repo: false,
      name: 'voiceops',
    })).toEqual({
      tone: 'blocked',
      title: 'Local folder connected',
      meta: 'git not initialized',
      detail: 'VoiceOps can read files. Initialize git in this folder before approved patches create branches or PR plans.',
    })

    const html = renderRightRail({
      workspace: { connected: true, is_git_repo: false, name: 'voiceops' },
    })

    expect(html).toContain('Local folder connected')
    expect(html).toContain('git not initialized')
    expect(html).toContain('Initialize git in this folder')
    expect(html).not.toContain('Local git repo connected')
  })

  it('summarizes external agent routing without opening setup panels', () => {
    expect(agentRoutingFlowCopy(null, 'Ada')).toEqual({
      tone: 'local',
      title: 'Checking coding agents',
      meta: 'routes loading',
      detail: 'Repo, memory, and typed commands work now. Agent assignment unlocks when provider routes finish loading.',
    })

    const loadingHtml = renderRightRail({
      externalAgentProviders: null,
    })
    expect(loadingHtml).toContain('Checking coding agents')
    expect(loadingHtml).toContain('routes loading')
    expect(loadingHtml).toContain('agent-route-flow local')
    expect(loadingHtml).toContain('Agent setup')
    expect(loadingHtml).toContain('checking code work')
    expect(loadingHtml).toContain('Repo, memory, and typed commands work now.')
    expect(loadingHtml).not.toContain('Agent routing loading')
    expect(loadingHtml).not.toContain('Agent assignment off')

    expect(agentRoutingFlowCopy([
      externalProvider({
        provider: 'claude',
        label: 'Claude Code',
        connected: true,
      }),
      externalProvider({
        provider: 'codex',
        label: 'OpenAI Codex',
        connected: true,
        mode_readiness: {
          review: { ready: true, detail: 'ready' },
          explain: { ready: true, detail: 'ready' },
          test: { ready: true, detail: 'ready' },
          patch: { ready: false, detail: 'Patch mode requires local CLI.' },
        },
      }),
    ], 'Ada')).toEqual({
      tone: 'ready',
      title: 'Coding agents ready',
      meta: '1 patch · 2/2 connected',
      detail: 'Claude Code can propose patches. Every file change still waits for approval.',
    })

    const html = renderRightRail({
      workDashboard: {
        metrics: [],
        agents: [],
        approvals: [],
        open_items: [],
        recent_actions: [],
        recent_decisions: [],
      },
      externalAgentProviders: [
        externalProvider({
          provider: 'claude',
          label: 'Claude Code',
          connected: true,
        }),
        externalProvider({
          provider: 'codex',
          label: 'OpenAI Codex',
          connected: true,
          mode_readiness: {
            review: { ready: true, detail: 'ready' },
            explain: { ready: true, detail: 'ready' },
            test: { ready: true, detail: 'ready' },
            patch: { ready: false, detail: 'Patch mode requires local CLI.' },
          },
        }),
      ],
    })
    expect(html).not.toContain('Coding agents ready')
    expect(html.indexOf('1 patch · 2/2 connected')).toBeLessThan(html.indexOf('Work dashboard'))
    expect(html).not.toContain('Every file change still waits for approval.')
  })

  it('makes read-only coding agent state action-oriented when patch mode is missing', () => {
    expect(agentRoutingFlowCopy([
      externalProvider({
        provider: 'claude',
        label: 'Claude Code',
        connected: true,
        mode_readiness: {
          review: { ready: true, detail: 'ready' },
          explain: { ready: true, detail: 'ready' },
          test: { ready: true, detail: 'ready' },
          patch: { ready: false, detail: 'Patch mode requires local CLI.' },
        },
      }),
      externalProvider({
        provider: 'codex',
        label: 'OpenAI Codex',
        connected: false,
      }),
    ], 'Ada')).toEqual({
      tone: 'local',
      title: 'Patch agent needed',
      meta: '1 read-only · 1/2 connected',
      detail: 'Review, explain, and tests can run. Connect a local coding agent before assigning patch work.',
    })

    const html = renderRightRail({
      workDashboard: {
        metrics: [],
        agents: [],
        approvals: [],
        open_items: [],
        recent_actions: [],
        recent_decisions: [],
      },
      externalAgentProviders: [
        externalProvider({
          provider: 'claude',
          label: 'Claude Code',
          connected: true,
          mode_readiness: {
            review: { ready: true, detail: 'ready' },
            explain: { ready: true, detail: 'ready' },
            test: { ready: true, detail: 'ready' },
            patch: { ready: false, detail: 'Patch mode requires local CLI.' },
          },
        }),
        externalProvider({
          provider: 'codex',
          label: 'OpenAI Codex',
          connected: false,
        }),
      ],
    })

    expect(html).toContain('Patch agent needed')
    expect(html).toContain('1 read-only · 1/2 connected')
    expect(html).toContain('Connect a local coding agent before assigning patch work.')
    expect(html).toContain('Agents 1 read-only, patch setup needed')
    expect(html).toContain('Patch mode requires local CLI.')
    expect(html).not.toContain('Review agents ready')
  })

  it('uses concise patch assignment blocker copy', () => {
    expect(assignmentReadinessMessage({
      ready: false,
      detail: 'Patch mode requires local CLI.',
    }, 'patch')).toBe('Patch mode requires local CLI.')
    expect(assignmentReadinessMessage({ ready: false }, 'patch')).toBe(
      'Patch work needs a patch-ready local CLI agent.',
    )
    expect(assignmentReadinessMessage({
      ready: false,
      detail: 'Selected provider only supports review.',
    }, 'patch')).toBe(
      'Patch work needs a patch-ready local CLI agent. Selected provider only supports review.',
    )
  })

  it('shows local repository setup steps when no workspace is connected', () => {
    const html = renderRightRail({
      workspace: { connected: false, name: '' },
    })

    expect(html).toContain('Repo not connected')
    expect(html).toContain('Repository setup')
    expect(html).toContain('Connect code actions by pointing the backend at a GitHub repository URL or an existing local clone.')
    expect(html).toContain('Repository setup steps')
    expect(html).toContain('Connect a GitHub repo URL, or open an existing local clone')
    expect(html).toContain('Set <code>VOICEOPS_WORKSPACE</code> to the GitHub URL or local folder')
    expect(html).toContain('Restart backend, then refresh this console')
    expect(html).not.toContain('Use Connect below')
    expect(html).toContain('VOICEOPS_WORKSPACE=https://github.com/org/repo.git')
    expect(html).toContain('# local fallback: git clone https://github.com/org/repo.git /absolute/path/to/repo')
    expect(html).toContain('GitHub URLs stay remote')
    expect(html).not.toContain('Local repository path')
    expect(html.indexOf('Repository setup')).toBeGreaterThan(html.indexOf('Repo not connected'))
    expect(html.indexOf('Approval')).toBeLessThan(html.indexOf('Repository setup'))
    expect(html.indexOf('Repository setup')).toBeGreaterThan(html.indexOf('<span>Agent setup</span>'))
    expect(html.match(/class="repo-setup"/g)).toHaveLength(1)
  })

  it('lets admins connect an existing local workspace from repository setup', () => {
    const html = renderRightRail({
      user: { permissions: ['admin:manage'] },
      workspace: { connected: false },
      onConnectWorkspace: async () => {},
      onCloneWorkspace: async () => {},
      onConnectGithubOAuth: async () => {},
    })

    expect(html).toContain('aria-label="Local repository path"')
    expect(html).toContain('/absolute/path/to/local/repo')
    expect(html).toContain('Connect')
    expect(html).toContain('Connect GitHub')
    expect(html).toContain('Use Connect below; restart only if you edit env')
    expect(html).toContain('aria-label="GitHub repository URL"')
    expect(html).toContain('VoiceOps will bind it directly without cloning')
    expect(html).toContain('Sign in with GitHub')
    expect(html).toContain('GITHUB_TOKEN')
  })

  it('shows GitHub OAuth status for private repo access', () => {
    const html = renderRightRail({
      user: { permissions: ['admin:manage'] },
      workspace: { connected: true, remote_kind: 'github', remote_url: 'https://github.com/team/app.git' },
      githubOAuth: { connected: true, login: 'octo', source: 'oauth' },
      onConnectGithubOAuth: async () => {},
    })

    expect(html).toContain('GitHub signed in')
    expect(html).toContain('octo')
    expect(html).toContain('Reconnect GitHub')
  })

  it('shows project access controls only to admins', () => {
    const projectUsers = [
      {
        id: 'user-priya',
        name: 'Priya Nair',
        email: 'priya@voiceops.dev',
        role: 'on_call',
        role_label: 'On-call engineer',
        projects: ['alpha', 'mobile'],
      },
      {
        id: 'user-admin',
        name: 'Sam Ortiz',
        email: 'admin@voiceops.dev',
        role: 'admin',
        role_label: 'Admin',
        projects: [],
      },
    ]

    const adminHtml = renderRightRail({
      user: { permissions: ['admin:manage'] },
      projectUsers,
    })
    const userHtml = renderRightRail({
      user: { permissions: ['agent:run'] },
      projectUsers,
    })

    expect(projectAccessMeta(projectUsers)).toBe('1/2 scoped')
    expect(adminHtml).toContain('Project access')
    expect(adminHtml).toContain('1/2 scoped')
    expect(adminHtml).toContain('aria-label="Projects for Priya Nair"')
    expect(adminHtml).toContain('value="alpha, mobile"')
    expect(adminHtml).toContain('Admin · all projects')
    expect(userHtml).not.toContain('Project access')
    expect(userHtml).not.toContain('Projects for Priya Nair')
  })

  it('keeps viewer-only users on read-only controls', () => {
    const html = renderRightRail({
      user: { permissions: ['dashboard:view'] },
      workspace: { connected: false },
      onConnectWorkspace: async () => {},
      onCloneWorkspace: async () => {},
      onApproveAction: async () => {},
      onRejectAction: async () => {},
      onCommitAction: async () => {},
      onCreatePullRequest: async () => {},
      onCreateAgentAssignment: async () => {},
      onDispatchAgentAssignment: async () => {},
      onCancelAgentAssignment: async () => {},
      onRetryAgentAssignment: async () => {},
      onClearCompletedAgentAssignments: async () => {},
      onConnectLLMProvider: async () => {},
      onPreflightLLMProvider: async () => {},
      onDisconnectLLMProvider: async () => {},
      onUpdateAgentLLMRoute: async () => {},
      onPreflightAgentLLMRoute: async () => {},
      onStartExternalAgentOAuth: async () => {},
      onConnectExternalAgentLocalCli: async () => {},
      onRunExternalAgent: async () => {},
      onRecommendExternalAgent: async () => {},
      workDashboard: {
        metrics: [],
        agents: [{ id: 'voiceops', name: 'VoiceOps' }],
        approvals: [],
        open_items: [],
        recent_actions: [],
        recent_decisions: [],
      },
      agentAssignments: [
        {
          id: 'assign-1',
          task: 'Review pending patch',
          agent_label: 'VoiceOps',
          agent_kind: 'internal',
          status: 'queued',
          mode: 'review',
          created_at: new Date().toISOString(),
        },
      ],
      actions: [
        {
          id: 'act-pending-viewer',
          action: 'patch',
          status: 'pending_approval',
          pending_approval: true,
          requested_by_name: 'Alice Chen',
          summary: 'Proposed health route fix.',
          files_changed: ['app.py'],
          approval: {
            diff: '--- a/app.py\n+++ b/app.py',
            test_command: 'python -m pytest -q',
          },
        },
        {
          id: 'act-completed-viewer',
          action: 'patch',
          status: 'completed',
          requested_by_name: 'Alice Chen',
          summary: 'Applied health route fix.',
          files_changed: ['app.py'],
          approval: {
            git: { branch_name: 'voiceops/act-completed-viewer-health' },
          },
        },
      ],
      externalAgentProviders: [externalProvider({ auth_methods: ['oauth', 'local_cli'] })],
      llmProviders: [{ provider: 'anthropic', label: 'Anthropic Claude', connected: true, model: 'claude-sonnet-4-5' }],
      agentLLMRouting: {
        room_id: 'main',
        routes: [{ role: 'code', provider: 'anthropic', model: 'claude-sonnet-4-5' }],
        runtime: [],
      },
    })

    expect(html).toContain('LLM credentials are read-only for this role.')
    expect(html).toContain('Agent routing is read-only for this role.')
    expect(html).toContain('Coding agent setup and runs are read-only for this role.')
    expect(html).not.toContain('aria-label="Local repository path"')
    expect(html).not.toContain('aria-label="Assignment task"')
    expect(html).not.toContain('class="approval-btn approve"')
    expect(html).not.toContain('class="approval-btn reject"')
    expect(html).not.toContain('aria-label="Commit approved patch"')
    expect(html).not.toContain('aria-label="Prepare pull request plan"')
    expect(html).not.toContain('<span>API key</span>')
    expect(html).not.toContain('>Connect</button>')
    expect(html).not.toContain('>Local CLI</button>')
    expect(html).not.toContain('>Try</button>')
  })

  it('hides workspace code inspection controls from viewer-only users', () => {
    const html = renderRightRail({
      user: { permissions: ['dashboard:view'] },
      workspace: { connected: true, name: 'repo', branch: 'main', remote_url: 'https://github.com/team/repo.git' },
    })

    expect(html).toContain('Code context restricted')
    expect(html).toContain('Code search and diff preview require voice workspace permission.')
    expect(html).not.toContain('aria-label="Ask workspace code"')
    expect(html).not.toContain('aria-label="Submit workspace code question"')
  })

  it('shows action, assignment, and credential controls to permitted operators', () => {
    const html = renderRightRail({
      user: { permissions: ['agent:approve', 'agent:run', 'credentials:manage_own'] },
      onApproveAction: async () => {},
      onRejectAction: async () => {},
      onCommitAction: async () => {},
      onCreatePullRequest: async () => {},
      onCreateAgentAssignment: async () => {},
      onDispatchAgentAssignment: async () => {},
      onConnectLLMProvider: async () => {},
      onPreflightLLMProvider: async () => {},
      onStartExternalAgentOAuth: async () => {},
      onConnectExternalAgentLocalCli: async () => {},
      onRunExternalAgent: async () => {},
      workDashboard: {
        metrics: [],
        agents: [{ id: 'voiceops', name: 'VoiceOps' }],
        approvals: [],
        open_items: [],
        recent_actions: [],
        recent_decisions: [],
      },
      actions: [
        {
          id: 'act-pending-operator',
          action: 'patch',
          status: 'pending_approval',
          pending_approval: true,
          requested_by_name: 'Alice Chen',
          summary: 'Proposed health route fix.',
          files_changed: ['app.py'],
          approval: {
            diff: '--- a/app.py\n+++ b/app.py',
            test_command: 'python -m pytest -q',
          },
        },
      ],
      externalAgentProviders: [externalProvider({ auth_methods: ['oauth', 'local_cli'] })],
    })

    expect(html).toContain('Approve</button>')
    expect(html).toContain('Reject</button>')
    expect(html).toContain('aria-label="Assignment task"')
    expect(html).toContain('<span>API key</span>')
    expect(html).toContain('>Connect</button>')
    expect(html).toContain('>Local CLI</button>')
    expect(html).toContain('>Try</button>')
  })

  it('surfaces GitHub URL workspace setup as direct connect', () => {
    const workspace = {
      connected: false,
      configured_workspace: 'https://github.com/team/app.git',
    }
    const html = renderRightRail({ workspace })

    expect(repositoryFlowCopy(workspace)).toEqual({
      tone: 'blocked',
      title: 'Repo not connected',
      meta: 'repo setup required',
      detail: 'Set VOICEOPS_WORKSPACE to a GitHub repository URL or a cloned local folder before code actions can propose patches.',
    })
    expect(commandFallbackCopy(workspace)).toEqual({
      title: 'Typed command fallback',
      detail: 'Set VOICEOPS_WORKSPACE in backend/.env to connect local code actions.',
    })
    expect(html).toContain('repo setup required')
    expect(html).toContain('Set VOICEOPS_WORKSPACE to a GitHub repository URL or a cloned local folder before code actions can propose patches.')
    expect(repoSetupCommands(workspace)).toEqual({
      env: 'VOICEOPS_WORKSPACE=https://github.com/team/app.git',
      clone: '# local fallback: git clone https://github.com/team/app.git /absolute/path/to/app',
    })
    expect(html).toContain('VOICEOPS_WORKSPACE=https://github.com/team/app.git')
    expect(html).toContain('# local fallback: git clone https://github.com/team/app.git /absolute/path/to/app')
  })

  it('uses the configured SSH remote when explaining repository setup', () => {
    const workspace = {
      connected: false,
      configured_workspace: 'git@github.com:team/mobile-app.git',
    }

    expect(repoSetupCommands(workspace)).toEqual({
      env: 'VOICEOPS_WORKSPACE=git@github.com:team/mobile-app.git',
      clone: '# local fallback: git clone git@github.com:team/mobile-app.git /absolute/path/to/mobile-app',
    })
  })

  it('hides repository setup once a workspace is connected', () => {
    const html = renderRightRail({
      workspace: { connected: true, name: 'voiceops', remote_url: 'https://github.com/team/voiceops.git' },
    })

    expect(html).not.toContain('Repository setup')
    expect(html).not.toContain('VOICEOPS_WORKSPACE=https://github.com/org/repo.git')
    expect(html).toContain('GitHub repo connected')
    expect(html).toContain('VOICEOPS_WORKSPACE points to GitHub')
    expect(html).not.toContain('Change local repo')
    expect(html).not.toContain('New local repository path')
  })

  it('lets admins switch a connected local workspace from repository status', () => {
    const html = renderRightRail({
      user: { permissions: ['admin:manage'] },
      workspace: {
        connected: true,
        name: 'voiceops',
        branch: 'main',
        remote_url: 'https://github.com/team/voiceops.git',
        persistence_note: 'Configured by VOICEOPS_WORKSPACE; UI switches affect the current backend process only unless env changes.',
      },
      onConnectWorkspace: async () => {},
      onCloneWorkspace: async () => {},
    })

    expect(html).toContain('Change local repo')
    expect(html).toContain('aria-label="New local repository path"')
    expect(html).toContain('/absolute/path/to/local/repo')
    expect(html).toContain('Switch')
    expect(html).toContain('Connect GitHub')
    expect(html).toContain('UI switches affect the current backend process only unless env changes.')
  })

  it('does not flash repository setup while workspace state is still loading', () => {
    const html = renderRightRail({
      workspace: undefined,
    })

    expect(html).not.toContain('Repository setup')
    expect(html).not.toContain('VOICEOPS_WORKSPACE=/absolute/path/to/repo')
  })

  it('shows backend queue health as syncing until assignment rows load', () => {
    const html = renderRightRail({
      workDashboard: {
        metrics: [],
        agents: [],
        approvals: [],
        open_items: [],
        recent_actions: [],
        recent_decisions: [],
        queue_health: [
          {
            label: 'Open',
            value: 8,
            tone: 'info',
            detail: '8 assignments are still open.',
          },
          {
            label: 'Stale',
            value: 3,
            tone: 'warning',
            detail: '3 open assignments are older than 30 minutes.',
          },
          {
            label: 'Blocked',
            value: 0,
            tone: 'neutral',
            detail: '0 assignments have readiness blockers.',
          },
          {
            label: 'Approvals',
            value: 0,
            tone: 'neutral',
            detail: '0 patch approvals are waiting.',
          },
        ],
        readiness: {
          ready: false,
          state: 'needs_attention',
          summary: 'Needs attention: 1 blocker, 1 warning.',
          blockers: ['1 patch approval is waiting.'],
          warnings: ['8 assignments are still open.'],
        },
      },
      agentAssignments: [],
      externalAgentProviders: [],
    })

    expect(html).toContain('8 indexed')
    expect(html).toContain('Queue index has 8 open assignments. Rows have not synced yet.')
    expect(html).not.toContain('title="8 assignments are still open."')
    expect(html).not.toContain('>8</b><span>Open</span>')
    expect(html).not.toContain('title="3 open assignments are older than 30 minutes."')
    expect(html).not.toContain('>3</b><span>Stale</span>')
    expect(html).toContain('1 patch approval is waiting.')
    expect(html).not.toContain('<b>Needs attention</b>')
    expect(html).not.toContain('title="0 assignments still open."')
  })

  it('keeps external agent model selectors folded behind run settings', () => {
    const html = renderRightRail({
      externalAgentProviders: [
        {
          provider: 'claude',
          label: 'Claude Code',
          connected: true,
          auth_method: 'local_cli',
          token_preview: '',
          supported_models: [
            'claude-fable-5',
            'claude-sonnet-4-6',
            'claude-opus-4-8',
            'claude-haiku-4-5',
            'claude-opus-4-7',
            'claude-sonnet',
          ],
          default_model: 'claude-sonnet-4-6',
          local_cli_command: 'claude',
          local_cli_command_template: 'claude --workspace {workspace} --prompt {prompt} --model {model}',
          mode_readiness: {
            review: { ready: true, detail: 'ready' },
            patch: { ready: true, detail: 'ready' },
            test: { ready: true, detail: 'ready' },
            explain: { ready: true, detail: 'ready' },
          },
        },
        {
          provider: 'codex',
          label: 'OpenAI Codex',
          connected: true,
          auth_method: 'api_key',
          token_preview: 'sk-...',
          supported_models: [
            'gpt-5.5',
            'gpt-5.4',
            'gpt-5.4-mini',
            'gpt-5.4-nano',
            'gpt-5',
          ],
          default_model: 'gpt-5.4',
          mode_readiness: {
            review: { ready: true, detail: 'ready' },
            patch: { ready: true, detail: 'ready' },
            test: { ready: true, detail: 'ready' },
            explain: { ready: true, detail: 'ready' },
          },
        },
      ],
    })

    expect(html).toContain('Claude Code')
    expect(html).toContain('<details class="external-agent-run-settings"><summary><span>Run settings</span><small>claude-sonnet-4-6</small></summary>')
    expect(html).toContain('aria-label="Claude Code model"')
    expect(html).toContain('claude')
    expect(html).toContain('claude-fable-5')
    expect(html).toContain('claude-sonnet-4-6')
    expect(html).toContain('claude-opus-4-8')
    expect(html).toContain('claude-haiku-4-5')
    expect(html).toContain('claude-opus-4-7')
    expect(html).toContain('OpenAI Codex')
    expect(html).toContain('<details class="external-agent-run-settings"><summary><span>Run settings</span><small>gpt-5.4</small></summary>')
    expect(html).toContain('aria-label="OpenAI Codex model"')
    expect(html).toContain('Recommend</button>')
    expect(html).toContain('gpt-5.5')
    expect(html).toContain('gpt-5.4')
    expect(html).toContain('gpt-5.4-mini')
    expect(html).toContain('gpt-5.4-nano')
    expect(html).toContain('custom model ID')
    expect(html).not.toContain('external-agent-run-settings" open')
  })

  it('renders external agent readiness warnings without marking them ready', () => {
    expect(externalAgentCapabilityBoundary({ connected: true, auth_method: 'api_key' })).toEqual({
      label: 'read-only credential',
      detail: 'OAuth/API can explain, review, or plan tests; patch work still needs local CLI.',
    })
    expect(externalAgentCapabilityBoundary({ connected: true, auth_method: 'local_cli' })).toEqual({
      label: 'native agent',
      detail: 'Patch work runs the local CLI in an isolated workspace and still needs approval.',
    })

    const html = renderRightRail({
      externalAgentProviders: [
        {
          provider: 'codex',
          label: 'OpenAI Codex',
          connected: true,
          auth_method: 'api_key',
          token_preview: 'sk-...',
          supported_models: ['gpt-5.4'],
          default_model: 'gpt-5.4',
          mode_readiness: {
            review: {
              ready: true,
              reason: 'mock_adapter',
              detail: 'API execution is disabled; read-only runs use the deterministic adapter.',
              severity: 'warning',
              execution_mode: 'mock_adapter',
            },
            patch: {
              ready: false,
              reason: 'local_cli_required',
              detail: 'Patch mode requires a local CLI credential.',
              severity: 'warning',
              execution_mode: 'local_cli_patch',
              preview_first: true,
            },
            test: { ready: true, reason: 'mock_adapter', detail: 'mock', severity: 'warning' },
            explain: { ready: true, reason: 'mock_adapter', detail: 'mock', severity: 'warning' },
          },
        },
      ],
    })

    expect(html).toContain('class="external-agent-readiness warning"')
    expect(html).toContain('read-only credential')
    expect(html).toContain('OAuth/API can explain, review, or plan tests; patch work still needs local CLI.')
    expect(html).toContain('review: mock adapter · mock adapter')
    expect(html).toContain('title="API execution is disabled; read-only runs use the deterministic adapter."')
    expect(html).not.toContain('class="external-agent-readiness ready"')
  })

  it('keeps demo patch adapter blockers out of the provider row headline', () => {
    const html = renderRightRail({
      externalAgentProviders: [
        {
          provider: 'claude',
          label: 'Claude Code',
          connected: true,
          auth_method: 'local_cli',
          supported_models: ['claude-sonnet-4-6'],
          default_model: 'claude-sonnet-4-6',
          mode_readiness: {
            review: { ready: true, detail: 'ready' },
            patch: {
              ready: false,
              reason: 'demo_adapter_requires_app_py',
              detail: 'This local demo patch adapter requires app.py in the configured workspace.',
              severity: 'error',
            },
            test: { ready: true, detail: 'ready' },
            explain: { ready: true, detail: 'ready' },
          },
        },
      ],
    })

    expect(html).toContain('title="Provider is connected. Use patch mode only when local CLI is ready."')
    expect(html).toContain('This local demo patch adapter requires app.py in the configured workspace.')
    expect(html.indexOf('title="Provider is connected. Use patch mode only when local CLI is ready."'))
      .toBeLessThan(html.indexOf('Setup details'))
  })

  it('summarizes coding agent setup before detailed controls', () => {
    const html = renderRightRail({
      externalAgentProviders: [
        externalProvider({
          provider: 'claude',
          label: 'Claude Code',
          connected: true,
          auth_method: 'local_cli',
          supported_models: ['claude-sonnet-4-6'],
          mode_readiness: {
            review: { ready: true, detail: 'ready' },
            patch: { ready: true, detail: 'ready' },
            test: { ready: true, detail: 'ready' },
            explain: { ready: true, detail: 'ready' },
          },
        }),
      ],
    })

    expect(html).toContain('aria-label="Coding agent setup summary"')
    expect(html).toContain('Patch workflow ready')
    expect(html).toContain('1 patch ready · 1/1 connected')
    expect(html).toContain('native agent')
    expect(html).toContain('Patch work runs the local CLI in an isolated workspace and still needs approval.')
    expect(html.indexOf('aria-label="Coding agent setup summary"')).toBeLessThan(html.indexOf('class="external-agent-row connected'))
  })

  it('renders external agent runtime preflight evidence and blockers', () => {
    const html = renderRightRail({
      externalAgentProviders: [
        {
          provider: 'codex',
          label: 'OpenAI Codex',
          connected: true,
          auth_method: 'local_cli',
          supported_models: ['gpt-5.4'],
          default_model: 'gpt-5.4',
          mode_readiness: {
            review: { ready: false, reason: 'cli_missing', detail: 'Local CLI command is not installed.', severity: 'error' },
            patch: { ready: false, reason: 'cli_missing', detail: 'Local CLI command is not installed.', severity: 'error' },
            test: { ready: false, reason: 'cli_missing', detail: 'Local CLI command is not installed.', severity: 'error' },
            explain: { ready: false, reason: 'cli_missing', detail: 'Local CLI command is not installed.', severity: 'error' },
          },
          preflight: {
            state: 'blocked',
            summary: 'Blocked; 1 setup item required.',
            blockers: ['Local CLI command is not installed.'],
            warnings: [],
            evidence: {
              env_policy: 'minimal_external_agent_env',
              local_cli_runtime: 'isolated_temp_workspace',
              tokens_returned_to_browser: false,
              preview_first_policy: 'required_for_patch',
            },
          },
        },
      ],
    })

    expect(html).toContain('Runtime preflight')
    expect(html).toContain('>blocked</b>')
    expect(html).toContain('Blocked; 1 setup item required. Local CLI command is not installed.')
    expect(html).toContain('minimal_external_agent_env')
    expect(html).toContain('isolated_temp_workspace')
    expect(html).toContain('server-side tokens')
    expect(html).toContain('required for patch')
  })

  it('renders external agent setup guidance inline', () => {
    const html = renderRightRail({
      externalAgentProviders: [
        {
          provider: 'claude',
          label: 'Claude Code',
          connected: false,
          supported_models: ['claude-sonnet-4-6'],
          default_model: 'claude-sonnet-4-6',
          mode_readiness: {
            review: { ready: false, detail: 'Connect this provider before assigning work.' },
            patch: { ready: false, detail: 'Connect this provider before assigning work.' },
            test: { ready: false, detail: 'Connect this provider before assigning work.' },
            explain: { ready: false, detail: 'Connect this provider before assigning work.' },
          },
          setup_guide: {
            state: 'not_connected',
            recommended_path: 'local_cli_for_code_changes',
            next_step: 'Connect local CLI for code-changing work, or API/OAuth for read-only modes.',
            steps: [
              { id: 'connect_local_cli', label: 'Connect local CLI', recommended: true },
              { id: 'connect_oauth', label: 'Connect OAuth', recommended: true },
              { id: 'connect_api_key', label: 'Connect API key', recommended: false },
            ],
          },
        },
      ],
    })

    expect(html).toContain('Next setup')
    expect(html).toContain('class="external-agent-primary-setup blocked"')
    expect(html).toContain('<details class="external-agent-details blocked">')
    expect(html).not.toContain('<details class="external-agent-details blocked" open="">')
    expect(html).toContain('local cli for code changes')
    expect(html).toContain('Connect local CLI for code-changing work, or API/OAuth for read-only modes.')
    expect(html).toContain('Connect local CLI')
    expect(html).toContain('Connect OAuth')
    expect(html).not.toContain('Connect API key</span>')
  })

  it('renders local open agent as CLI-only provider', () => {
    const html = renderRightRail({
      externalAgentProviders: [
        {
          provider: 'local',
          label: 'Local Open Agent',
          connected: false,
          auth_methods: ['local_cli'],
          supported_models: ['local-default', 'glm-5.2-local', 'qwen-coder-local'],
          default_model: 'local-default',
          mode_readiness: {
            review: { ready: false, detail: 'Connect this provider before assigning work.' },
            patch: { ready: false, detail: 'Connect this provider before assigning work.' },
            test: { ready: false, detail: 'Connect this provider before assigning work.' },
            explain: { ready: false, detail: 'Connect this provider before assigning work.' },
          },
          setup_guide: {
            state: 'not_connected',
            recommended_path: 'local_cli_for_code_changes',
            next_step: 'Connect a local CLI command for this open-source or self-hosted coding agent.',
            steps: [
              { id: 'connect_local_cli', label: 'Connect local CLI', recommended: true },
            ],
          },
        },
      ],
    })

    expect(html).toContain('Local Open Agent')
    expect(html).toContain('glm-5.2-local')
    expect(html).toContain('qwen-coder-local')
    expect(html).toContain('Local CLI')
    expect(html).not.toContain('Connect</button><button type="button" aria-expanded="false">Local CLI')
    expect(html).toContain('Connect a local CLI command for this open-source or self-hosted coding agent.')
  })

  it('renders external assignment model control for selected coding agent', () => {
    const html = renderRightRail({
      workDashboard: {
        metrics: [],
        agents: [],
        approvals: [],
        open_items: [],
        recent_actions: [],
        recent_decisions: [],
      },
      externalAgentProviders: [
        {
          provider: 'codex',
          label: 'OpenAI Codex',
          connected: true,
          auth_method: 'local_cli',
          supported_models: ['gpt-5.5', 'gpt-5.4', 'gpt-5-mini'],
          default_model: 'gpt-5.4',
          mode_readiness: {
            review: { ready: true, detail: 'ready' },
            patch: { ready: true, detail: 'ready' },
            test: { ready: true, detail: 'ready' },
            explain: { ready: true, detail: 'ready' },
            memory: { ready: false, detail: 'memory uses internal agent' },
          },
        },
      ],
    })

    expect(html).toContain('Assign work')
    expect(html).toContain('<details class="workdash-assignment-details">')
    expect(html).toContain('Auto external agent · recommended route')
    expect(html).toContain('aria-label="Assignment model"')
    expect(html).toContain('gpt-5.5')
    expect(html).toContain('gpt-5.4')
    expect(html).toContain('gpt-5-mini')
  })

  it('hides assignment controls when no assignable agent exists', () => {
    const html = renderToStaticMarkup(
      <WorkDashboardPanel
        {...workDashboardHandlers}
        dashboard={{
          metrics: [],
          agents: [],
          approvals: [],
          open_items: [],
          recent_actions: [],
          recent_decisions: [],
        }}
        externalAgentProviders={[]}
        agentAssignments={[]}
      />,
    )

    expect(html).toContain('Agents')
    expect(html).toContain('Off')
    expect(html).toContain('connect coding agent')
    expect(html).toContain('class="workdash-next ready"')
    expect(html).toContain('class="workdash-status-strip secondary ready"')
    expect(html).not.toContain('aria-label="Assignment agent"')
    expect(html).not.toContain('aria-label="Assignment task"')
    expect(html).not.toContain('No agent available')
  })

  it('keeps work dashboard next step focused on the highest-priority action', () => {
    expect(workDashboardNextStep({ approvalsCount: 1, activeAssignmentsCount: 2 })).toEqual({
      title: 'Review pending patch',
      lines: ['Approve or reject the diff before starting more code-changing work.'],
    })
    expect(workDashboardNextStep({ loadedQueueIsPartial: true, availableAgentCount: 1 })).toEqual({
      title: 'Queue preview ready',
      lines: ['Open queue details to run or cancel waiting work.'],
    })
    expect(workDashboardNextStep({ activeAssignmentsCount: 2, availableAgentCount: 1 })).toEqual({
      title: 'Run queued work',
      lines: ['Run ready assignments, or cancel stale ones before assigning more work.'],
    })
    expect(workDashboardNextStep({ availableAgentCount: 2 })).toEqual({
      title: 'Start with a command',
      lines: ['Use text or live meeting first. Assign work only when a coding agent needs a task.'],
    })
  })

  it('uses factual review status copy when there is no pending patch approval', () => {
    const html = renderToStaticMarkup(
      <WorkDashboardPanel
        {...workDashboardHandlers}
        dashboard={{
          metrics: [],
          agents: [{ id: 'coordinator', name: 'Coordinator', kind: 'internal', status: 'online' }],
          approvals: [],
          open_items: [],
          recent_actions: [],
          recent_decisions: [],
        }}
        agentAssignments={[]}
      />,
    )

    expect(html).toContain('Review No patch, approval clear')
    expect(html).toContain('<span>Review</span><b>No patch</b><small>approval clear</small>')
    expect(html).toContain('Start with a command')
    expect(html).toContain('Use text or live meeting first. Assign work only when a coding agent needs a task.')
    expect(html).not.toContain('Review Clear')
    expect(html).not.toContain('no pending patch')
  })

  it('uses readable agent status copy instead of bare provider ratios', () => {
    const blockedHtml = renderToStaticMarkup(
      <WorkDashboardPanel
        {...workDashboardHandlers}
        dashboard={{
          metrics: [],
          agents: [],
          approvals: [],
          open_items: [],
          recent_actions: [],
          recent_decisions: [],
        }}
        agentAssignments={[]}
        externalAgentProviders={[
          externalProvider({
            provider: 'claude',
            connected: true,
            mode_readiness: { patch: { ready: false, detail: 'CLI setup required.' } },
          }),
          externalProvider({
            provider: 'codex',
            connected: true,
            mode_readiness: { patch: { ready: false, detail: 'Token missing.' } },
          }),
          externalProvider({ provider: 'cursor', connected: false }),
          externalProvider({ provider: 'local-open', connected: false }),
        ]}
      />,
    )
    expect(blockedHtml).toContain('<span>Agents</span><b>Patch setup</b><small>patch setup needed</small>')
    expect(blockedHtml).toContain('Agents Patch setup, patch setup needed')
    expect(blockedHtml).toContain('workdash-status-cell attention')
    expect(blockedHtml).not.toContain('<b>2/4</b>')

    const readyHtml = renderToStaticMarkup(
      <WorkDashboardPanel
        {...workDashboardHandlers}
        dashboard={{
          metrics: [],
          agents: [],
          approvals: [],
          open_items: [],
          recent_actions: [],
          recent_decisions: [],
        }}
        agentAssignments={[]}
        externalAgentProviders={[
          externalProvider({
            provider: 'claude',
            connected: true,
            mode_readiness: { patch: { ready: true, detail: 'ready' } },
          }),
          externalProvider({
            provider: 'codex',
            connected: true,
            mode_readiness: { patch: { ready: false, detail: 'Token missing.' } },
          }),
          externalProvider({ provider: 'cursor', connected: false }),
        ]}
      />,
    )
    expect(readyHtml).toContain('<span>Agents</span><b>1 patch ready</b><small>2/3 connected</small>')
    expect(readyHtml).toContain('Agents 1 patch ready, 2/3 connected')
  })

  it('labels assign work with loaded rows and provider capability state', () => {
    expect(partialQueueSummary(20, 78)).toBe('20 shown · 58 more')
    expect(partialQueueSummary(20, 20)).toBe('20 shown')

    expect(workDashboardDelegateMeta({
      queueRowsSyncing: true,
      activeAssignmentsCount: 0,
      agentAssignmentsCount: 0,
      availableAgentCount: 1,
    })).toBe('queue syncing · 1 agent')
    expect(workDashboardDelegateMeta({
      loadedQueueIsPartial: true,
      activeAssignmentsCount: 20,
      agentAssignmentsCount: 20,
      availableAgentCount: 4,
    })).toBe('20 shown · 4 agents')
    expect(workDashboardDelegateMeta({
      activeAssignmentsCount: 3,
      agentAssignmentsCount: 3,
      availableAgentCount: 2,
    })).toBe('3 queued · 2 agents')
    expect(workDashboardDelegateMeta({
      activeAssignmentsCount: 0,
      agentAssignmentsCount: 2,
      availableAgentCount: 2,
    })).toBe('2 loaded · 2 agents')
    expect(workDashboardDelegateMeta({
      loadedQueueIsPartial: true,
      activeAssignmentsCount: 20,
      agentAssignmentsCount: 20,
      availableAgentCount: 4,
      connectedProviderCount: 2,
      totalProviderCount: 4,
      patchReadyProviderCount: 0,
    })).toBe('2/4 connected · 2 need setup')
    expect(workDashboardDelegateMeta({
      connectedProviderCount: 1,
      totalProviderCount: 4,
      patchReadyProviderCount: 0,
    })).toBe('1/4 connected · 1 needs setup')
    expect(workDashboardDelegateMeta({
      activeAssignmentsCount: 3,
      agentAssignmentsCount: 3,
      availableAgentCount: 4,
      connectedProviderCount: 3,
      totalProviderCount: 4,
      patchReadyProviderCount: 2,
    })).toBe('3/4 connected · 2 patch ready')

    expect(workDashboardDelegateSummaryMeta({
      loadedQueueIsPartial: true,
      activeAssignmentsCount: 20,
      agentAssignmentsCount: 20,
      availableAgentCount: 1,
    })).toBe('1 agent ready')
    expect(workDashboardDelegateSummaryMeta({
      connectedProviderCount: 2,
      totalProviderCount: 4,
      patchReadyProviderCount: 0,
    })).toBe('choose agent')
    expect(workDashboardDelegateSummaryMeta({
      connectedProviderCount: 3,
      totalProviderCount: 4,
      patchReadyProviderCount: 2,
    })).toBe('2 patch ready')
  })

  it('shows the loaded work dashboard next step before queue details', () => {
    const html = renderToStaticMarkup(
      <WorkDashboardPanel
        {...workDashboardHandlers}
        dashboard={{
          metrics: [],
          agents: [{ id: 'coordinator', name: 'Coordinator', kind: 'internal', status: 'online' }],
          approvals: [
            {
              id: 'act-1',
              title: 'Patch app.py',
              status: 'pending_approval',
              actor_name: 'Alice',
              files: ['app.py'],
            },
          ],
          open_items: [],
          recent_actions: [],
          recent_decisions: [],
        }}
        agentAssignments={[
          {
            id: 'asg-1',
            agent_id: 'coordinator',
            agent_label: 'Coordinator',
            agent_kind: 'internal',
            task: 'Review queued patch',
            mode: 'review',
            status: 'queued',
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
          },
        ]}
      />,
    )

    expect(html).toContain('aria-label="Work dashboard status:')
    expect(html).toContain('Review 1 pending, approval required')
    expect(html).toContain('class="workdash-next attention"')
    expect(html).toContain('class="workdash-status-strip secondary ready"')
    expect(html).not.toContain('class="workdash-brief')
    expect(html).toContain('Review pending patch')
    expect(html).toContain('Approve or reject the diff before starting more code-changing work.')
    expect(html.indexOf('Review pending patch')).toBeLessThan(html.indexOf('Needs review'))
    expect(html.indexOf('Review pending patch')).toBeLessThan(html.indexOf('Assignments</span>'))
  })

  it('keeps queue health focused on non-zero attention items', () => {
    const html = renderToStaticMarkup(
      <WorkDashboardPanel
        {...workDashboardHandlers}
        dashboard={{
          metrics: [],
          agents: [{ id: 'coordinator', name: 'Coordinator', kind: 'internal', status: 'online' }],
          approvals: [],
          open_items: [],
          recent_actions: [],
          recent_decisions: [],
          queue_health: [
            { label: 'Open', value: 1, tone: 'info', detail: '1 open assignment.' },
            { label: 'Stale', value: 3, tone: 'warning', detail: '3 stale assignments.' },
            { label: 'Blocked', value: 0, tone: 'neutral', detail: 'No blocked assignments.' },
            { label: 'Approvals', value: 0, tone: 'neutral', detail: 'No approvals waiting.' },
          ],
        }}
        agentAssignments={[
          {
            id: 'asg-1',
            agent_id: 'coordinator',
            agent_label: 'Coordinator',
            agent_kind: 'internal',
            task: 'Review queue state',
            mode: 'review',
            status: 'queued',
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
          },
        ]}
      />,
    )

    expect(html).toContain('1 open')
    expect(html).toContain('3</b><span>Stale</span>')
    expect(html).not.toContain('1</b><span>Open</span>')
    expect(html).not.toContain('0</b><span>Blocked</span>')
    expect(html).not.toContain('0</b><span>Approvals</span>')
  })

  it('keeps partial queue metadata from repeating the main work queue label', () => {
    const html = renderToStaticMarkup(
      <WorkDashboardPanel
        {...workDashboardHandlers}
        dashboard={{
          metrics: [],
          agents: [{ id: 'coordinator', name: 'Coordinator', kind: 'internal', status: 'online' }],
          approvals: [],
          open_items: [],
          recent_actions: [],
          recent_decisions: [],
          readiness: {
            ready: false,
            state: 'degraded',
            summary: '78 assignments are stale.',
            blockers: ['78 assignments are stale.'],
            warnings: [],
          },
          queue_health: [
            { label: 'Open', value: 78, tone: 'info', detail: '78 open assignments.' },
            { label: 'Stale', value: 78, tone: 'warning', detail: '78 assignments are stale.' },
          ],
        }}
        agentAssignments={Array.from({ length: 20 }, (_, index) => ({
          id: `asg-${index}`,
          agent_id: 'coordinator',
          agent_label: 'Coordinator',
          agent_kind: 'internal',
          task: `Review item ${index}`,
          mode: 'review',
          status: 'queued',
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        }))}
      />,
    )

    expect(html).toContain('20 shown · 58 more')
    expect(html).toContain('<span>Queue</span><b>20 shown</b><small>58 more</small>')
    expect(html).toContain('<small>58 more</small>')
    expect(html).not.toContain('<small>20 shown</small>')
    expect(html).not.toContain('<b>20 visible</b>')
    expect(html).not.toContain('20 visible · 58 waiting')
    expect(html).not.toContain('<small>58 waiting</small>')
    expect(html).not.toContain('20 loaded · 58 more queued')
    expect(html).not.toContain('58 more queued')
    expect(html).not.toContain('<small>more work not shown</small>')
    expect(html).toContain('Queue preview ready')
    expect(html).toContain('Open queue details to run or cancel waiting work.')
    expect(html).toContain('Assign work')
    expect(html).toContain('title="20 shown · 1 agent"')
    expect(html).toContain('<small>1 agent ready</small>')
    expect(html).not.toContain('20 queued · 1 agent')
    expect(html).not.toContain('Loading queue')
    expect(html).not.toContain('More work is loading')
    expect(html).not.toContain('more open work in queue index')
    expect(html).not.toContain('<small>78 assignments are stale.</small>')
    expect(html).toContain('Assignments</span>')
    expect(html).not.toContain('title="78 assignments are stale. Loading queue: More work is loading. Typed commands still work."')
    expect(html).not.toContain('Rows loading')
    expect(html).not.toContain('<b>Needs attention</b>')
    expect(html.match(/20 shown · 58 more/g)).toHaveLength(1)
  })

  it('hides redundant single queue health when readiness already explains it', () => {
    expect(shouldShowQueueHealth([
      { label: 'Stale', value: 78, tone: 'warning', detail: '78 assignments are stale.' },
    ], '78 assignments are stale.')).toBe(false)
    expect(shouldShowQueueHealth([
      { label: 'Blocked', value: 2, tone: 'danger', detail: '2 providers need setup.' },
    ], '78 assignments are stale.')).toBe(true)
    expect(shouldShowQueueHealth([
      { label: 'Stale', value: 78, tone: 'warning', detail: '78 assignments are stale.' },
      { label: 'Blocked', value: 2, tone: 'danger', detail: '2 providers need setup.' },
    ], '78 assignments are stale.')).toBe(true)
    expect(shouldShowQueueHealth([], 'ready')).toBe(false)
  })

  it('keeps the work dashboard action-first and hides routine context when no agent is running', () => {
    const html = renderToStaticMarkup(
      <WorkDashboardPanel
        {...workDashboardHandlers}
        dashboard={{
          metrics: [{ label: 'Closed', value: 4, tone: 'success' }],
          agents: [{ id: 'coordinator', name: 'Coordinator', kind: 'internal', status: 'online' }],
          approvals: [
            {
              id: 'act-1',
              title: 'Patch app.py',
              status: 'pending_approval',
              actor_name: 'Alice',
              files: ['app.py'],
            },
          ],
          open_items: [
            { id: 'mem-1', title: 'Fix health route', status: 'open', actor_name: 'Alice' },
          ],
          recent_actions: [
            { id: 'act-2', title: 'Ran tests', status: 'completed', actor_name: 'Bob' },
          ],
          recent_decisions: [
            { id: 'dec-1', title: 'Keep preview-first approvals', status: 'noted', actor_name: 'Priya' },
          ],
        }}
        agentAssignments={[
          {
            id: 'asg-1',
            agent_id: 'coordinator',
            agent_label: 'Coordinator',
            agent_kind: 'internal',
            task: 'Review open task',
            mode: 'review',
            status: 'queued',
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
          },
        ]}
      />,
    )

    expect(html.indexOf('class="workdash-next attention"')).toBeLessThan(html.indexOf('class="workdash-status-strip secondary ready"'))
    expect(html.indexOf('class="workdash-status-strip secondary ready"')).toBeLessThan(html.indexOf('Needs review'))
    expect(html.indexOf('class="workdash-status-strip secondary ready"')).toBeLessThan(html.indexOf('Agent activity'))
    expect(html.indexOf('Agent activity')).toBeLessThan(html.indexOf('Needs review'))
    expect(html.indexOf('Needs review')).toBeLessThan(html.indexOf('Assignments</span>'))
    expect(html.indexOf('Assignments</span>')).toBeLessThan(html.indexOf('Assign work'))
    expect(html).not.toContain('Room context')
    expect(html).not.toContain('Open work')
    expect(html).toContain('class="workdash-status-strip secondary ready"')
    expect(html).toContain('aria-label="Agent activity: Assignment queued. Coordinator · queued · review. 1 open."')
    expect(html).not.toContain('class="workdash-brief')
    expect(html).toContain('<details class="workdash-queue-details">')
    expect(html).not.toContain('<details class="workdash-queue-details" open="">')
    expect(html).toContain('<details class="workdash-assignment-details">')
  })

  it('shows room context only while an agent run is active', () => {
    const html = renderToStaticMarkup(
      <WorkDashboardPanel
        {...workDashboardHandlers}
        dashboard={{
          metrics: [{ label: 'Closed', value: 4, tone: 'success' }],
          agents: [{ id: 'coordinator', name: 'Coordinator', kind: 'internal', status: 'online' }],
          approvals: [],
          open_items: [{ id: 'mem-1', title: 'Fix health route', status: 'open', actor_name: 'Alice' }],
          recent_actions: [{ id: 'act-2', title: 'Ran tests', status: 'completed', actor_name: 'Bob' }],
          recent_decisions: [{ id: 'dec-1', title: 'Keep preview-first approvals', status: 'noted', actor_name: 'Priya' }],
        }}
        agentRuns={[{ id: 'run-1', status: 'running', route: 'meeting_patch_closure', summary: 'Review Agent is checking the patch proposal.' }]}
        agentAssignments={[
          {
            id: 'asg-1',
            agent_id: 'coordinator',
            agent_label: 'Coordinator',
            agent_kind: 'internal',
            task: 'Review open task',
            mode: 'review',
            status: 'queued',
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
          },
        ]}
      />,
    )

    expect(html.indexOf('Assign work')).toBeLessThan(html.indexOf('Room context'))
    expect(html).toContain('Agent activity')
    expect(html).toContain('Review Agent is checking the patch proposal.')
    expect(html).toContain('meeting patch closure')
    expect(html).toContain('3 items · 1 running')
    expect(html).toContain('Open work')
    expect(html).toContain('Recent closure')
    expect(html).toContain('Decisions')
  })

  it('caps visible assignment rows and shows the hidden queue count', () => {
    const now = new Date().toISOString()
    const assignments = Array.from({ length: 5 }, (_, index) => ({
      id: `asg-${index + 1}`,
      agent_id: 'coordinator',
      agent_label: 'Coordinator',
      agent_kind: 'internal',
      task: `Queue task ${index + 1}`,
      mode: 'review',
      status: 'queued',
      created_at: now,
      updated_at: now,
    }))
    const html = renderToStaticMarkup(
      <WorkDashboardPanel
        {...workDashboardHandlers}
        dashboard={{
          metrics: [],
          agents: [{ id: 'coordinator', name: 'Coordinator', kind: 'internal', status: 'online' }],
          approvals: [],
          open_items: [],
          recent_actions: [],
          recent_decisions: [],
        }}
        agentAssignments={assignments}
      />,
    )

    expect(html).toContain('Queue task 1')
    expect(html).toContain('Queue task 3')
    expect(html).not.toContain('Queue task 4')
    expect(html).not.toContain('Queue task 5')
    expect(html).toContain('+2 more in this filter')
  })

  it('compacts repeated queue assignments by task, agent, mode, and status', () => {
    const rows = compactQueueAssignments([
      { id: 'asg-1', task: 'Review dashboard', agent_id: 'voiceops', agent_label: 'VoiceOps', agent_kind: 'internal', mode: 'review', status: 'queued' },
      { id: 'asg-2', task: 'Review dashboard', agent_id: 'voiceops', agent_label: 'VoiceOps', agent_kind: 'internal', mode: 'review', status: 'queued' },
      { id: 'asg-3', task: 'Review dashboard', agent_id: 'voiceops', agent_label: 'VoiceOps', agent_kind: 'internal', mode: 'patch', status: 'queued' },
    ])

    expect(rows).toHaveLength(2)
    expect(rows[0].duplicateCount).toBe(2)
    expect(rows[0].duplicateIds).toEqual(['asg-1', 'asg-2'])
    expect(rows[1].duplicateCount).toBe(1)
  })

  it('shows one row for repeated stale assignments instead of a queue wall', () => {
    const old = new Date(Date.now() - 7 * 60 * 60 * 1000).toISOString()
    const assignments = Array.from({ length: 5 }, (_, index) => ({
      id: `asg-repeat-${index + 1}`,
      agent_id: 'agent-voiceops',
      agent_label: 'VoiceOps',
      agent_kind: 'internal',
      task: 'Review the dashboard health queue',
      mode: 'review',
      status: 'queued',
      requested_by_name: 'Priya Nair',
      created_at: old,
      updated_at: old,
    }))
    const html = renderToStaticMarkup(
      <WorkDashboardPanel
        {...workDashboardHandlers}
        dashboard={{
          metrics: [],
          agents: [{ id: 'agent-voiceops', name: 'VoiceOps', kind: 'internal', status: 'online' }],
          approvals: [],
          open_items: [],
          recent_actions: [],
          recent_decisions: [],
        }}
        agentAssignments={assignments}
      />,
    )

    expect(html).toContain('Grouped 4 repeated assignments.')
    expect(html).toContain('Repeated 5 times')
    expect((html.match(/Review the dashboard health queue/g) || [])).toHaveLength(1)
    expect(html).not.toContain('+2 more in this filter')
  })

  it('hides queue filters and clear action when every loaded assignment is open', () => {
    const now = new Date().toISOString()
    const assignments = Array.from({ length: 3 }, (_, index) => ({
      id: `asg-open-${index + 1}`,
      agent_id: 'agent-voiceops',
      agent_label: 'VoiceOps',
      agent_kind: 'internal',
      task: `Review queue item ${index + 1}`,
      mode: 'review',
      status: 'queued',
      created_at: now,
      updated_at: now,
    }))
    const html = renderToStaticMarkup(
      <WorkDashboardPanel
        {...workDashboardHandlers}
        dashboard={{
          metrics: [],
          agents: [{ id: 'agent-voiceops', name: 'VoiceOps', kind: 'internal', status: 'online' }],
          approvals: [],
          open_items: [],
          recent_actions: [],
          recent_decisions: [],
        }}
        agentAssignments={assignments}
      />,
    )

    expect(html).not.toContain('Clear done')
    expect(html).not.toContain('aria-label="Assignment queue filter"')
    expect(html).not.toContain('All 3')
    expect(html).not.toContain('Done 0')
  })

  it('distinguishes loaded queue rows from total open queue health', () => {
    const now = new Date().toISOString()
    const assignments = Array.from({ length: 20 }, (_, index) => ({
      id: `asg-${index + 1}`,
      agent_id: 'coordinator',
      agent_label: 'Coordinator',
      agent_kind: 'internal',
      task: `Queue task ${index + 1}`,
      mode: 'review',
      status: 'queued',
      created_at: now,
      updated_at: now,
    }))
    const html = renderToStaticMarkup(
      <WorkDashboardPanel
        {...workDashboardHandlers}
        dashboard={{
          metrics: [],
          agents: [{ id: 'coordinator', name: 'Coordinator', kind: 'internal', status: 'online' }],
          approvals: [],
          open_items: [],
          recent_actions: [],
          recent_decisions: [],
          readiness: {
            state: 'needs_attention',
            ready: false,
            blockers: ['78 assignments are stale.'],
            warnings: [],
            summary: 'Queue needs attention',
          },
          queue_health: [
            { label: 'Open', value: 78, tone: 'info', detail: '78 assignments are still open.' },
            { label: 'Stale', value: 78, tone: 'warning', detail: '78 assignments are stale.' },
          ],
        }}
        agentAssignments={assignments}
      />,
    )

    expect(html).toContain('20 shown · 58 more')
    expect(html).not.toContain('78</b><span>Open</span>')
    expect(html).not.toContain('<small>78 assignments are stale.</small>')
    expect(html).not.toContain('more work not shown')
    expect(html).not.toContain('more open work in queue index')
    expect(html).not.toContain('<b>Needs attention</b>')
    expect(html).not.toContain('78</b><span>Stale</span>')
  })

  it('explains partial queue state when health reports open work before rows load', () => {
    const html = renderToStaticMarkup(
      <WorkDashboardPanel
        {...workDashboardHandlers}
        dashboard={{
          metrics: [],
          agents: [{ id: 'coordinator', name: 'Coordinator', kind: 'internal', status: 'online' }],
          approvals: [],
          open_items: [],
          recent_actions: [],
          recent_decisions: [],
          queue_health: [
            { label: 'Open', value: 78, tone: 'info', detail: '78 assignments are still open.' },
            { label: 'Stale', value: 78, tone: 'warning', detail: '78 assignments are stale.' },
          ],
        }}
        agentAssignments={[]}
      />,
    )

    expect(html).toContain('78 indexed')
    expect(html).toContain('rows syncing')
    expect(html).not.toContain('loading assignment rows')
    expect(html).toContain('Queue index has 78 open assignments. Rows have not synced yet.')
    expect(html).toContain('Assign work')
    expect(html).toContain('title="queue syncing · 1 agent"')
    expect(html).toContain('<small>queue syncing</small>')
    expect(html).not.toContain('78</b><span>Open</span>')
    expect(html).not.toContain('78</b><span>Stale</span>')
    expect(html).not.toContain('Clear done')
    expect(html).not.toContain('All 0')
    expect(html).not.toContain('Open 0')
    expect(html).not.toContain('Done 0')
  })

  it('keeps the work dashboard zero state concise', () => {
    const html = renderToStaticMarkup(
      <WorkDashboardPanel
        {...workDashboardHandlers}
        dashboard={{
          metrics: [],
          agents: [],
          approvals: [],
          open_items: [],
          recent_actions: [],
          recent_decisions: [],
          queue_health: [
            { label: 'Open', value: 0, tone: 'neutral', detail: '0 assignments are still open.' },
            { label: 'Blocked', value: 0, tone: 'neutral', detail: '0 blockers.' },
            { label: 'Approvals', value: 0, tone: 'neutral', detail: '0 approvals.' },
          ],
        }}
        agentAssignments={[]}
      />,
    )

    expect(html).toContain('Agents')
    expect(html).toContain('Off')
    expect(html).toContain('connect coding agent')
    expect(html).toContain('Use typed commands')
    expect(html).toContain('Ask memory or repo questions. Connect a coding agent later for review, test, or patch work.')
    expect(html).toContain('class="workdash-status-strip secondary ready"')
    expect(html).not.toContain('No coding agents')
    expect(html).not.toContain('Commands ready · assignment off')
    expect(html).not.toContain('Use text or live meeting for memory and code questions.')
    expect(html).not.toContain('Connect a coding agent in Agent setup before assigning review, test, or patch work.')
    expect(html).not.toContain('No coding agent ready')
    expect(html).not.toContain('Use the live meeting or type a command now. Connect a coding agent')
    expect(html).not.toContain('aria-label="Queue health summary"')
  })

  it('renders all connected external coding agents as assignable options with auto routing', () => {
    const html = renderToStaticMarkup(
      <WorkDashboardPanel
        {...workDashboardHandlers}
        dashboard={{
          metrics: [],
          agents: [],
          approvals: [],
          open_items: [],
          recent_actions: [],
          recent_decisions: [],
        }}
        externalAgentProviders={[
          externalProvider({
            provider: 'claude',
            label: 'Claude Code',
            supported_models: ['claude-sonnet-4-6', 'claude-opus-4-8', 'claude-haiku-4-5'],
            default_model: 'claude-sonnet-4-6',
          }),
          externalProvider({
            provider: 'codex',
            label: 'OpenAI Codex',
            supported_models: ['gpt-5.5', 'gpt-5.4', 'gpt-5-mini'],
            default_model: 'gpt-5.4',
          }),
          externalProvider({
            provider: 'cursor',
            label: 'Cursor',
            supported_models: ['cursor-default', 'cursor-sonnet', 'cursor-fast'],
          }),
          externalProvider({
            provider: 'local',
            label: 'Local Open Agent',
            supported_models: ['local-default', 'glm-5.2-local', 'qwen-coder-local'],
          }),
        ]}
      />,
    )

    expect(html).toContain('aria-label="Assignment agent"')
    expect(html).toContain('<option value="claude"')
    expect(html).toContain('Claude Code')
    expect(html).toContain('<option value="codex"')
    expect(html).toContain('OpenAI Codex')
    expect(html).toContain('<option value="cursor"')
    expect(html).toContain('Cursor')
    expect(html).toContain('<option value="local"')
    expect(html).toContain('Local Open Agent')
    expect(html).toContain('<option value="auto"')
    expect(html).toContain('Auto external agent · recommended route')
    expect(html).toContain('aria-label="Assignment model"')
    expect(html).toContain('claude-sonnet-4-6')
    expect(html).toContain('claude-opus-4-8')
    expect(html).toContain('claude-haiku-4-5')
  })

  it('keeps the external agent default prompt short enough for the rail', () => {
    const html = renderRightRail({
      externalAgentProviders: [
        externalProvider({
          provider: 'codex',
          label: 'OpenAI Codex',
        }),
      ],
    })

    expect(html).toContain('aria-label="External agent prompt"')
    expect(html).toContain('value="review dashboard"')
    expect(html).toContain('<details class="external-agent-run-details">')
    expect(html).not.toContain('<details class="external-agent-run-details" open="">')
    expect(html).toContain('Advanced run control')
    expect(html).toContain('Work dashboard is the main flow')
    expect(html).not.toContain('review the latest dashboard changes')
  })

  it('keeps coding agent summary focused on the current setup blocker', () => {
    expect(externalAgentPanelBoundary()).toBe(
      'Code-changing work requires a local CLI agent already logged in on this machine. OAuth/API credentials are read-only; do not paste passwords, tokens, or browser cookies.',
    )
    expect(externalAgentPanelSummary([])).toEqual({
      tone: 'blocked',
      title: 'No coding agents',
      meta: 'connect one',
      detail: 'Connect Codex, Claude Code, Cursor, or a local CLI before assigning code work.',
    })

    const readOnly = externalAgentPanelSummary([
      externalProvider({
        connected: true,
        auth_method: 'api_key',
        mode_readiness: {
          review: { ready: true, detail: 'ready' },
          test: { ready: true, detail: 'ready' },
          explain: { ready: true, detail: 'ready' },
          patch: { ready: false, detail: 'Patch mode requires a local CLI credential.' },
        },
      }),
    ])

    expect(readOnly).toEqual({
      tone: 'warning',
      title: 'Read-only agent setup',
      meta: '1 read-only · 1/1 connected',
      detail: 'Patch mode requires a local CLI credential.',
    })

    const html = renderRightRail({ externalAgentProviders: [] })
    expect(html).toContain('Code-changing work requires a local CLI agent already logged in')
    expect(html).toContain('OAuth/API credentials are read-only')
    expect(html).toContain('do not paste passwords, tokens, or browser cookies')
  })

  it('uses the customized agent name in work dashboard readiness copy', () => {
    const html = renderToStaticMarkup(
      <WorkDashboardPanel
        {...workDashboardHandlers}
        agentName="Atlas"
        dashboard={{
          metrics: [],
          agents: [],
          approvals: [],
          open_items: [],
          recent_actions: [],
          recent_decisions: [],
        }}
        agentAssignments={[
          {
            id: 'asg-memory-1',
            agent_id: 'auto',
            agent_label: 'Auto external agent',
            agent_kind: 'external',
            task: 'summarize open memory',
            mode: 'memory',
            status: 'queued',
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
          },
        ]}
        externalAgentProviders={[
          externalProvider({
            provider: 'local',
            label: 'Local Open Agent',
            supported_models: ['local-default'],
          }),
        ]}
      />,
    )

    expect(html).toContain('Memory assignments use the internal Atlas memory agent.')
    expect(html).not.toContain('internal VoiceOps memory agent')
  })

  it('renders auto assignment provider recommendation in the queue', () => {
    const html = renderRightRail({
      workDashboard: {
        metrics: [],
        agents: [],
        approvals: [],
        open_items: [],
        recent_actions: [],
        recent_decisions: [],
      },
      agentAssignments: [
        {
          id: 'asg-auto-1',
          agent_id: 'auto',
          agent_label: 'Auto external agent',
          agent_kind: 'external',
          task: 'AI have the best agent fix that',
          mode: 'patch',
          status: 'queued',
          requested_by_name: 'Priya Nair',
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
          metadata: {
            recommended_provider: 'local',
            recommended_label: 'Local Open Agent',
            recommended_mode: 'patch',
            recommended_model: 'local-default',
            recommended_task_kind: 'write_patch',
            recommendation_ready: true,
            recommendation_confidence: 0.86,
            recommendation_reason: 'Local provider is connected and supports patch mode.',
          },
        },
      ],
      externalAgentProviders: [
        {
          provider: 'local',
          label: 'Local Open Agent',
          connected: true,
          mode_readiness: {
            patch: { ready: true, detail: 'ready' },
          },
        },
      ],
    })

    expect(html).toContain('Local Open Agent · patch · local-default · 86%')
    expect(html).toContain('title="Local provider is connected and supports patch mode."')
    expect(html).toContain('Auto local')
  })

  it('renders operator acceptance stage visibility and strict command', () => {
    const html = renderToStaticMarkup(
      <OperatorAcceptancePanel
        report={{
          status: 'needs_attention',
          accepted: false,
          duration_ms: 1250,
          run_harnesses: false,
          stages: [
            {
              id: 'team_onboarding',
              label: 'Team onboarding package',
              status: 'ready',
              ready: true,
              required: true,
              duration_ms: 10,
              summary: 'ready',
            },
            {
              id: 'final_acceptance',
              label: 'Final multi-person AI teammate acceptance',
              status: 'needs_attention',
              ready: false,
              required: true,
              duration_ms: 12,
              summary: 'missing strict harness evidence',
              next_steps: ['Run strict acceptance.'],
            },
          ],
          next_steps: ['Run strict acceptance.'],
        }}
      />,
    )

    expect(html).toContain('Operator acceptance')
    expect(html).toContain('needs attention')
    expect(html).toContain('recorded evidence')
    expect(html).toContain('Team onboarding package')
    expect(html).toContain('Final multi-person AI teammate acceptance')
    expect(html).toContain('Run strict acceptance.')
    expect(html).toContain('python scripts/operator_acceptance.py --json --require-accepted')
  })

  it('renders event-store migration readiness', () => {
    const html = renderToStaticMarkup(
      <EventStoreReadinessPanel
        eventStore={{
          backend: 'json',
          status: 'migration_available',
          ready: false,
          path: '/tmp/collab.json',
          runtime_mode: 'development_json',
          production_ready: false,
          local_demo_acceptable: true,
          bootstrap_command: 'cd backend && python scripts/bootstrap_local_runtime.py --env-file .env.local --workspace /absolute/path/to/team/repository --json',
          migration_available: true,
          migration_command: 'cd backend && python scripts/migrate_collab_json_to_sqlite.py --json data/collaboration.json --sqlite data/collaboration.sqlite3',
          cutover_env: {
            COLLAB_STORE_BACKEND: 'sqlite',
            COLLAB_SQLITE_PATH: '/tmp/collab.sqlite3',
          },
          event_count: 0,
          stream_count: 0,
          state_counts: { rooms: 1, messages: 2, actions: 1 },
          checks: [
            {
              id: 'backend',
              label: 'SQLite backend configured',
              ready: false,
              status: 'migration_available',
              detail: 'json backend is usable for local development.',
            },
          ],
          warnings: ['Run the migration before production.'],
        }}
      />,
    )

    expect(html).toContain('Event store')
    expect(html).toContain('migration_available')
    expect(html).toContain('Backend')
    expect(html).toContain('json')
    expect(html).toContain('development_json')
    expect(html).toContain('production requires SQLite')
    expect(html).toContain('rooms: 1')
    expect(html).toContain('messages: 2')
    expect(html).toContain('SQLite backend configured')
    expect(html).toContain('Production cutover')
    expect(html).toContain('COLLAB_STORE_BACKEND=sqlite')
    expect(html).toContain('COLLAB_SQLITE_PATH=/tmp/collab.sqlite3')
    expect(html).toContain('bootstrap_local_runtime.py')
    expect(html).toContain('migrate_collab_json_to_sqlite.py')
  })

  it('renders target readiness bootstrap actions without hiding later blockers', () => {
    const html = renderToStaticMarkup(
      <TargetReadinessPanel
        target={{
          status: 'needs_attention',
          ready: false,
          score: 57,
          ready_count: 4,
          total_count: 7,
          next_steps: ['Run the local runtime bootstrap with a git workspace.'],
          milestones: [
            {
              id: 'workspace_git',
              label: 'Workspace + git workflow',
              ready: false,
              status: 'needs_attention',
              detail: 'Workspace is not connected',
              next_action: 'Run the local runtime bootstrap with a git workspace.',
              command: 'cd backend && python scripts/bootstrap_local_runtime.py --env-file .env.local --workspace /absolute/path/to/team/repository --json',
            },
            {
              id: 'approval_git_tests',
              label: 'Approval + branch + tests',
              ready: false,
              status: 'unproven',
              evidence: 'passed | 2 speakers | 3/3 chunks',
              next_action: 'Run the mock closure gate after git is connected.',
              command: 'cd frontend && npm run e2e:all -- --json',
            },
            {
              id: 'durable_event_store',
              label: 'Durable event store',
              ready: false,
              status: 'migration_available',
              next_action: 'Run the local runtime bootstrap or migrate collaboration memory to SQLite event-store mode.',
              command: 'cd backend && python scripts/bootstrap_local_runtime.py --env-file .env.local --workspace /absolute/path/to/team/repository --json',
            },
          ],
        }}
      />,
    )

    expect(html).toContain('Target closure')
    expect(html).toContain('57% · 4/7')
    expect(html).toContain('Readiness actions')
    expect(html).toContain('3 blockers')
    expect(html).toContain('Workspace + git workflow')
    expect(html).toContain('Approval + branch + tests')
    expect(html).toContain('Durable event store')
    expect(html).toContain('bootstrap_local_runtime.py')
    expect(html).toContain('npm run e2e:all')
  })

  it('renders deployment hardening blockers and commands', () => {
    const html = renderToStaticMarkup(
      <DeploymentHardeningPanel
        hardening={{
          status: 'needs_attention',
          ready: false,
          environment: 'production',
          score: 72,
          ready_count: 8,
          total_count: 11,
          next_steps: ['Rotate JWT_SECRET with a generated secret.'],
          commands: ['openssl rand -hex 32'],
          checks: [
            {
              id: 'jwt_secret',
              label: 'JWT secret is non-default and strong',
              ready: false,
              status: 'needs_attention',
              severity: 'critical',
              detail: 'JWT secret is default.',
              action: 'Rotate JWT_SECRET with a generated secret.',
            },
            {
              id: 'startup_security_gate',
              label: 'Production startup gate fails closed',
              ready: false,
              status: 'needs_attention',
              severity: 'critical',
              detail: 'PRODUCTION_STARTUP_SECURITY_GATE is disabled.',
              action: 'Set PRODUCTION_STARTUP_SECURITY_GATE=true.',
              evidence: {
                environment: 'production',
                production_startup_security_gate: false,
                startup_gate: 'blocked',
              },
            },
            {
              id: 'event_store_readiness',
              label: 'SQLite event store is initialized',
              ready: true,
              status: 'ready',
              severity: 'high',
              detail: 'sqlite event store has 0 events.',
            },
          ],
        }}
        cutover={{
          status: 'needs_attention',
          ready: false,
          next_steps: [
            'Set JWT_SECRET to a generated 32+ character secret in production secret storage.',
            'Set SEED_DEMO_USERS=false and replace seeded demo accounts with real users before production.',
          ],
          checks: [
            {
              id: 'bootstrap_secret_removed',
              label: 'Temporary bootstrap password file is deleted',
              ready: false,
              status: 'failed',
              severity: 'critical',
              summary: 'Temporary bootstrap password file still exists at /tmp/bootstrap-admin.txt.',
              next_step: 'Delete bootstrap-admin.txt after account rotation.',
            },
            {
              id: 'real_users',
              label: 'Real admin exists and seeded demo users are absent',
              ready: true,
              status: 'passed',
              severity: 'critical',
              summary: '1 real admin user; no seeded demo users.',
            },
          ],
        }}
      />,
    )

    expect(html).toContain('Deployment hardening')
    expect(html).toContain('72% · 8/11')
    expect(html).toContain('production · needs attention')
    expect(html).toContain('Startup gate')
    expect(html).toContain('fail-closed off')
    expect(html).toContain('blocked')
    expect(html).toContain('JWT secret is non-default and strong')
    expect(html).toContain('Team cutover')
    expect(html).toContain('Production cutover actions')
    expect(html).toContain('Temporary bootstrap password file still exists')
    expect(html).toContain('Set JWT_SECRET to a generated 32+ character secret in production secret storage.')
    expect(html).toContain('Set SEED_DEMO_USERS=false and replace seeded demo accounts with real users before production.')
    expect(html).toContain('Real admin exists and seeded demo users are absent')
    expect(html).toContain('Rotate JWT_SECRET with a generated secret.')
    expect(html).toContain('openssl rand -hex 32')
  })

  it('dedupes and limits production cutover action steps', () => {
    expect(cutoverActionSteps({
      next_steps: ['Rotate secrets.', 'Rotate secrets.'],
      checks: [
        { ready: false, next_step: 'Disable demo users.' },
        { ready: false, next_step: 'Configure HF_TOKEN.' },
        { ready: false, next_step: 'Delete bootstrap secret.' },
      ],
    })).toEqual(['Rotate secrets.', 'Disable demo users.', 'Configure HF_TOKEN.'])
  })

  it('renders deployment startup gate as enforced when ready', () => {
    const html = renderToStaticMarkup(
      <DeploymentHardeningPanel
        hardening={{
          status: 'ready',
          ready: true,
          environment: 'production',
          score: 100,
          ready_count: 12,
          total_count: 12,
          checks: [
            {
              id: 'startup_security_gate',
              label: 'Production startup gate fails closed',
              ready: true,
              status: 'ready',
              severity: 'critical',
              detail: 'Startup security gate would allow this production configuration.',
              evidence: {
                environment: 'production',
                production_startup_security_gate: true,
                startup_gate: 'passed',
              },
            },
          ],
        }}
        cutover={{
          status: 'ready',
          ready: true,
          checks: [
            {
              id: 'runtime_settings_loaded',
              label: 'Running backend settings are loaded',
              ready: true,
              status: 'passed',
              severity: 'medium',
              summary: 'Runtime settings are active.',
            },
          ],
        }}
      />,
    )

    expect(html).toContain('Startup gate')
    expect(html).toContain('enforced')
    expect(html).toContain('fail-closed on')
    expect(html).toContain('passed')
    expect(html).toContain('Team cutover')
    expect(html).toContain('bootstrap removed · real users · secrets ready')
  })

  it('renders ready sqlite event-store metrics', () => {
    const html = renderToStaticMarkup(
      <EventStoreReadinessPanel
        eventStore={{
          backend: 'sqlite',
          status: 'ready',
          ready: true,
          path: '/tmp/collab.sqlite3',
          runtime_mode: 'durable_sqlite',
          production_ready: true,
          local_demo_acceptable: true,
          migration_available: false,
          event_count: 12,
          stream_count: 4,
          latest_position: 12,
          state_counts: { rooms: 1, messages: 6 },
          checks: [
            { id: 'schema', label: 'Authoritative state schema', ready: true, status: 'ready', detail: 'ok' },
          ],
          warnings: [],
        }}
      />,
    )

    expect(html).toContain('Event store')
    expect(html).toContain('ready')
    expect(html).toContain('sqlite')
    expect(html).toContain('durable_sqlite')
    expect(html).toContain('production ready')
    expect(html).toContain('>12</b>')
    expect(html).toContain('>4</b>')
    expect(html).not.toContain('migrate_collab_json_to_sqlite.py')
    expect(html).not.toContain('Production cutover')
  })

  it('renders per-role LLM runtime readiness', () => {
    const html = renderToStaticMarkup(
      <AgentLLMRoutingPanel
        routing={{
          room_id: 'main',
          routes: [
            { role: 'code', provider: 'openai_compatible', model: 'glm-5.2', base_url: 'http://127.0.0.1:8000/v1' },
          ],
          runtime: [
            {
              role: 'code',
              effective_provider: 'openai_compatible',
              effective_model: 'glm-5.2',
              ready: true,
              credential_source: 'user_connection',
              status: 'ready',
              detail: 'ready',
            },
            {
              role: 'review',
              effective_provider: 'openai',
              effective_model: 'gpt-5.4',
              ready: false,
              credential_source: 'missing',
              status: 'missing_credential',
              detail: 'missing key',
            },
          ],
        }}
      />,
    )

    expect(html).toContain('Agent LLM runtime matrix')
    expect(html).toContain('openai_compatible')
    expect(html).toContain('glm-5.2')
    expect(html).toContain('user_connection')
    expect(html).toContain('missing_credential')
  })

  it('renders Anthropic route selection and Claude model presets', () => {
    const html = renderToStaticMarkup(
      <AgentLLMRoutingPanel
        routing={{
          room_id: 'main',
          routes: [
            { role: 'review', provider: 'anthropic', model: 'claude-sonnet-4-5', base_url: null },
          ],
          runtime: [
            {
              role: 'review',
              effective_provider: 'anthropic',
              effective_model: 'claude-sonnet-4-5',
              ready: true,
              credential_source: 'user_connection',
              status: 'ready',
              detail: 'ready',
            },
          ],
        }}
      />,
    )

    expect(agentLLMModelPlaceholder('anthropic')).toBe('claude-sonnet-4-5')
    expect(html).toContain('Anthropic')
    expect(html).toContain('claude-sonnet-4-5')
    expect(html).toContain('claude-opus-4-1')
    expect(html).toContain('claude-haiku-3-5')
    expect(html).toContain('user_connection')
  })

  it('renders LLM credential connection controls with provider status', () => {
    const html = renderToStaticMarkup(
      <LLMCredentialsPanel
        providers={[
          {
            provider: 'openai',
            label: 'OpenAI API',
            connected: false,
            status: 'disconnected',
            detail: 'Not connected.',
          },
          {
            provider: 'anthropic',
            label: 'Anthropic Claude',
            connected: true,
            model: 'claude-sonnet-4-5',
            token_preview: 'sk-a...7890',
            detail: 'Connected.',
          },
          {
            provider: 'openai_compatible',
            label: 'OpenAI-compatible / GLM',
            connected: false,
            detail: 'Not connected.',
          },
        ]}
        initialPreflightResult={{
          ready: false,
          detail: 'Provider is not configured.',
          blockers: ['missing_openai_api_key'],
        }}
      />,
    )

    expect(llmProviderSummary([{ connected: true }, { connected: false }])).toBe('1/2 connected')
    expect(html).toContain('LLM credentials')
    expect(html).toContain('Connected LLM providers')
    expect(html).toContain('Anthropic Claude')
    expect(html).toContain('sk-a...7890')
    expect(html).toContain('GLM/local')
    expect(html).toContain('stored encrypted')
    expect(html).toContain('Provider blocked')
    expect(html).toContain('missing_openai_api_key')
  })

  it('includes LLM credentials above per-role routing in agent setup', () => {
    const html = renderToStaticMarkup(
      <RightRail
        workspace={{ connected: true, name: 'repo' }}
        actions={[]}
        memory={[]}
        handoff={{ lines: [] }}
        agentRuns={[]}
        agentAssignments={[]}
        externalAgentProviders={[]}
        llmProviders={[
          { provider: 'anthropic', label: 'Anthropic Claude', connected: true, model: 'claude-sonnet-4-5' },
        ]}
        agentLLMRouting={{
          room_id: 'main',
          routes: [
            { role: 'review', provider: 'anthropic', model: 'claude-sonnet-4-5', base_url: null },
          ],
          runtime: [],
        }}
      />,
    )

    expect(html).toContain('1/1 connected')
    expect(html.indexOf('LLM credentials')).toBeLessThan(html.indexOf('Agent LLM routing'))
  })

  it('renders agent route preflight controls and result', () => {
    const html = renderToStaticMarkup(
      <AgentLLMRoutingPanel
        onPreflightRoute={() => Promise.resolve({ ready: true })}
        initialPreflightResult={{
          ready: false,
          status: 'missing_credential',
          detail: 'OpenAI route is selected but no API key is available.',
          blockers: ['Connect OpenAI or configure OPENAI_API_KEY.'],
          latency_ms: null,
        }}
        routing={{
          room_id: 'main',
          routes: [
            { role: 'review', provider: 'openai', model: 'gpt-5.4', base_url: null },
          ],
          runtime: [],
        }}
      />,
    )

    expect(html).toContain('Test route')
    expect(html).toContain('Preflight')
    expect(html).toContain('missing_credential')
    expect(html).toContain('OpenAI route is selected but no API key is available.')
    expect(html).toContain('Connect OpenAI or configure OPENAI_API_KEY.')
  })
})
