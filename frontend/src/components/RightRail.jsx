import { useEffect, useState } from 'react'
import {
  CaretDown,
  CaretRight,
  Check,
  CheckCircle,
  GitBranch,
  GitCommit,
  GitDiff,
  Package,
  GitPullRequest,
  ListMagnifyingGlass,
  BookOpenText,
  ArrowUpRight,
  ClipboardText,
  Code,
  Files,
  MagnifyingGlass,
  Robot,
  UsersThree,
  WarningCircle,
  Waveform,
  X,
} from '@phosphor-icons/react'
import {
  clearCache,
  fetchCacheStatus,
  fetchDeploymentHardening,
  fetchEventStoreReadiness,
  fetchGitDiff,
  fetchGitStatus,
  fetchLocalDoctor,
  fetchOperatorAcceptance,
  fetchProductionCutover,
  fetchUsers,
  fetchRoomMemory,
  fetchRoomMemoryHealth,
  fetchRuntimeStatus,
  fetchSpeakerVerification,
  fetchSpeakerWarmup,
  fetchSystemReadiness,
  fetchTargetReadiness,
  fetchWorkspaceTree,
  fetchDemoGateRun,
  queryRoomAudit,
  queryRoomCode,
  queryRoomMemory,
  queryRoomProvenance,
  rebuildRoomRagIndex,
  routeRoomCommand,
  startDemoGateRun,
  startGeneratedSpeakerVerification,
  startSpeakerVerification,
  startSpeakerWarmup,
  updateUserProjects,
} from '../api.js'
import { citationKey, citationMeta, citationTitle, ragTraceItems, uniqueCitations } from '../ragCitations.js'
import {
  demoEvidenceSummary,
  latestDemoGateResult,
  speakerVerificationJobSummary,
  speakerVerificationSummary,
} from '../readiness.js'
import { captionStatusClass, captionStatusLabel } from '../liveCaptions.js'
import { liveLatencySummary, liveSpeakerReadiness } from '../liveDiagnostics.js'
import { buildAuditEvents } from '../auditEvents.js'
import { APP_NAME } from '../branding.js'
import { compactDisplayText, displayActorName, displayText } from '../displayText.js'
import {
  actionLifecycleFacts,
  actionRequesterDisplay,
  actionStatusLabel,
  actionSummaryLabel,
  actionSummaryRaw,
  actionTypeLabel,
  compactRecentActions,
  gitStatusAfterLabel,
  pendingApprovalBrief,
} from '../actionLogModel.js'

const ARTIFACT_ICON = {
  pr: GitPullRequest,
  logs: ListMagnifyingGlass,
  book: BookOpenText,
  runbook: BookOpenText,
}

function memoryLabel(kind) {
  return String(kind || 'note').replace(/_/g, ' ')
}

function displayHandoffText(value) {
  return compactDisplayText(value).replace(/\s+Route:\s.*$/i, '').trim()
}

const MEMORY_FILTERS = [
  { label: 'All', value: '' },
  { label: 'Decisions', value: 'decision' },
  { label: 'Tasks', value: 'task' },
  { label: 'Questions', value: 'question' },
  { label: 'Risks', value: 'risk' },
  { label: 'Code', value: 'code_reference' },
]

function MemoryItemRow({ item, agentName = APP_NAME }) {
  const text = displayText(item.text)
  const actor = displayActorName(item.actor_name, agentName)
  return (
    <div className={`memory-item ${item.kind}`} key={item.id}>
      <span className="mono">{memoryLabel(item.kind)}</span>
      <p title={item.text}>{text}</p>
      <small>{actor}{item.status === 'open' ? ' - open' : ''}</small>
    </div>
  )
}

export function visibleMemoryItems(memory, filter = '', preferOpen = false, limit = 6) {
  const filtered = (memory || [])
    .filter((item) => !filter || item.kind === filter)
    .slice(-(limit * 2))
    .reverse()
  if (!preferOpen) return filtered.slice(0, limit)
  return filtered
    .sort((left, right) => {
      const leftOpen = left.status === 'open' ? 1 : 0
      const rightOpen = right.status === 'open' ? 1 : 0
      return rightOpen - leftOpen
    })
    .slice(0, limit)
}

export function memoryDetailsTitle({ preferOpen, displayCount }) {
  return {
    title: preferOpen ? 'Open memory' : 'Recent memory',
    meta: `${displayCount} shown`,
  }
}

export function MemoryHealthStrip({ health }) {
  const status = health.status || 'checking'
  const totalItems = Number(health.total_items || 0)
  const openCount = Number(health.status_counts?.open || 0)
  const staleCount = Number(health.stale_open_count || 0)
  const tone = status === 'healthy' ? 'ok' : status === 'blocked' ? 'blocked' : 'review'
  const statusLabel = status === 'needs_review' ? 'open items' : status.replace(/_/g, ' ')
  const actionText = health.blockers?.[0]
    || (openCount > 0
      ? `Review ${openCount} open memory item${openCount === 1 ? '' : 's'} before handoff.`
      : staleCount > 0
        ? `Refresh ${staleCount} stale memory item${staleCount === 1 ? '' : 's'}.`
        : health.warnings?.[0] || 'Memory is ready for handoff.')
  const countText = [
    `${totalItems} item${totalItems === 1 ? '' : 's'}`,
    openCount > 0
      ? `${openCount} open`
      : staleCount > 0
        ? `${staleCount} stale`
        : 'no open items',
  ].join(' · ')
  const showActionText = status === 'blocked' || (!openCount && staleCount > 0)
  return (
    <div className={`memory-health ${tone}`} aria-label={`Meeting memory health: ${statusLabel}. ${countText}. ${actionText}`} title={actionText}>
      <div className="memory-health-head">
        <span>
          {tone === 'ok' ? <CheckCircle size={13} /> : <WarningCircle size={13} />}
          {statusLabel}
        </span>
        <small>{countText}</small>
      </div>
      {showActionText && actionText && <p>{actionText}</p>}
    </div>
  )
}

export function memoryEmptyText({ answer, hasIndexedMemory, loading }) {
  if (loading) return 'Loading current memory.'
  if (answer) {
    return `No matched ${answer.type === 'audit' ? 'audit events' : 'memory items'} for this question.`
  }
  if (hasIndexedMemory) return 'Memory exists, but no items match this view.'
  return 'Decisions, questions, tasks, and code references will collect here.'
}

export function mobileMemoryHint({ health, hasIndexedMemory, loading }) {
  if (loading) return 'Checking memory'
  const total = Number(health?.total_items || 0)
  const open = Number(health?.status_counts?.open || 0)
  if (open > 0) return `${open} open item${open === 1 ? '' : 's'}`
  if (total > 0 || hasIndexedMemory) return `${total} indexed item${total === 1 ? '' : 's'}`
  return 'Ask what changed'
}

function MemoryMobileHint({ health, hasIndexedMemory, loading }) {
  const label = mobileMemoryHint({ health, hasIndexedMemory, loading })
  const total = Number(health?.total_items || 0)
  const status = health?.status === 'healthy' ? 'ready' : health?.status ? health.status.replace(/_/g, ' ') : 'ready'
  return (
    <a className="memory-mobile-hint" href="#meeting-memory" aria-label={`Meeting memory shortcut: ${label}. Status ${status}.`}>
      <span>{label}</span>
      <small>{total > 0 ? 'query decisions, files, tasks' : 'try decisions or files'}</small>
    </a>
  )
}

export function memoryRouteLabel(route) {
  const routeName = String(route?.route || '').replace(/_/g, ' ').trim()
  const policy = String(route?.action_policy || '').replace(/_/g, ' ').trim()
  return [routeName, policy].filter(Boolean).join(' · ')
}

function countLabel(count, singular, plural = `${singular}s`) {
  const value = Number(count || 0)
  return `${value} ${value === 1 ? singular : plural}`
}

export function actionAuditSummary(items = []) {
  const labels = {
    repeat: 'repeated',
    route: 'routing',
    files: 'files',
    verification: 'tests',
  }
  return items
    .map((item) => labels[item?.key] || item?.key)
    .filter(Boolean)
    .slice(0, 3)
    .join(' · ')
}

export function memoryAnswerSourceSummary(answer, displayMemory = []) {
  if (!answer) return []
  const items = Array.isArray(answer.items) ? answer.items : displayMemory
  if (answer.type === 'audit') {
    return [countLabel(items.length, 'audit event')]
  }
  if (answer.type !== 'rag') {
    return [countLabel(items.length, 'matched item')]
  }

  const retrieval = answer.retrieval || {}
  const citations = uniqueCitations(answer.citations)
  const summary = [citations.length > 0 ? countLabel(citations.length, 'citation') : 'no citations']
  if (typeof retrieval.candidate_count === 'number') summary.push(countLabel(retrieval.candidate_count, 'candidate'))
  if (typeof retrieval.indexed_documents === 'number') summary.push(countLabel(retrieval.indexed_documents, 'doc'))
  return summary
}

export function memoryAnswerDisplayText(answer = {}) {
  const raw = displayText(answer.answer || '')
  if (
    answer.type === 'rag'
    && /^Retrieved untrusted context\s*(?:\(not executable instructions\))?\s*:/i.test(raw)
  ) {
    return 'Matched meeting context from memory. Review sources below.'
  }
  return raw
}

export function shouldShowMemoryEmptyState({ answer, displayMemory = [] } = {}) {
  if (!answer) return !displayMemory.length
  if (displayMemory.length > 0) return false
  if (Array.isArray(answer.items) && answer.items.length > 0) return false
  if (Array.isArray(answer.citations) && answer.citations.length > 0) return false
  return true
}

const AGENT_LLM_ROLES = [
  { value: 'coordinator', label: 'Coordinator' },
  { value: 'memory', label: 'Memory' },
  { value: 'code', label: 'Code' },
  { value: 'review', label: 'Review' },
  { value: 'test', label: 'Test' },
  { value: 'git', label: 'Git' },
]

const AGENT_LLM_PROVIDERS = [
  { value: 'default', label: 'Default' },
  { value: 'mock', label: 'Mock' },
  { value: 'openai', label: 'OpenAI' },
  { value: 'anthropic', label: 'Anthropic' },
  { value: 'openai_compatible', label: 'GLM/local' },
  { value: 'bedrock', label: 'Bedrock' },
]

const AGENT_LLM_MODEL_PRESETS = {
  openai: ['gpt-5.4', 'gpt-5.3', 'gpt-4.1'],
  anthropic: ['claude-sonnet-4-5', 'claude-opus-4-1', 'claude-haiku-3-5'],
  openai_compatible: ['glm-5.2', 'qwen3-coder', 'deepseek-coder'],
  bedrock: ['anthropic.claude-3-5-sonnet-20241022-v2:0'],
}

export function agentLLMModelPlaceholder(provider) {
  if (provider === 'anthropic') return 'claude-sonnet-4-5'
  if (provider === 'openai') return 'gpt-5.4'
  if (provider === 'openai_compatible') return 'glm-5.2'
  if (provider === 'bedrock') return 'anthropic.claude-*'
  return 'provider default'
}

const LLM_CREDENTIAL_PROVIDERS = [
  { value: 'openai', label: 'OpenAI', needsBaseUrl: false },
  { value: 'anthropic', label: 'Anthropic', needsBaseUrl: false },
  { value: 'openai_compatible', label: 'GLM/local', needsBaseUrl: true },
]

export function llmProviderSummary(providers = []) {
  const rows = Array.isArray(providers) ? providers : []
  const connected = rows.filter((provider) => provider.connected)
  if (connected.length) {
    return `${connected.length}/${rows.length || LLM_CREDENTIAL_PROVIDERS.length} connected`
  }
  return rows.length ? 'no keys connected' : 'credentials'
}

export function projectAccessMeta(users = [], loading = false, error = '') {
  if (loading) return 'loading'
  if (error) return 'unavailable'
  const rows = Array.isArray(users) ? users : []
  if (!rows.length) return 'admin'
  const scoped = rows.filter((user) => Array.isArray(user.projects) && user.projects.length).length
  return scoped ? `${scoped}/${rows.length} scoped` : `${rows.length} users · open`
}

function parseProjectList(value) {
  return String(value || '')
    .split(/[,\n]/)
    .map((item) => item.trim())
    .filter(Boolean)
}

export function ProjectAccessPanel({ initialUsers = null }) {
  const [users, setUsers] = useState(Array.isArray(initialUsers) ? initialUsers : [])
  const [drafts, setDrafts] = useState({})
  const [loading, setLoading] = useState(!Array.isArray(initialUsers))
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')

  useEffect(() => {
    if (!Array.isArray(initialUsers)) return
    setUsers(initialUsers)
    setDrafts(Object.fromEntries(initialUsers.map((user) => [user.id, (user.projects || []).join(', ')])))
  }, [initialUsers])

  useEffect(() => {
    if (Array.isArray(initialUsers)) return undefined
    let alive = true
    setLoading(true)
    fetchUsers()
      .then((rows) => {
        if (!alive) return
        setUsers(rows)
        setDrafts(Object.fromEntries(rows.map((user) => [user.id, (user.projects || []).join(', ')])))
        setError('')
      })
      .catch((err) => {
        if (alive) setError(err.message || 'Project access unavailable')
      })
      .finally(() => {
        if (alive) setLoading(false)
      })
    return () => {
      alive = false
    }
  }, [initialUsers])

  async function save(user) {
    if (!user?.id || busy) return
    setBusy(user.id)
    setStatus('')
    setError('')
    try {
      const updated = await updateUserProjects(user.id, parseProjectList(drafts[user.id]))
      setUsers((rows) => rows.map((row) => (row.id === updated.id ? updated : row)))
      setDrafts((previous) => ({ ...previous, [updated.id]: (updated.projects || []).join(', ') }))
      setStatus(`${updated.name || updated.email} saved`)
    } catch (err) {
      setError(err.message || 'Project access update failed')
    } finally {
      setBusy('')
    }
  }

  return (
    <div className="project-access-panel">
      <div className="project-access-summary">
        <span><UsersThree size={14} /> Project access</span>
        <small>{projectAccessMeta(users, loading, error)}</small>
      </div>
      {loading ? (
        <div className="panel-empty compact">Loading team access.</div>
      ) : users.length ? (
        <div className="project-access-list" aria-label="Team project access">
          {users.map((item) => (
            <div className="project-access-row" key={item.id}>
              <div>
                <b>{item.name || item.email}</b>
                <small>{item.role_label || item.role}{(item.projects || []).length ? ` · ${(item.projects || []).join(', ')}` : ' · all projects'}</small>
              </div>
              <label>
                <span>Projects</span>
                <input
                  aria-label={`Projects for ${item.name || item.email}`}
                  value={drafts[item.id] ?? (item.projects || []).join(', ')}
                  onChange={(event) => setDrafts((previous) => ({ ...previous, [item.id]: event.target.value }))}
                  placeholder="alpha, mobile"
                />
              </label>
              <button className="mini-btn" type="button" disabled={busy === item.id} onClick={() => save(item)}>
                {busy === item.id ? 'Saving' : 'Save'}
              </button>
            </div>
          ))}
        </div>
      ) : (
        <div className="panel-empty compact">No users found.</div>
      )}
      {status && <small className="agent-llm-status">{status}</small>}
      {error && <small className="agent-llm-status warn">{error}</small>}
    </div>
  )
}

export function LLMCredentialsPanel({
  providers = [],
  onConnect,
  onDisconnect,
  onPreflight,
  initialPreflightResult = null,
  readOnly = false,
}) {
  const providerRows = Array.isArray(providers) && providers.length
    ? providers
    : LLM_CREDENTIAL_PROVIDERS.map((provider) => ({
      provider: provider.value,
      label: provider.label,
      connected: false,
      status: 'disconnected',
      detail: 'Not connected.',
    }))
  const firstProvider = providerRows.find((provider) => provider.connected) || providerRows[0] || {}
  const [draft, setDraft] = useState({
    provider: firstProvider.provider || 'openai',
    api_key: '',
    model: firstProvider.model || '',
    base_url: firstProvider.base_url || '',
    account_label: '',
  })
  const [busy, setBusy] = useState('')
  const [status, setStatus] = useState('')
  const [preflightResult, setPreflightResult] = useState(initialPreflightResult)
  const selectedInfo = providerRows.find((provider) => provider.provider === draft.provider)
  const selectedMeta = LLM_CREDENTIAL_PROVIDERS.find((provider) => provider.value === draft.provider)
  const showBaseUrl = selectedMeta?.needsBaseUrl || draft.provider === 'anthropic'

  useEffect(() => {
    const current = providerRows.find((provider) => provider.provider === draft.provider)
    if (!current) return
    setDraft((prev) => ({
      ...prev,
      model: prev.model || current.model || '',
      base_url: prev.base_url || current.base_url || '',
    }))
  }, [providers?.length, draft.provider])

  async function connectProvider() {
    if (!onConnect || busy) return
    setBusy('connect')
    setStatus('')
    try {
      await onConnect(draft.provider, {
        api_key: draft.api_key,
        model: draft.model || null,
        base_url: draft.base_url || null,
        account_label: draft.account_label || null,
      })
      setDraft((prev) => ({ ...prev, api_key: '' }))
      setStatus('connected')
    } catch (err) {
      setStatus(err.message || 'Connection failed')
    } finally {
      setBusy('')
    }
  }

  async function preflightProvider() {
    if (!onPreflight || busy) return
    setBusy('preflight')
    setStatus('')
    setPreflightResult(null)
    try {
      const result = await onPreflight(draft.provider, {
        api_key: draft.api_key || null,
        model: draft.model || null,
        base_url: draft.base_url || null,
        require_json: true,
      })
      setPreflightResult(result)
    } catch (err) {
      setPreflightResult({
        ready: false,
        detail: err.message || 'Provider preflight failed',
        blockers: [err.message || 'Provider preflight failed'],
      })
    } finally {
      setBusy('')
    }
  }

  async function disconnectProvider() {
    if (!onDisconnect || busy) return
    setBusy('disconnect')
    setStatus('')
    try {
      await onDisconnect(draft.provider)
      setStatus('disconnected')
    } catch (err) {
      setStatus(err.message || 'Disconnect failed')
    } finally {
      setBusy('')
    }
  }

  return (
    <div>
      <p className="section-lab">LLM credentials</p>
      <div className="llm-credentials-panel">
        <div className="llm-provider-list" aria-label="Connected LLM providers">
          {providerRows.map((provider) => (
            <button
              type="button"
              className={[
                provider.provider === draft.provider ? 'selected' : '',
                provider.connected ? 'connected' : 'missing',
              ].filter(Boolean).join(' ')}
              key={provider.provider}
              onClick={() => {
                setPreflightResult(null)
                setStatus('')
                setDraft({
                  provider: provider.provider,
                  api_key: '',
                  model: provider.model || '',
                  base_url: provider.base_url || '',
                  account_label: provider.account_label || '',
                })
              }}
            >
              <span>{provider.label || provider.provider}</span>
              <b>{provider.connected ? 'connected' : 'missing'}</b>
              <small>{provider.connected ? provider.token_preview || provider.model || 'ready' : provider.detail || provider.status}</small>
            </button>
          ))}
        </div>

        {readOnly ? (
          <small className="agent-setup-readonly">LLM credentials are read-only for this role.</small>
        ) : (
          <>
            <div className="llm-credential-form">
              <label>
                <span>Provider</span>
                <select value={draft.provider} onChange={(event) => {
                  const provider = providerRows.find((item) => item.provider === event.target.value)
                  setPreflightResult(null)
                  setStatus('')
                  setDraft({
                    provider: event.target.value,
                    api_key: '',
                    model: provider?.model || '',
                    base_url: provider?.base_url || '',
                    account_label: provider?.account_label || '',
                  })
                }}>
                  {LLM_CREDENTIAL_PROVIDERS.map((provider) => (
                    <option key={provider.value} value={provider.value}>{provider.label}</option>
                  ))}
                </select>
              </label>
              <label>
                <span>API key</span>
                <input
                  autoComplete="off"
                  value={draft.api_key}
                  onChange={(event) => setDraft((prev) => ({ ...prev, api_key: event.target.value }))}
                  placeholder={draft.provider === 'openai_compatible' ? 'optional for local' : 'stored encrypted'}
                  type="password"
                />
              </label>
              <label>
                <span>Model</span>
                <input
                  value={draft.model}
                  onChange={(event) => setDraft((prev) => ({ ...prev, model: event.target.value }))}
                  list={`llm-provider-models-${draft.provider}`}
                  placeholder={agentLLMModelPlaceholder(draft.provider)}
                />
                <datalist id={`llm-provider-models-${draft.provider}`}>
                  {(AGENT_LLM_MODEL_PRESETS[draft.provider] || []).map((model) => (
                    <option key={model} value={model} />
                  ))}
                </datalist>
              </label>
              {showBaseUrl && (
                <label>
                  <span>Base URL</span>
                  <input
                    value={draft.base_url}
                    onChange={(event) => setDraft((prev) => ({ ...prev, base_url: event.target.value }))}
                    placeholder={draft.provider === 'anthropic' ? 'https://api.anthropic.com' : 'http://127.0.0.1:8000/v1'}
                  />
                </label>
              )}
              <label>
                <span>Label</span>
                <input
                  value={draft.account_label}
                  onChange={(event) => setDraft((prev) => ({ ...prev, account_label: event.target.value }))}
                  placeholder={`${selectedMeta?.label || 'Provider'} account`}
                />
              </label>
            </div>

            <div className="llm-credential-footer">
              <small>
                {selectedInfo?.connected
                  ? `${selectedInfo.label || selectedInfo.provider} is connected${selectedInfo.model ? ` · ${selectedInfo.model}` : ''}.`
                  : 'Connect a user key before assigning this provider to an agent role.'}
              </small>
              <div className="agent-llm-actions">
                <button type="button" className="mini-btn" onClick={preflightProvider} disabled={!onPreflight || Boolean(busy)}>
                  {busy === 'preflight' ? 'Testing' : 'Test'}
                </button>
                <button type="button" className="mini-btn" onClick={disconnectProvider} disabled={!selectedInfo?.connected || !onDisconnect || Boolean(busy)}>
                  {busy === 'disconnect' ? 'Disconnecting' : 'Disconnect'}
                </button>
                <button type="button" className="mini-btn primary" onClick={connectProvider} disabled={!onConnect || Boolean(busy)}>
                  {busy === 'connect' ? 'Saving' : 'Connect'}
                </button>
              </div>
            </div>
          </>
        )}
        {status && <small className="agent-llm-status">{status}</small>}
        {preflightResult && (
          <div className={`agent-llm-preflight ${preflightResult.ready ? 'ready' : 'blocked'}`}>
            <div>
              {preflightResult.ready ? <CheckCircle size={13} /> : <WarningCircle size={13} />}
              <span>{preflightResult.ready ? 'Provider ready' : 'Provider blocked'}</span>
              {preflightResult.latency_ms != null && <b>{preflightResult.latency_ms}ms</b>}
            </div>
            <small>{preflightResult.detail}</small>
            {(preflightResult.blockers || []).slice(0, 2).map((blocker) => (
              <small className="warn" key={blocker}>{blocker}</small>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

export function AgentLLMRoutingPanel({ routing, onUpdateRoute, onPreflightRoute, initialPreflightResult = null }) {
  const routes = routing?.routes || []
  const runtime = routing?.runtime || []
  const firstRoute = routes.find((route) => route.provider !== 'default') || routes.find((route) => route.role === 'code') || {}
  const [draft, setDraft] = useState({
    role: firstRoute.role || 'code',
    provider: firstRoute.provider || 'default',
    model: firstRoute.model || '',
    base_url: firstRoute.base_url || '',
  })
  const [status, setStatus] = useState('')
  const [preflightBusy, setPreflightBusy] = useState(false)
  const [preflightResult, setPreflightResult] = useState(initialPreflightResult)

  useEffect(() => {
    const current = routes.find((route) => route.role === draft.role)
    if (!current) return
    setDraft((prev) => ({
      ...prev,
      provider: current.provider || 'default',
      model: current.model || '',
      base_url: current.base_url || '',
    }))
    setPreflightResult(null)
  }, [routing?.room_id, draft.role])

  const selectedRoute = routes.find((route) => route.role === draft.role)
  const readOnly = !onUpdateRoute && !onPreflightRoute

  async function saveRoute() {
    if (!onUpdateRoute) return
    setStatus('saving')
    try {
      await onUpdateRoute({
        role: draft.role,
        provider: draft.provider,
        model: draft.model || null,
        base_url: draft.base_url || null,
      })
      setStatus('saved')
    } catch (err) {
      setStatus(err.message || 'Save failed')
    }
  }

  async function testRoute() {
    if (!onPreflightRoute || preflightBusy) return
    setPreflightBusy(true)
    setStatus('')
    setPreflightResult(null)
    try {
      const result = await onPreflightRoute({
        role: draft.role,
        provider: draft.provider,
        model: draft.model || null,
        base_url: draft.base_url || null,
      })
      setPreflightResult(result)
    } catch (err) {
      setPreflightResult({
        ready: false,
        status: 'error',
        detail: err.message || 'Route preflight failed',
        blockers: [err.message || 'Route preflight failed'],
      })
    } finally {
      setPreflightBusy(false)
    }
  }

  return (
    <div>
      <p className="section-lab">Agent LLM routing</p>
      <div className="agent-llm-panel">
        {readOnly && <small className="agent-setup-readonly">Agent routing is read-only for this role.</small>}
        <div className="agent-llm-grid">
          <label>
            <span>Role</span>
            <select value={draft.role} onChange={(event) => {
              setPreflightResult(null)
              setDraft((prev) => ({ ...prev, role: event.target.value }))
            }} disabled={readOnly}>
              {AGENT_LLM_ROLES.map((role) => <option key={role.value} value={role.value}>{role.label}</option>)}
            </select>
          </label>
          <label>
            <span>Provider</span>
            <select value={draft.provider} onChange={(event) => {
              setPreflightResult(null)
              setDraft((prev) => ({ ...prev, provider: event.target.value }))
            }} disabled={readOnly}>
              {AGENT_LLM_PROVIDERS.map((provider) => <option key={provider.value} value={provider.value}>{provider.label}</option>)}
            </select>
          </label>
        </div>
        <label className="agent-llm-field">
          <span>Model</span>
          <input
            value={draft.model}
            onChange={(event) => {
              setPreflightResult(null)
              setDraft((prev) => ({ ...prev, model: event.target.value }))
            }}
            disabled={readOnly}
            list={`agent-llm-models-${draft.provider}`}
            placeholder={agentLLMModelPlaceholder(draft.provider)}
          />
          <datalist id={`agent-llm-models-${draft.provider}`}>
            {(AGENT_LLM_MODEL_PRESETS[draft.provider] || []).map((model) => (
              <option key={model} value={model} />
            ))}
          </datalist>
        </label>
        <label className="agent-llm-field">
          <span>Base URL</span>
          <input
            value={draft.base_url}
            onChange={(event) => {
              setPreflightResult(null)
              setDraft((prev) => ({ ...prev, base_url: event.target.value }))
            }}
            disabled={readOnly}
            placeholder="http://127.0.0.1:8000/v1"
          />
        </label>
        <div className="agent-llm-footer">
          <small>
            {selectedRoute?.provider && selectedRoute.provider !== 'default'
              ? `${selectedRoute.role} uses ${selectedRoute.provider}${selectedRoute.model ? ` · ${selectedRoute.model}` : ''}`
              : 'Default uses the requester connection or environment fallback.'}
          </small>
          <div className="agent-llm-actions">
            <button type="button" className="mini-btn" onClick={testRoute} disabled={!onPreflightRoute || preflightBusy}>
              {preflightBusy ? 'Testing' : 'Test route'}
            </button>
            <button type="button" className="mini-btn primary" onClick={saveRoute} disabled={!onUpdateRoute || status === 'saving'}>
              {status === 'saving' ? 'Saving' : 'Save'}
            </button>
          </div>
        </div>
        {status && status !== 'saving' && <small className="agent-llm-status">{status}</small>}
        {preflightResult && (
          <div className={`agent-llm-preflight ${preflightResult.ready ? 'ready' : 'blocked'}`}>
            <div>
              {preflightResult.ready ? <CheckCircle size={13} /> : <WarningCircle size={13} />}
              <span>{preflightResult.ready ? 'Preflight passed' : `Preflight: ${preflightResult.status || 'blocked'}`}</span>
              {preflightResult.latency_ms != null && <b>{preflightResult.latency_ms}ms</b>}
            </div>
            <small>{preflightResult.detail}</small>
            {(preflightResult.blockers || []).slice(0, 2).map((blocker) => (
              <small className="warn" key={blocker}>{blocker}</small>
            ))}
          </div>
        )}
        {runtime.length > 0 && (
          <div className="agent-llm-runtime" aria-label="Agent LLM runtime matrix">
            {runtime.slice(0, 7).map((route) => (
              <div className={route.ready ? 'ready' : 'blocked'} key={route.role} title={route.detail}>
                <span>{route.role}</span>
                <b>{route.effective_provider}{route.effective_model ? ` · ${route.effective_model}` : ''}</b>
                <small>{route.ready ? route.credential_source : route.status}</small>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

const VISIBLE_CITATION_LIMIT = 3

export function visibleCitationSummary(citations = [], limit = VISIBLE_CITATION_LIMIT) {
  const rows = uniqueCitations(citations)
  const visible = rows.slice(0, limit)
  return {
    visible,
    hidden: rows.slice(limit),
    total: rows.length,
  }
}

function CitationRow({ citation, index, compact = false }) {
  return (
    <div className={`citation-row${compact ? ' compact' : ''}`} key={citationKey(citation, index)}>
      <div>
        <b>{citationTitle(citation)}</b>
        <small>{citationMeta(citation)}</small>
      </div>
      {citation.excerpt && <p>{citation.excerpt}</p>}
    </div>
  )
}

export function CitationList({ citations }) {
  const { visible, hidden, total } = visibleCitationSummary(citations)
  if (!visible.length) return null
  const totalLabel = hidden.length
    ? `${visible.length}/${total} shown`
    : countLabel(total, 'source')
  const hiddenLabel = countLabel(hidden.length, 'more source')
  return (
    <div className="citation-list" aria-label="RAG sources">
      <div className="citation-list-head">
        <span>Sources</span>
        <small>{totalLabel}</small>
      </div>
      {visible.map((citation, index) => (
        <CitationRow citation={citation} index={index} key={citationKey(citation, index)} />
      ))}
      {hidden.length > 0 && (
        <details className="citation-extra">
          <summary>{hiddenLabel}</summary>
          <div>
            {hidden.map((citation, index) => (
              <CitationRow
                citation={citation}
                compact
                index={visible.length + index}
                key={citationKey(citation, visible.length + index)}
              />
            ))}
          </div>
        </details>
      )}
    </div>
  )
}

function MemoryAnswerSourceSummary({ answer, displayMemory }) {
  const summary = memoryAnswerSourceSummary(answer, displayMemory)
  if (!summary.length) return null
  return (
    <div className="memory-source-summary" aria-label="Answer source coverage">
      {summary.map((item) => <span key={item}>{item}</span>)}
    </div>
  )
}

function MemoryControls({ filter, setFilter, setAnswer, rebuildIndex, indexBusy, indexStatus }) {
  return (
    <div className="memory-controls" aria-label={`Memory options: ${filter ? memoryLabel(filter) : 'All types'}`}>
      <div className="memory-filters" aria-label="Memory filters">
        {MEMORY_FILTERS.map((item) => (
          <button
            key={item.value || 'all'}
            className={filter === item.value ? 'on' : ''}
            type="button"
            onClick={() => {
              setFilter(item.value)
              setAnswer(null)
            }}
          >
            {item.label}
          </button>
        ))}
        <button
          className="rag-index-button"
          type="button"
          onClick={rebuildIndex}
          disabled={indexBusy}
          aria-label="Rebuild RAG index"
        >
          {indexBusy ? 'Indexing' : 'Reindex'}
        </button>
      </div>
      {indexStatus && (
        <div className="rag-index-status" aria-label="RAG index status">
          <span>{indexStatus.provider || 'local index'}</span>
          <b>{Number(indexStatus.document_count || 0)} docs</b>
          {typeof indexStatus.short_memory_hits === 'number' && <small>{indexStatus.short_memory_hits} short</small>}
          {typeof indexStatus.long_memory_hits === 'number' && <small>{indexStatus.long_memory_hits} long</small>}
          {typeof indexStatus.ontology_hits === 'number' && <small>{indexStatus.ontology_hits} ontology</small>}
          {typeof indexStatus.candidate_count === 'number' && <small>{indexStatus.candidate_count} candidates</small>}
        </div>
      )}
    </div>
  )
}

function MemoryPanel({ memory, agentName, roomId = 'main' }) {
  const [filter, setFilter] = useState('')
  const [question, setQuestion] = useState('')
  const [answer, setAnswer] = useState(null)
  const [health, setHealth] = useState(null)
  const [loadedMemory, setLoadedMemory] = useState([])
  const [memoryLoading, setMemoryLoading] = useState(false)
  const [memoryLoadError, setMemoryLoadError] = useState('')
  const [indexStatus, setIndexStatus] = useState(null)
  const [busy, setBusy] = useState(false)
  const [indexBusy, setIndexBusy] = useState(false)
  const [error, setError] = useState('')
  const localMemory = Array.isArray(memory) ? memory : []
  const hasLocalMemory = localMemory.length > 0
  const hasIndexedMemory = Number(health?.total_items || 0) > 0
  const memorySource = hasLocalMemory ? localMemory : loadedMemory
  const openMemoryCount = Number(health?.status_counts?.open || 0)
  const preferOpenMemory = !filter && !answer && openMemoryCount > 0
  const visibleMemory = visibleMemoryItems(memorySource, filter, preferOpenMemory)
  const displayMemory = answer?.type === 'memory' ? answer.items || [] : visibleMemory
  const showMemoryEmptyState = shouldShowMemoryEmptyState({ answer, displayMemory })
  const roomMemoryLoading = !Array.isArray(memory) && !answer && !health
  const memoryDetails = memoryDetailsTitle({ preferOpen: preferOpenMemory, displayCount: displayMemory.length })
  const activeRoomId = roomId || 'main'

  async function refreshHealth() {
    try {
      setHealth(await fetchRoomMemoryHealth(activeRoomId))
    } catch {
      setHealth(null)
    }
  }

  useEffect(() => {
    refreshHealth()
  }, [activeRoomId, memory?.length])

  useEffect(() => {
    let alive = true
    if (!hasIndexedMemory || hasLocalMemory || answer?.type === 'memory') {
      if (hasLocalMemory) setLoadedMemory([])
      setMemoryLoading(false)
      return () => {
        alive = false
      }
    }
    setMemoryLoading(true)
    setMemoryLoadError('')
    fetchRoomMemory(activeRoomId, { kind: filter || undefined, status: preferOpenMemory ? 'open' : undefined, limit: 6 })
      .then((items) => {
        if (alive) setLoadedMemory(Array.isArray(items) ? items : [])
      })
      .catch((err) => {
        if (alive) {
          setLoadedMemory([])
          setMemoryLoadError(err.message || 'Memory list unavailable')
        }
      })
      .finally(() => {
        if (alive) setMemoryLoading(false)
      })
    return () => {
      alive = false
    }
  }, [activeRoomId, answer?.type, filter, hasIndexedMemory, hasLocalMemory, preferOpenMemory])

  async function submit(e) {
    e.preventDefault()
    const text = question.trim()
    if (!text || busy) return
    setBusy(true)
    setError('')
    try {
      const route = await routeRoomCommand(activeRoomId, text, 'broad')
      const type = route.route === 'audit_query'
        ? 'audit'
        : route.route === 'rag_query'
          ? 'rag'
          : 'memory'
      const result = type === 'audit'
        ? await queryRoomAudit(activeRoomId, text)
        : type === 'rag'
          ? await queryRoomProvenance(activeRoomId, text)
          : await queryRoomMemory(activeRoomId, text)
      setAnswer({ ...result, type, route })
      if (type === 'rag' && result.retrieval) {
        setIndexStatus({
          provider: result.retrieval.provider,
          document_count: result.retrieval.indexed_documents,
          candidate_count: result.retrieval.candidate_count,
          short_memory_hits: result.retrieval.short_memory_hits,
          long_memory_hits: result.retrieval.long_memory_hits,
          ontology_hits: result.retrieval.ontology_hits,
        })
      }
      await refreshHealth()
    } catch (err) {
      setError(err.message || 'Meeting query failed')
    } finally {
      setBusy(false)
    }
  }

  async function rebuildIndex() {
    if (indexBusy) return
    setIndexBusy(true)
    setError('')
    try {
      const result = await rebuildRoomRagIndex(activeRoomId)
      setIndexStatus({
        provider: result.provider,
        document_count: result.document_count,
        sources: result.sources,
      })
      await refreshHealth()
    } catch (err) {
      setError(err.message || 'RAG index failed')
    } finally {
      setIndexBusy(false)
    }
  }

  return (
    <div className="memory-panel">
      <p className="section-lab">Meeting memory</p>
      <form className="memory-query" onSubmit={submit}>
        <MagnifyingGlass size={14} />
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="Search or ask memory"
          aria-label="Search meeting memory and audit"
        />
        <button
          className="memory-query-submit"
          type="submit"
          aria-label="Search meeting memory"
          title="Search meeting memory"
          disabled={busy || !question.trim()}
        >
          {busy ? '...' : <ArrowUpRight size={13} />}
        </button>
      </form>
      <MemoryMobileHint
        health={health}
        hasIndexedMemory={hasIndexedMemory}
        loading={memoryLoading || roomMemoryLoading}
      />
      {health && <MemoryHealthStrip health={health} />}
      {(error || memoryLoadError) && <div className="memory-error" role="alert">{error || memoryLoadError}</div>}
      {answer && (
        <div className={`memory-answer ${answer.type === 'audit' ? 'audit-answer' : ''} ${answer.type === 'rag' ? 'rag-answer' : ''}`}>
          <b>{agentName} {answer.type === 'audit' ? 'audit' : answer.type === 'rag' ? 'RAG' : 'memory'}</b>
          {memoryRouteLabel(answer.route) && (
            <small className="memory-route">
              {memoryRouteLabel(answer.route)}
            </small>
          )}
          <MemoryAnswerSourceSummary answer={answer} displayMemory={displayMemory} />
          <p title={answer.answer}>{memoryAnswerDisplayText(answer)}</p>
          {ragTraceItems(answer.retrieval).length > 0 && (
            <div className="rag-trace" aria-label="RAG retrieval trace">
              {ragTraceItems(answer.retrieval).map((item) => <span key={item}>{item}</span>)}
            </div>
          )}
          <CitationList citations={answer.citations} />
        </div>
      )}
      {answer?.type === 'audit' && answer.items?.length ? (
        <AuditFeed events={answer.items} />
      ) : answer && displayMemory.length ? (
        <div className="memory-list">
          {displayMemory.map((item) => <MemoryItemRow item={item} agentName={agentName} key={item.id} />)}
        </div>
      ) : displayMemory.length ? (
        <details className="memory-details">
          <summary>
            <span>{memoryDetails.title}</span>
            <small>{memoryDetails.meta}</small>
          </summary>
          <MemoryControls
            filter={filter}
            setFilter={setFilter}
            setAnswer={setAnswer}
            rebuildIndex={rebuildIndex}
            indexBusy={indexBusy}
            indexStatus={indexStatus}
          />
          <div className="memory-list">
            {displayMemory.map((item) => <MemoryItemRow item={item} agentName={agentName} key={item.id} />)}
          </div>
        </details>
      ) : showMemoryEmptyState ? (
        <div className="panel-empty">
          <ClipboardText size={28} color="var(--ink-ghost)" />
          {memoryEmptyText({ answer, hasIndexedMemory, loading: memoryLoading || roomMemoryLoading })}
        </div>
      ) : null}
    </div>
  )
}

function AuditFeed({ events }) {
  const visibleEvents = (events || []).slice(0, 6)
  if (!visibleEvents.length) {
    return (
      <div className="panel-empty audit-empty">
        <ClipboardText size={28} color="var(--ink-ghost)" />
        Speaker corrections, approvals, git results, and demo gates will appear here.
      </div>
    )
  }
  return (
    <div className="audit-feed">
      {visibleEvents.map((event) => {
        const Icon = event.kind === 'speaker' ? Waveform : event.kind === 'action' ? GitDiff : ClipboardText
        const actor = event.actor || event.actor_name
        return (
          <div className={`audit-event ${event.tone}`} key={event.id}>
            <span className="audit-icon">
              <Icon size={13} />
            </span>
            <div className="audit-main">
              <div className="audit-head">
                <b>{event.title}</b>
                <small>{event.status}</small>
              </div>
              <p>{event.detail}</p>
              <div className="audit-meta">
                {actor && <span>{actor}</span>}
                {(event.chips || []).slice(0, 3).map((chip) => (
                  <span key={chip}>{chip}</span>
                ))}
              </div>
            </div>
          </div>
        )
      })}
    </div>
  )
}

function WorkspacePanel({
  roomId = 'main',
  workspace,
  gitRefreshKey,
  demoGateRefreshKey,
  demoGateResult,
  canViewDeployment = false,
  canInspectCode = false,
  canManageRuntime = false,
}) {
  const [tree, setTree] = useState(null)
  const [gitStatus, setGitStatus] = useState(null)
  const [gitDiff, setGitDiff] = useState(null)
  const [cacheStatus, setCacheStatus] = useState(null)
  const [runtimeStatus, setRuntimeStatus] = useState(null)
  const [localDoctor, setLocalDoctor] = useState(null)
  const [speakerWarmup, setSpeakerWarmup] = useState(null)
  const [speakerVerification, setSpeakerVerification] = useState(null)
  const [readiness, setReadiness] = useState(null)
  const [targetReadiness, setTargetReadiness] = useState(null)
  const [operatorAcceptance, setOperatorAcceptance] = useState(null)
  const [eventStoreReadiness, setEventStoreReadiness] = useState(null)
  const [deploymentHardening, setDeploymentHardening] = useState(null)
  const [productionCutover, setProductionCutover] = useState(null)
  const [demoGateRun, setDemoGateRun] = useState(null)
  const [question, setQuestion] = useState('')
  const [answer, setAnswer] = useState(null)
  const [busy, setBusy] = useState(false)
  const [cacheBusy, setCacheBusy] = useState(false)
  const [warmupBusy, setWarmupBusy] = useState(false)
  const [verificationBusy, setVerificationBusy] = useState(false)
  const [demoGateBusy, setDemoGateBusy] = useState(false)
  const [error, setError] = useState('')

  function applyDiagnosticResult(promise, setter) {
    // ponytail: diagnostic reads are best-effort; add cancellation only if this panel starts unmounting frequently.
    promise.then(setter).catch(() => {})
  }

  async function refreshTargetReadiness() {
    try {
      const [targetResult, eventStoreResult, deploymentResult, cutoverResult] = await Promise.allSettled([
        fetchTargetReadiness(),
        fetchEventStoreReadiness(),
        canViewDeployment ? fetchDeploymentHardening() : Promise.resolve(null),
        canViewDeployment ? fetchProductionCutover() : Promise.resolve(null),
      ])
      if (targetResult.status === 'fulfilled') setTargetReadiness(targetResult.value)
      if (eventStoreResult.status === 'fulfilled') setEventStoreReadiness(eventStoreResult.value)
      if (deploymentResult.status === 'fulfilled' && deploymentResult.value) setDeploymentHardening(deploymentResult.value)
      if (cutoverResult.status === 'fulfilled' && cutoverResult.value) setProductionCutover(cutoverResult.value)
    } catch {
      // Target readiness is diagnostic; do not fail the primary action if it cannot refresh.
    }
  }

  async function refreshGitAndCache() {
    const [statusResult, diffResult, cacheResult] = await Promise.allSettled([
      canInspectCode ? fetchGitStatus(roomId) : Promise.resolve(null),
      canInspectCode ? fetchGitDiff(roomId) : Promise.resolve(null),
      fetchCacheStatus(),
    ])
    if (statusResult.status === 'fulfilled') setGitStatus(statusResult.value)
    if (diffResult.status === 'fulfilled') setGitDiff(diffResult.value)
    if (cacheResult.status === 'fulfilled') setCacheStatus(cacheResult.value)
  }

  useEffect(() => {
    applyDiagnosticResult(fetchRuntimeStatus(), setRuntimeStatus)
    applyDiagnosticResult(fetchLocalDoctor(), setLocalDoctor)
    applyDiagnosticResult(fetchSpeakerWarmup(), setSpeakerWarmup)
    applyDiagnosticResult(fetchSpeakerVerification(), setSpeakerVerification)
    applyDiagnosticResult(fetchSystemReadiness(), setReadiness)
    applyDiagnosticResult(fetchTargetReadiness(), setTargetReadiness)
    applyDiagnosticResult(fetchOperatorAcceptance(), setOperatorAcceptance)
    applyDiagnosticResult(fetchEventStoreReadiness(), setEventStoreReadiness)
    if (canViewDeployment) {
      applyDiagnosticResult(fetchDeploymentHardening(), setDeploymentHardening)
      applyDiagnosticResult(fetchProductionCutover(), setProductionCutover)
    }
    applyDiagnosticResult(fetchDemoGateRun(), setDemoGateRun)
  }, [gitRefreshKey, demoGateRefreshKey, workspace?.connected, workspace?.name, canViewDeployment])

  useEffect(() => {
    if (demoGateRun?.state !== 'running') return undefined
    let alive = true
    const timer = window.setInterval(async () => {
      try {
        const result = await fetchDemoGateRun()
        if (!alive) return
        setDemoGateRun(result)
        if (result.evidence) {
          setReadiness((prev) => prev ? { ...prev, demo_evidence: result.evidence } : prev)
        }
        if (['succeeded', 'failed'].includes(result.state)) {
          const [refreshed, target, acceptance, eventStore, cutover] = await Promise.allSettled([
            fetchSystemReadiness(),
            fetchTargetReadiness(),
            fetchOperatorAcceptance(),
            fetchEventStoreReadiness(),
            canViewDeployment ? fetchProductionCutover() : Promise.resolve(null),
          ])
          if (alive) {
            if (refreshed.status === 'fulfilled') setReadiness(refreshed.value)
            if (target.status === 'fulfilled') setTargetReadiness(target.value)
            if (acceptance.status === 'fulfilled') setOperatorAcceptance(acceptance.value)
            if (eventStore.status === 'fulfilled') setEventStoreReadiness(eventStore.value)
            if (cutover.status === 'fulfilled' && cutover.value) setProductionCutover(cutover.value)
          }
        }
      } catch {
        // Keep the last known gate run state visible if polling fails.
      }
    }, 1800)
    return () => {
      alive = false
      window.clearInterval(timer)
    }
  }, [demoGateRun?.state, demoGateRun?.job_id, canViewDeployment])

  useEffect(() => {
    if (speakerWarmup?.state !== 'running') return undefined
    let alive = true
    const timer = window.setInterval(async () => {
      try {
        const result = await fetchSpeakerWarmup()
        if (alive) setSpeakerWarmup(result)
      } catch {
        // Keep the last known state visible if polling fails.
      }
    }, 1800)
    return () => {
      alive = false
      window.clearInterval(timer)
    }
  }, [speakerWarmup?.state, speakerWarmup?.job_id])

  useEffect(() => {
    if (speakerVerification?.state !== 'running') return undefined
    let alive = true
    const timer = window.setInterval(async () => {
      try {
        const result = await fetchSpeakerVerification()
        if (alive) {
          setSpeakerVerification(result)
          if (result.verification) {
            setReadiness((prev) => prev ? { ...prev, speaker_verification: result.verification } : prev)
          }
          if (['succeeded', 'failed'].includes(result.state)) {
            await refreshTargetReadiness()
          }
        }
      } catch {
        // Keep the last known state visible if polling fails.
      }
    }, 1800)
    return () => {
      alive = false
      window.clearInterval(timer)
    }
  }, [speakerVerification?.state, speakerVerification?.job_id])

  useEffect(() => {
    let alive = true
    if (!workspace?.connected || !canInspectCode) {
      setTree(null)
      return undefined
    }
    fetchWorkspaceTree(60, roomId)
      .then((result) => {
        if (alive) {
          setTree(result)
          setError('')
        }
      })
      .catch((err) => {
        if (alive) setError(err.message || 'Workspace context unavailable')
      })
    return () => {
      alive = false
    }
  }, [workspace?.connected, workspace?.name, roomId, canInspectCode])

  useEffect(() => {
    let alive = true
    if (!workspace?.connected || !canInspectCode) {
      setGitStatus(null)
      setGitDiff(null)
      setCacheStatus(null)
      return undefined
    }
    Promise.allSettled([fetchGitStatus(roomId), canInspectCode ? fetchGitDiff(roomId) : Promise.resolve(null), fetchCacheStatus()])
      .then(([statusResult, diffResult, cacheResult]) => {
        if (!alive) return
        if (statusResult.status === 'fulfilled') setGitStatus(statusResult.value)
        if (diffResult.status === 'fulfilled') setGitDiff(diffResult.value)
        if (cacheResult.status === 'fulfilled') setCacheStatus(cacheResult.value)
      })
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [workspace?.connected, workspace?.name, roomId, gitRefreshKey, canInspectCode])

  async function clearRuntimeCache() {
    if (cacheBusy) return
    setCacheBusy(true)
    setError('')
    try {
      const result = await clearCache()
      setCacheStatus(result)
      await refreshGitAndCache()
    } catch (err) {
      setError(err.message || 'Cache clear failed')
    } finally {
      setCacheBusy(false)
    }
  }

  async function warmSpeakerProvider() {
    if (warmupBusy) return
    setWarmupBusy(true)
    setError('')
    try {
      const started = await startSpeakerWarmup()
      setSpeakerWarmup(started)
      if (started.state === 'running') {
        window.setTimeout(async () => {
          try {
            setSpeakerWarmup(await fetchSpeakerWarmup())
          } catch {
            // Runtime status can be refreshed manually by reopening the console.
          }
        }, 1200)
      }
    } catch (err) {
      setError(err.message || 'Speaker warmup failed')
    } finally {
      setWarmupBusy(false)
    }
  }

  async function verifySpeakerAudio(file, requireMultipleSpeakers) {
    if (verificationBusy || !file) return
    setVerificationBusy(true)
    setError('')
    try {
      const started = await startSpeakerVerification(file, { requireMultipleSpeakers })
      setSpeakerVerification(started)
      if (started.verification) {
        setReadiness((prev) => prev ? { ...prev, speaker_verification: started.verification } : prev)
      }
      await refreshTargetReadiness()
    } catch (err) {
      setError(err.message || 'Speaker verification failed')
    } finally {
      setVerificationBusy(false)
    }
  }

  async function verifyGeneratedSpeakerAudio(requireMultipleSpeakers = true) {
    if (verificationBusy) return
    setVerificationBusy(true)
    setError('')
    try {
      const started = await startGeneratedSpeakerVerification({ requireMultipleSpeakers })
      setSpeakerVerification(started)
      if (started.verification) {
        setReadiness((prev) => prev ? { ...prev, speaker_verification: started.verification } : prev)
      }
      await refreshTargetReadiness()
    } catch (err) {
      setError(err.message || 'Generated speaker verification failed')
    } finally {
      setVerificationBusy(false)
    }
  }

  async function runDemoGate(gateId) {
    if (demoGateBusy) return
    setDemoGateBusy(true)
    setError('')
    try {
      const started = await startDemoGateRun(gateId)
      setDemoGateRun(started)
      await refreshTargetReadiness()
    } catch (err) {
      setError(err.message || 'Demo gate failed to start')
    } finally {
      setDemoGateBusy(false)
    }
  }

  async function submit(e) {
    e.preventDefault()
    const text = question.trim()
    if (!canInspectCode || !text || busy) return
    setBusy(true)
    setError('')
    try {
      const result = await queryRoomCode('main', text)
      setAnswer(result)
    } catch (err) {
      setError(err.message || 'Code query failed')
    } finally {
      setBusy(false)
    }
  }

  const files = tree?.files || []
  const previewFiles = files.slice(0, 5)
  const hasOperatorDiagnostics = Boolean(operatorAcceptance || eventStoreReadiness || readiness || runtimeStatus)

  return (
    <div>
      <p className="section-lab">Workspace context</p>
      <TargetReadinessPanel target={targetReadiness} />
      <DeploymentHardeningPanel hardening={deploymentHardening} cutover={productionCutover} />
      {hasOperatorDiagnostics && (
        <DiagnosticDetails
          title="Operator diagnostics"
          meta={operatorDiagnosticsMeta({
            operatorAcceptance,
            eventStoreReadiness,
            systemReadiness: readiness,
            runtimeStatus,
          })}
        >
          <OperatorAcceptancePanel report={operatorAcceptance} />
          <EventStoreReadinessPanel eventStore={eventStoreReadiness} />
          <SystemReadinessPanel
            readiness={readiness}
            demoGateRun={demoGateRun}
            demoGateResult={demoGateResult}
            demoGateBusy={demoGateBusy}
            onRunDemoGate={runDemoGate}
            canManageRuntime={canManageRuntime}
          />
          <RuntimeStatusPanel
            status={runtimeStatus}
            localDoctor={localDoctor}
            speakerWarmup={speakerWarmup}
            speakerVerification={speakerVerification}
            warmupBusy={warmupBusy}
            verificationBusy={verificationBusy}
            onWarmup={warmSpeakerProvider}
            onVerify={verifySpeakerAudio}
            onVerifyGenerated={verifyGeneratedSpeakerAudio}
            canManageRuntime={canManageRuntime}
          />
        </DiagnosticDetails>
      )}
      {workspace?.connected ? (
        <>
          <div className="workspace-summary">
            <span><Files size={14} />{canInspectCode ? (tree ? `${tree.total_files} files indexed` : 'Indexing workspace') : 'Code context restricted'}</span>
            {tree?.truncated && <small>truncated</small>}
          </div>
          <GitStatusPanel status={gitStatus} diff={gitDiff} />
          <CacheStatusPanel
            status={cacheStatus}
            busy={cacheBusy}
            onClear={clearRuntimeCache}
            canManageRuntime={canManageRuntime}
          />
          {canInspectCode ? (
            <form className="memory-query workspace-query" onSubmit={submit}>
              <Code size={14} />
              <input
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                placeholder="Ask about files, functions, routes"
                aria-label="Ask workspace code"
              />
              <button type="submit" aria-label="Submit workspace code question" disabled={busy || !question.trim()}>
                {busy ? '...' : 'Ask'}
              </button>
            </form>
          ) : (
            <small className="agent-setup-readonly">Code search and diff preview require voice workspace permission.</small>
          )}
          {error && <div className="memory-error" role="alert">{error}</div>}
          {answer && (
            <div className="memory-answer workspace-answer">
              <b>Code answer</b>
              <p>{answer.answer}</p>
            </div>
          )}
          {answer?.references?.length ? (
            <div className="workspace-refs">
              {answer.references.slice(0, 5).map((ref) => (
                <div className="workspace-ref" key={`${ref.path}:${ref.line}`}>
                  <span className="mono">{ref.path}:{ref.line}</span>
                  <p>{ref.snippet}</p>
                </div>
              ))}
            </div>
          ) : canInspectCode ? (
            <div className="workspace-files">
              {previewFiles.map((file) => (
                <span className="workspace-file" key={file.path}>{file.path}</span>
              ))}
            </div>
          ) : null}
        </>
      ) : (
        <div className="panel-empty">
          <Files size={28} color="var(--ink-ghost)" />
          Connect VOICEOPS_WORKSPACE to ask code questions.
        </div>
      )}
    </div>
  )
}

function supportLabel(value) {
  return value ? 'ready' : 'missing'
}

export function liveDiagnosticChecks(diagnostics = {}) {
  const setupItems = liveSetupDiagnosticItems(diagnostics)
  const blocked = (ids) => setupItems.some((item) => ids.includes(item.id) && !item.ready)
  return [
    { label: 'Page', ready: diagnostics.secureContext !== false && !blocked(['page']) },
    {
      label: 'Mic',
      ready: Boolean(diagnostics.getUserMedia) && !blocked(['browser-api', 'browser-permission', 'mac-input', 'audio-session']),
    },
    { label: 'Recorder', ready: Boolean(diagnostics.mediaRecorder) && !blocked(['recorder']) },
    { label: 'Captions', ready: Boolean(diagnostics.instantCaptions) },
  ]
}

export function diagnosticsErrorLabel(kind) {
  const value = String(kind || '').toLowerCase()
  if (!value) return 'Runtime error'
  if (value.includes('api-runtime')) return 'API error'
  if (value.includes('socket') || value.includes('websocket')) return 'Socket error'
  if (value.includes('notsupported') || value.includes('not-supported')) return 'Audio session error'
  if (value.includes('media-recorder')) return 'Recorder error'
  if (
    value.includes('microphone')
    || value.includes('get-user-media')
    || value.includes('notallowed')
    || value.includes('notfound')
    || value.includes('notreadable')
    || value.includes('overconstrained')
    || value.includes('audio-capture')
  ) {
    return 'Mic error'
  }
  return 'Runtime error'
}

export function diagnosticsEventLabel(event) {
  const value = String(event || '')
  const lowered = value.toLowerCase()
  if (!value || lowered === 'idle') return 'idle'
  if (lowered.includes('notallowed') || lowered.includes('permission')) return 'mic permission'
  if (lowered.includes('notfound')) return 'mic missing'
  if (lowered.includes('notreadable')) return 'mic busy'
  if (lowered.includes('overconstrained')) return 'mic input'
  if (lowered.includes('notsupported') || lowered.includes('not-supported')) return 'audio session'
  if (lowered.includes('media-recorder')) return 'recorder'
  if (lowered.includes('get-user-media')) return 'mic unavailable'
  if (lowered.includes('socket') || lowered.includes('websocket')) return 'socket'
  if (lowered.includes('api-runtime')) return 'API unavailable'
  return value.replace(/^microphone[_-]?/i, '').replace(/[_-]+/g, ' ')
}

function diagnosticsMetaLabel(kind) {
  const label = diagnosticsErrorLabel(kind)
  if (label === 'Mic error') return 'mic issue'
  if (label === 'Recorder error') return 'recorder issue'
  if (label === 'Audio session error') return 'audio session issue'
  if (label === 'Socket error') return 'socket issue'
  if (label === 'API error') return 'api issue'
  if (kind) return 'runtime issue'
  return ''
}

function diagnosticsFallbackMessage(kind) {
  return diagnosticsErrorLabel(kind) === 'Mic error'
    ? 'Microphone capture is unavailable.'
    : 'Runtime diagnostics need attention.'
}

function diagnosticKindIncludes(kind, fragments) {
  const value = String(kind || '').toLowerCase()
  return fragments.some((fragment) => value.includes(fragment))
}

export function liveSetupDiagnosticItems(diagnostics = {}) {
  const kind = String(diagnostics?.lastErrorKind || '')
  const message = String(diagnostics?.lastErrorMessage || '')
  const combined = `${kind} ${message}`.toLowerCase()
  const pageBlocked = diagnostics?.secureContext === false || diagnosticKindIncludes(kind, ['security'])
  const browserApiBlocked = diagnostics?.getUserMedia === false || diagnosticKindIncludes(kind, ['get-user-media'])
  const permissionBlocked = diagnostics?.microphonePermission === 'denied'
    || /notallowed|not-allowed|permission|denied/.test(combined)
  const deviceBlocked = /notfound|not found|notreadable|busy|in use|audio-capture|no microphone|overconstrained|constraint/.test(combined)
  const audioSessionBlocked = diagnosticKindIncludes(kind, ['notsupported', 'not-supported'])
  const recorderBlocked = diagnostics?.mediaRecorder === false || diagnosticKindIncludes(kind, ['media-recorder'])
  const socketBlocked = diagnosticKindIncludes(kind, ['socket', 'websocket'])
  const shouldShow = Boolean(pageBlocked || browserApiBlocked || permissionBlocked || deviceBlocked || audioSessionBlocked || recorderBlocked || socketBlocked)

  if (!shouldShow) return []

  return [
    {
      id: 'page',
      label: 'Page',
      ready: !pageBlocked,
      detail: pageBlocked ? 'Open this app on 127.0.0.1 or localhost.' : 'Local page is allowed.',
    },
    browserApiBlocked ? {
      id: 'browser-api',
      label: 'Browser mic API',
      ready: false,
      detail: 'Open this local console in Chrome or Safari for voice capture.',
    } : null,
    {
      id: 'browser-permission',
      label: 'Browser permission',
      ready: !permissionBlocked,
      detail: permissionBlocked ? 'Allow microphone in browser site settings.' : 'Browser can request microphone.',
    },
    {
      id: 'mac-input',
      label: 'Mac input',
      ready: !deviceBlocked,
      detail: deviceBlocked ? 'Select an input or close apps using the mic.' : 'No input device block detected.',
    },
    audioSessionBlocked ? {
      id: 'audio-session',
      label: 'Audio session',
      ready: false,
      detail: 'This browser session could not start live audio.',
    } : null,
    {
      id: 'recorder',
      label: 'Recorder',
      ready: !recorderBlocked,
      detail: recorderBlocked ? 'Use a browser with MediaRecorder support.' : 'Recorder API is available.',
    },
    {
      id: 'live-socket',
      label: 'Live socket',
      ready: !socketBlocked,
      detail: socketBlocked ? 'Check backend live meeting websocket.' : 'No websocket interruption detected.',
    },
  ].filter(Boolean)
}

function formatDuration(ms) {
  if (ms == null) return 'pending'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)}s`
}

function recentGateStages(run) {
  return Array.isArray(run?.stages) ? run.stages.slice(-3) : []
}

export function OperatorAcceptancePanel({ report }) {
  if (!report) return null
  const stages = report.stages || []
  const required = stages.filter((stage) => stage.required)
  const readyRequired = required.filter((stage) => stage.ready)
  const firstIssue = stages.find((stage) => stage.required && !stage.ready) || stages.find((stage) => !stage.ready)
  const nextStep = report.next_steps?.[0] || firstIssue?.next_steps?.[0]
  const statusLabel = report.accepted ? 'accepted' : 'needs attention'
  const denominator = required.length || stages.length || 0
  return (
    <div className={`operator-acceptance ${report.accepted ? 'ready' : 'attention'}`}>
      <div className="git-status-line">
        <span>
          {report.accepted ? <CheckCircle size={14} /> : <WarningCircle size={14} />}
          Operator acceptance
        </span>
        <small>{readyRequired.length}/{denominator}</small>
      </div>
      <small className="runtime-detail">
        {statusLabel} · {report.run_harnesses ? 'harnesses included' : 'recorded evidence'} · {formatDuration(report.duration_ms)}
      </small>
      <div className="operator-acceptance-stages">
        {stages.slice(0, 4).map((stage) => (
          <div className={stage.ready ? 'ready' : 'blocked'} key={stage.id} title={stage.summary}>
            <span>{stage.ready ? <Check size={11} /> : <X size={11} />}{stage.label}</span>
            <b>{stage.status}</b>
          </div>
        ))}
      </div>
      {nextStep && <small className="runtime-detail warn">{nextStep}</small>}
      <div className="readiness-command mono operator-command">
        cd backend && python scripts/operator_acceptance.py --json --require-accepted
      </div>
    </div>
  )
}

export function EventStoreReadinessPanel({ eventStore }) {
  if (!eventStore) return null
  const checks = eventStore.checks || []
  const failed = checks.find((check) => !check.ready)
  const stateCounts = eventStore.state_counts || {}
  const cutoverEnv = Object.entries(eventStore.cutover_env || {})
  const visibleCounts = Object.entries(stateCounts)
    .filter(([, count]) => count > 0)
    .slice(0, 4)
  const statusLabel = eventStore.ready ? 'ready' : eventStore.status || 'needs attention'
  const primaryCommand = eventStore.bootstrap_command || eventStore.init_command || eventStore.migration_command
  const migrationCommand = eventStore.migration_command && eventStore.migration_command !== primaryCommand
    ? eventStore.migration_command
    : null
  return (
    <div className={`event-store-readiness ${eventStore.ready ? 'ready' : 'attention'}`}>
      <div className="git-status-line">
        <span>
          {eventStore.ready ? <CheckCircle size={14} /> : <WarningCircle size={14} />}
          Event store
        </span>
        <small>{statusLabel}</small>
      </div>
      <div className="event-store-metrics">
        <div>
          <span>Backend</span>
          <b>{eventStore.backend}</b>
        </div>
        <div>
          <span>Events</span>
          <b>{eventStore.event_count || 0}</b>
        </div>
        <div>
          <span>Streams</span>
          <b>{eventStore.stream_count || 0}</b>
        </div>
      </div>
      <small className="runtime-detail">
        {eventStore.runtime_mode || eventStore.backend} · production {eventStore.production_ready ? 'ready' : 'requires SQLite'}
      </small>
      {visibleCounts.length > 0 && (
        <div className="event-store-counts">
          {visibleCounts.map(([name, count]) => (
            <span key={name}>{name}: {count}</span>
          ))}
        </div>
      )}
      {failed && (
        <small className="runtime-detail warn">{failed.label}: {failed.status}</small>
      )}
      {!failed && eventStore.warnings?.[0] && (
        <small className="runtime-detail warn">{eventStore.warnings[0]}</small>
      )}
      {!eventStore.ready && cutoverEnv.length > 0 && (
        <div className="event-store-cutover">
          <div>
            <b>Production cutover</b>
            <small>Set durable collaboration storage before team use.</small>
          </div>
          {cutoverEnv.map(([key, value]) => (
            <code key={key}>{key}={value}</code>
          ))}
        </div>
      )}
      {primaryCommand && !eventStore.ready && (
        <div className="readiness-command mono event-store-command">{primaryCommand}</div>
      )}
      {migrationCommand && !eventStore.ready && (
        <div className="readiness-command mono event-store-command secondary">{migrationCommand}</div>
      )}
    </div>
  )
}

export function operatorDiagnosticsMeta({
  operatorAcceptance = null,
  eventStoreReadiness = null,
  systemReadiness = null,
  runtimeStatus = null,
} = {}) {
  const known = [operatorAcceptance, eventStoreReadiness, systemReadiness, runtimeStatus].filter(Boolean).length
  if (!known) return 'checking'
  const blockers = [
    operatorAcceptance && operatorAcceptance.accepted === false,
    eventStoreReadiness && eventStoreReadiness.ready === false,
    systemReadiness && systemReadiness.ready === false,
    runtimeStatus && (runtimeStatus.warnings || []).length > 0,
  ].filter(Boolean).length
  if (!blockers) return 'ready'
  return `${blockers} blocker${blockers === 1 ? '' : 's'}`
}

export function cutoverActionSteps(cutover) {
  const explicit = cutover?.next_steps || []
  const fromChecks = (cutover?.checks || [])
    .filter((check) => !check.ready && check.next_step)
    .map((check) => check.next_step)
  return Array.from(new Set([...explicit, ...fromChecks].filter(Boolean))).slice(0, 3)
}

export function DeploymentHardeningPanel({ hardening, cutover = null }) {
  if (!hardening) return null
  const checks = hardening.checks || []
  const blocked = checks.filter((check) => !check.ready)
  const startupGate = checks.find((check) => check.id === 'startup_security_gate')
  const visibleChecks = [
    ...blocked,
    ...checks.filter((check) => check.ready),
  ].filter((check) => check.id !== 'startup_security_gate').slice(0, 6)
  const nextStep = hardening.next_steps?.[0] || blocked[0]?.action
  const command = hardening.commands?.[0] || blocked.find((check) => check.command)?.command
  const startupEvidence = startupGate?.evidence || {}
  const cutoverChecks = cutover?.checks || []
  const blockedCutover = cutoverChecks.find((check) => !check.ready)
  const visibleCutoverChecks = [
    ...cutoverChecks.filter((check) => !check.ready),
    ...cutoverChecks.filter((check) => check.ready),
  ].slice(0, 3)
  const cutoverActions = cutoverActionSteps(cutover)
  return (
    <div className={`deployment-hardening ${hardening.ready ? 'ready' : 'attention'}`}>
      <div className="git-status-line">
        <span>
          {hardening.ready ? <CheckCircle size={14} /> : <WarningCircle size={14} />}
          Deployment hardening
        </span>
        <small>{hardening.score}% · {hardening.ready_count}/{hardening.total_count}</small>
      </div>
      <small className="runtime-detail">
        {hardening.environment || 'local'} · {hardening.ready ? 'ready' : 'needs attention'}
      </small>
      {startupGate && (
        <div className={`startup-gate ${startupGate.ready ? 'ready' : 'blocked'}`} title={startupGate.detail}>
          <div>
            {startupGate.ready ? <CheckCircle size={13} /> : <WarningCircle size={13} />}
            <span>Startup gate</span>
            <b>{startupGate.ready ? 'enforced' : 'blocked'}</b>
          </div>
          <small>
            {startupEvidence.environment || hardening.environment || 'local'}
            {' · '}
            {startupEvidence.production_startup_security_gate ? 'fail-closed on' : 'fail-closed off'}
            {' · '}
            {startupEvidence.startup_gate || startupGate.status}
          </small>
        </div>
      )}
      {cutover && (
        <div className={`cutover-status ${cutover.ready ? 'ready' : 'blocked'}`}>
          <div>
            {cutover.ready ? <CheckCircle size={13} /> : <WarningCircle size={13} />}
            <span>Team cutover</span>
            <b>{cutover.ready ? 'ready' : 'blocked'}</b>
          </div>
          <small title={blockedCutover?.summary || cutover.next_steps?.[0] || ''}>
            {blockedCutover?.summary || 'bootstrap removed · real users · secrets ready'}
          </small>
          {!cutover.ready && cutoverActions.length > 0 && (
            <div className="cutover-actions" aria-label="Production cutover actions">
              {cutoverActions.map((step) => (
                <span key={step} title={step}>{step}</span>
              ))}
            </div>
          )}
          {visibleCutoverChecks.length > 0 && (
            <div className="cutover-checks">
              {visibleCutoverChecks.map((check) => (
                <span className={check.ready ? 'ok' : 'warn'} key={check.id} title={check.summary}>
                  {check.ready ? <Check size={10} /> : <WarningCircle size={10} />}
                  {check.label}
                </span>
              ))}
            </div>
          )}
        </div>
      )}
      <div className="runtime-checks hardening-checks">
        {visibleChecks.map((check) => (
          <span
            className={check.ready ? 'ok' : 'warn'}
            key={check.id}
            title={check.detail}
          >
            {check.ready ? <Check size={10} /> : <WarningCircle size={10} />}
            {check.label}
          </span>
        ))}
      </div>
      {nextStep && <small className="runtime-detail warn">{nextStep}</small>}
      {command && <div className="readiness-command mono deployment-command">{command}</div>}
    </div>
  )
}

function stageLabel(stage) {
  return String(stage || 'idle').replaceAll('_', ' ')
}

function LiveDiagnosticsPanel({ diagnostics, canManageRuntime = false }) {
  const [warmupBusy, setWarmupBusy] = useState(false)
  const [warmupMessage, setWarmupMessage] = useState('')
  const [readiness, setReadiness] = useState(null)
  useEffect(() => {
    let alive = true
    fetchSystemReadiness()
      .then((result) => {
        if (alive) setReadiness(result)
      })
      .catch(() => {
        if (alive) setReadiness(null)
      })
    return () => {
      alive = false
    }
  }, [])
  if (!diagnostics) return null
  const checks = liveDiagnosticChecks(diagnostics)
  const stageEvents = diagnostics.stageEvents || []
  const runtimeLabel = diagnostics.device
    ? `${diagnostics.device}${diagnostics.computeType ? ` / ${diagnostics.computeType}` : ''}`
    : 'local'
  const shouldOfferWarmup = diagnostics.warmupRecommended
    || (diagnostics.provider === 'whisperx' && diagnostics.phase === 'degraded')
  const speakerReadiness = liveSpeakerReadiness(diagnostics, readiness)
  const latencySummary = liveLatencySummary(diagnostics)
  const showLatency = diagnostics.active
    || diagnostics.latencySequence
    || (diagnostics.latencyCompletionCount || 0) > 0

  async function warmupSpeakerProvider() {
    if (warmupBusy) return
    setWarmupBusy(true)
    setWarmupMessage('')
    try {
      const result = await startSpeakerWarmup()
      setWarmupMessage(result.state === 'running'
        ? 'Speaker warmup started'
        : result.detail || warmupStatusLabel(result))
    } catch (err) {
      setWarmupMessage(err.message || 'Speaker warmup failed')
    } finally {
      setWarmupBusy(false)
    }
  }

  return (
    <div>
      <p className="section-lab">Live diagnostics</p>
      <div className="live-diagnostics">
        <div className="git-status-line">
          <span><Code size={14} />{diagnostics.active ? 'Meeting live' : 'Meeting idle'}</span>
          <small>{diagnostics.smokeMode ? 'smoke' : diagnostics.phase}</small>
        </div>
        <div className="live-diag-checks">
          {checks.map((check) => (
            <span className={check.ready ? 'ok' : 'warn'} key={check.label}>
              {check.ready ? <Check size={10} /> : <X size={10} />}
              {check.label} {supportLabel(check.ready)}
            </span>
          ))}
        </div>
        <div className="runtime-grid">
          <div className="runtime-row">
            <span>Stage</span>
            <b>{diagnostics.stage || 'idle'}</b>
          </div>
          <div className="runtime-row">
            <span>Chunks sent</span>
            <b>{diagnostics.chunksSent || 0}</b>
          </div>
          <div className="runtime-row">
            <span>Backend</span>
            <b className={diagnostics.backendBusy ? 'warn' : ''}>
              {diagnostics.backendBusy ? 'processing' : 'ready'}
            </b>
          </div>
          <div className="runtime-row">
            <span>Held latest</span>
            <b className={diagnostics.heldChunk ? 'warn' : ''}>
              {diagnostics.heldChunk ? `${diagnostics.heldChunkKb || 1} KB` : 'none'}
            </b>
          </div>
          {(diagnostics.replacedChunks || 0) > 0 && (
            <div className="runtime-row">
              <span>Replaced</span>
              <b className="warn">{diagnostics.replacedChunks}</b>
            </div>
          )}
          {(diagnostics.skippedChunks || 0) > 0 && (
            <div className="runtime-row">
              <span>Backend busy</span>
              <b className="warn">{diagnostics.skippedChunks}</b>
            </div>
          )}
          {(diagnostics.queuedChunks || 0) > 0 && (
            <div className="runtime-row">
              <span>Backend queued</span>
              <b>{diagnostics.queuedChunks}</b>
            </div>
          )}
          {(diagnostics.pendingChunks || 0) > 0 && (
            <div className="runtime-row">
              <span>Backend pending</span>
              <b className="warn">{diagnostics.pendingChunks}</b>
            </div>
          )}
          {(diagnostics.replacedPendingChunks || 0) > 0 && (
            <div className="runtime-row">
              <span>Backend replaced</span>
              <b className="warn">{diagnostics.replacedPendingChunks}</b>
            </div>
          )}
          {(diagnostics.reconnectAttempts || 0) > 0 && (
            <div className="runtime-row">
              <span>Reconnects</span>
              <b className="warn">{diagnostics.reconnectAttempts}</b>
            </div>
          )}
          {(diagnostics.droppedChunks || 0) > 0 && (
            <div className="runtime-row">
              <span>Dropped chunks</span>
              <b className="warn">{diagnostics.droppedChunks}</b>
            </div>
          )}
          {(diagnostics.duplicateChunks || 0) > 0 && (
            <div className="runtime-row">
              <span>Duplicates</span>
              <b className="warn">{diagnostics.duplicateChunks}</b>
            </div>
          )}
          {(diagnostics.outOfOrderChunks || 0) > 0 && (
            <div className="runtime-row">
              <span>Out of order</span>
              <b className="warn">{diagnostics.outOfOrderChunks}</b>
            </div>
          )}
          {(diagnostics.emptyChunks || 0) > 0 && (
            <div className="runtime-row">
              <span>Empty chunks</span>
              <b className="warn">{diagnostics.emptyChunks}</b>
            </div>
          )}
          {(diagnostics.oversizedChunks || 0) > 0 && (
            <div className="runtime-row">
              <span>Too large</span>
              <b className="warn">{diagnostics.oversizedChunks}</b>
            </div>
          )}
          {(diagnostics.gapChunks || 0) > 0 && (
            <div className="runtime-row">
              <span>Sequence gaps</span>
              <b className="warn">{diagnostics.gapChunks}</b>
            </div>
          )}
          {(diagnostics.maxChunkKb || 0) > 0 && (
            <div className="runtime-row">
              <span>Max chunk</span>
              <b>{diagnostics.maxChunkKb} KB</b>
            </div>
          )}
          <div className="runtime-row">
            <span>Last event</span>
            <b title={diagnostics.lastEvent || undefined}>{diagnosticsEventLabel(diagnostics.lastEvent || 'idle')}</b>
          </div>
          {diagnostics.lastErrorKind && (
            <div className="runtime-row">
              <span>{diagnosticsErrorLabel(diagnostics.lastErrorKind)}</span>
              <b className="warn">{diagnostics.lastErrorKind}</b>
            </div>
          )}
          <div className="runtime-row">
            <span>Provider</span>
            <b>{diagnostics.provider || 'local'}</b>
          </div>
          <div className="runtime-row">
            <span>Speaker readiness</span>
            <b className={speakerReadiness.tone === 'warn' ? 'warn' : ''}>{speakerReadiness.label}</b>
          </div>
          <div className="runtime-row">
            <span>Captions</span>
            <b className={captionStatusClass(diagnostics.captionState, diagnostics.instantCaptions)}>
              {captionStatusLabel(diagnostics.captionState, diagnostics.instantCaptions)}
            </b>
          </div>
          <div className="runtime-row">
            <span>Correction</span>
            <b className={diagnostics.correctionPending ? 'warn' : ''}>
              {diagnostics.correctionPending
                ? 'pending'
                : (diagnostics.lateCorrections || 0) > 0
                  ? `late ${diagnostics.lateCorrections}`
                  : 'ready'}
            </b>
          </div>
          <div className="runtime-row">
            <span>Runtime</span>
            <b>{runtimeLabel}</b>
          </div>
        </div>
        {showLatency && (
          <div className={`latency-panel ${latencySummary.tone}`}>
            <div className="latency-head">
              <span><Waveform size={12} />Live latency</span>
              <b>{latencySummary.label}</b>
            </div>
            <div className="latency-grid">
              <div>
                <span>Current</span>
                <b>{diagnostics.latencySequence ? `#${diagnostics.latencySequence} ${stageLabel(diagnostics.latencyStage)}` : 'waiting'}</b>
              </div>
              <div>
                <span>Elapsed</span>
                <b>{formatDuration(diagnostics.latencyElapsedMs)}</b>
              </div>
              <div>
                <span>Last final</span>
                <b>{diagnostics.latencyLastChunkMs == null
                  ? 'pending'
                  : `#${diagnostics.latencyLastChunkSequence} ${formatDuration(diagnostics.latencyLastChunkMs)}`}</b>
              </div>
              <div>
                <span>Best warm</span>
                <b>{formatDuration(diagnostics.latencyBestWarmMs)}</b>
              </div>
              <div>
                <span>Cold start</span>
                <b>{formatDuration(diagnostics.latencyColdStartMs)}</b>
              </div>
              <div>
                <span>Final chunks</span>
                <b>{diagnostics.latencyCompletionCount || 0}</b>
              </div>
            </div>
            <small>{latencySummary.detail}</small>
          </div>
        )}
        {diagnostics.captionPreview && (
          <div className="caption-preview">
            <Waveform size={12} />
            <span>{diagnostics.captionPreview}</span>
          </div>
        )}
        {(diagnostics.lastErrorMessage || diagnostics.recoveryHint) && (
          <div className="runtime-warning live-diag-message warn">
            <WarningCircle size={13} />
            <span>
              {diagnostics.lastErrorMessage || diagnosticsFallbackMessage(diagnostics.lastErrorKind)}
              {diagnostics.recoveryHint ? ` ${diagnostics.recoveryHint}` : ''}
            </span>
          </div>
        )}
        {speakerReadiness.detail && diagnostics.provider === 'whisperx' && (
          <div className={`runtime-warning live-diag-message ${speakerReadiness.tone === 'ok' ? 'ok' : ''}`}>
            {speakerReadiness.tone === 'ok' ? <CheckCircle size={13} /> : <WarningCircle size={13} />}
            <span>{speakerReadiness.detail}</span>
          </div>
        )}
        {diagnostics.captionFallback && (
          <div className="runtime-warning live-diag-message">
            <WarningCircle size={13} />
            <span>Using browser captions while speaker labels finish in the background.</span>
          </div>
        )}
        {stageEvents.length > 0 && (
          <div className="stage-timeline">
            <div className="stage-timeline-head">
              <span>Processing timeline</span>
              <b>{formatDuration(diagnostics.stageTotalMs)}</b>
            </div>
            {stageEvents.slice(-6).map((event, index) => (
              <div className="stage-timeline-row" key={`${event.sequence}-${event.stage}-${index}`}>
                <span>{stageLabel(event.stage)}</span>
                <b>{formatDuration(event.durationMs ?? event.elapsedMs)}</b>
              </div>
            ))}
          </div>
        )}
        {diagnostics.cpuMode && (
          <div className="runtime-warning live-diag-message">
            <WarningCircle size={13} />
            <span>CPU mode may lag. First model load is usually the slowest stage.</span>
          </div>
        )}
        {shouldOfferWarmup && canManageRuntime && (
          <div className="live-warmup-action">
            <div>
              <WarningCircle size={13} />
              <span>{diagnostics.warmupHint || 'Warm speaker models before the next live meeting.'}</span>
            </div>
            <button type="button" onClick={warmupSpeakerProvider} disabled={warmupBusy}>
              {warmupBusy ? 'Starting' : 'Warm'}
            </button>
          </div>
        )}
        {warmupMessage && (
          <div className="runtime-warning live-diag-message">
            <WarningCircle size={13} />
            <span>{warmupMessage}</span>
          </div>
        )}
        {diagnostics.lastMessage && (
          <div className="runtime-warning live-diag-message">
            <WarningCircle size={13} />
            <span>{diagnostics.lastMessage}</span>
          </div>
        )}
      </div>
    </div>
  )
}

function SystemReadinessPanel({
  readiness,
  demoGateRun,
  demoGateResult,
  demoGateBusy,
  onRunDemoGate,
  canManageRuntime = false,
}) {
  if (!readiness) return null
  const checks = readiness.checks || []
  const speakerVerification = speakerVerificationSummary(readiness)
  const demoEvidence = demoEvidenceSummary(readiness)
  const visibleChecks = checks.filter((check) => (
    ['workspace', 'git', 'speaker', 'memory', 'live', 'diarization'].includes(check.id)
  ))
  const blockingChecks = new Set(['backend', 'workspace', 'git', 'speaker', 'memory', 'live'])
  const firstIssue = checks.find((check) => blockingChecks.has(check.id) && !check.ready)
  const statusLabel = readiness.ready ? 'ready' : 'needs attention'
  const runningGateId = demoGateRun?.state === 'running' ? demoGateRun.gate_id : ''
  return (
    <div className={`system-readiness ${readiness.ready ? 'ready' : 'attention'}`}>
      <div className="git-status-line">
        <span>
          {readiness.ready ? <CheckCircle size={14} /> : <WarningCircle size={14} />}
          Demo readiness
        </span>
        <small>{statusLabel}</small>
      </div>
      <div className="runtime-checks readiness-checks">
        {visibleChecks.map((check) => (
          <span className={check.ready ? 'ok' : 'warn'} key={check.id} title={check.detail || check.label}>
            {check.ready ? <Check size={10} /> : <X size={10} />}
            {check.label}
          </span>
        ))}
      </div>
      <div className="readiness-command mono">{readiness.demo_command || 'cd backend && python scripts/demo_readiness.py'}</div>
      <div className="demo-evidence">
        <div className="demo-evidence-title">Evidence chain</div>
        {demoEvidence.map((item) => (
          <div className={`demo-evidence-row ${item.tone}`} key={item.id}>
            <div className="demo-evidence-head">
              <span>{item.tone === 'ok' ? <CheckCircle size={12} /> : <WarningCircle size={12} />}{item.label}</span>
              <b>{item.status}</b>
            </div>
            <small>{[item.metric, item.labels, item.elapsed].filter(Boolean).join(' | ')}</small>
            <small className="runtime-detail">{item.detail}</small>
            <div className="demo-evidence-actions">
              {canManageRuntime && (
                <button
                  type="button"
                  onClick={() => onRunDemoGate?.(item.id)}
                  disabled={demoGateBusy || Boolean(runningGateId)}
                  title={item.command}
                >
                  {runningGateId === item.id ? 'Running' : 'Run gate'}
                </button>
              )}
              {runningGateId === item.id && demoGateRun?.detail && (
                <span>{demoGateRun.detail}</span>
              )}
            </div>
            {runningGateId === item.id && recentGateStages(demoGateRun).length > 0 && (
              <div className="demo-gate-stages">
                {recentGateStages(demoGateRun).map((stage) => (
                  <div className="demo-gate-stage" key={`${stage.stage}:${stage.elapsed_ms}`}>
                    <span>{stageLabel(stage.stage)}</span>
                    <b>{formatDuration(stage.elapsed_ms)}</b>
                    <small>{stage.message}</small>
                  </div>
                ))}
              </div>
            )}
            {demoGateResult?.gateId === item.id && runningGateId !== item.id && (
              <div className={`demo-gate-result ${demoGateResult.tone}`}>
                <div>
                  {demoGateResult.tone === 'ok' ? <CheckCircle size={12} /> : <WarningCircle size={12} />}
                  <span>Timeline result</span>
                  <b>{demoGateResult.status}</b>
                </div>
                {demoGateResult.metric && <small>{demoGateResult.metric}</small>}
                {demoGateResult.jobId && <small className="mono">{demoGateResult.jobId}</small>}
                {demoGateResult.detail && <small className="runtime-detail">{demoGateResult.detail}</small>}
              </div>
            )}
            <div className="readiness-command mono evidence-command">{item.command}</div>
          </div>
        ))}
      </div>
      {speakerVerification && (
        <div className={`speaker-verification ${speakerVerification.tone}`}>
          <div className="runtime-row">
            <span>{speakerVerification.title}</span>
            <b className={speakerVerification.tone === 'ok' ? '' : 'warn'}>{speakerVerification.metric}</b>
          </div>
          {(speakerVerification.labels || speakerVerification.elapsed || speakerVerification.checkedAt) && (
            <small className="runtime-detail">
              {[speakerVerification.labels, speakerVerification.elapsed, speakerVerification.checkedAt]
                .filter(Boolean)
                .join(' | ')}
            </small>
          )}
          <small className="runtime-detail">{speakerVerification.detail}</small>
          {speakerVerification.quality?.label && (
            <small className="runtime-detail">{speakerVerification.quality.label}</small>
          )}
          {speakerVerification.quality?.notes?.[0] && (
            <small className="runtime-detail warn">{speakerVerification.quality.notes[0]}</small>
          )}
          {speakerVerification.warnings?.[0] && (
            <small className="runtime-detail warn">{speakerVerification.warnings[0]}</small>
          )}
          {!readiness.speaker_verification?.verified && (
            <div className="readiness-command mono verification-command">{speakerVerification.command}</div>
          )}
        </div>
      )}
      {firstIssue && (
        <div className="runtime-warning">
          <WarningCircle size={13} />
          <span>{firstIssue.label}: {firstIssue.detail || firstIssue.status}</span>
        </div>
      )}
    </div>
  )
}

function warmupStatusLabel(warmup) {
  if (!warmup) return 'not checked'
  if (warmup.state === 'succeeded') return 'warmed'
  if (warmup.state === 'running') return 'warming'
  if (warmup.state === 'failed') return 'failed'
  if (warmup.state === 'skipped') return 'not needed'
  return warmup.state || 'idle'
}

export function TargetReadinessPanel({ target }) {
  if (!target) return null
  const milestones = target.milestones || []
  const firstBlocked = milestones.find((item) => !item.ready)
  const nextStep = target.next_steps?.[0]
  const blockedActions = milestones
    .filter((item) => !item.ready && (item.next_action || item.command))
    .slice(0, 3)
  return (
    <div className={`target-readiness ${target.ready ? 'ready' : 'attention'}`}>
      <div className="git-status-line">
        <span>
          {target.ready ? <CheckCircle size={14} /> : <WarningCircle size={14} />}
          Target closure
        </span>
        <small>{target.score}% · {target.ready_count}/{target.total_count}</small>
      </div>
      <div className="target-milestones">
        {milestones.slice(0, 7).map((milestone) => (
          <div className={milestone.ready ? 'ready' : 'blocked'} key={milestone.id}>
            <div>
              {milestone.ready ? <Check size={11} /> : <X size={11} />}
              <span>{milestone.label}</span>
            </div>
            <small>{milestone.evidence || milestone.status}</small>
          </div>
        ))}
      </div>
      {nextStep && <small className="runtime-detail warn">{nextStep}</small>}
      {!target.ready && blockedActions.length > 0 && (
        <div className="target-bootstrap">
          <div className="target-bootstrap-head">
            <span><ClipboardText size={13} />Readiness actions</span>
            <small>{blockedActions.length} blocker{blockedActions.length === 1 ? '' : 's'}</small>
          </div>
          {blockedActions.map((item) => (
            <div className="target-bootstrap-row" key={item.id}>
              <div>
                <b>{item.label}</b>
                <small>{item.next_action || item.detail || item.status}</small>
              </div>
              {item.command && <code>{item.command}</code>}
            </div>
          ))}
        </div>
      )}
      {!blockedActions.length && firstBlocked?.command && (
        <div className="readiness-command mono target-command">{firstBlocked.command}</div>
      )}
    </div>
  )
}

function LocalDoctorPanel({ doctor }) {
  if (!doctor) return null
  const visibleChecks = (doctor.checks || []).slice(0, 6)
  const nextSteps = (doctor.next_steps || []).slice(0, 2)
  return (
    <div className={`local-doctor ${doctor.ready ? 'ready' : 'attention'}`}>
      <div className="git-status-line">
        <span>{doctor.ready ? <CheckCircle size={14} /> : <WarningCircle size={14} />}Local audio doctor</span>
        <small>{doctor.ready ? 'ready' : 'needs attention'}</small>
      </div>
      <small className="runtime-detail">
        {doctor.provider} · {doctor.device} · {doctor.worker_mode}
      </small>
      <div className="runtime-checks doctor-checks">
        {visibleChecks.map((check) => (
          <span
            className={check.ready && check.status !== 'warning' ? 'ok' : 'warn'}
            key={check.id}
            title={check.action || check.detail}
          >
            {check.ready && check.status !== 'warning' ? <Check size={10} /> : <WarningCircle size={10} />}
            {check.label}
          </span>
        ))}
      </div>
      {doctor.warnings?.[0] && (
        <small className="runtime-detail warn">{doctor.warnings[0]}</small>
      )}
      {nextSteps.length > 0 && (
        <div className="doctor-next">
          {nextSteps.map((step) => (
            <small key={step}>{step}</small>
          ))}
        </div>
      )}
      <div className="readiness-command mono doctor-command">{doctor.command}</div>
    </div>
  )
}

function RuntimeStatusPanel({
  status,
  localDoctor,
  speakerWarmup,
  speakerVerification,
  warmupBusy,
  verificationBusy,
  onWarmup,
  onVerify,
  onVerifyGenerated,
  canManageRuntime = false,
}) {
  const [verificationFile, setVerificationFile] = useState(null)
  const [requireMultipleSpeakers, setRequireMultipleSpeakers] = useState(true)
  if (!status) return null
  const stores = status.stores || []
  const providers = status.providers || []
  const warnings = status.warnings || []
  const visibleStores = stores.filter((item) => ['collab', 'speakers'].includes(item.id))
  const visibleProviders = providers.filter((item) => ['speaker', 'llm'].includes(item.id))
  const speakerProvider = providers.find((item) => item.id === 'speaker')
  const canWarmup = canManageRuntime && speakerProvider?.value === 'whisperx'
  const canVerify = canWarmup
  const warmupTerminal = ['succeeded', 'failed', 'skipped'].includes(speakerWarmup?.state)
  const latestStage = speakerWarmup?.stages?.[speakerWarmup.stages.length - 1]
  const verificationSummary = speakerVerificationJobSummary(speakerVerification)
  const verificationRunning = speakerVerification?.state === 'running'
  const verificationStages = speakerVerification?.stages || []
  return (
    <div className="runtime-status">
      <div className="git-status-line">
        <span><Code size={14} />Runtime</span>
        <small>{warnings.length ? `${warnings.length} warnings` : 'ready'}</small>
      </div>
      <div className="runtime-grid">
        {visibleStores.map((store) => (
          <div className="runtime-row" key={store.id}>
            <span>{store.label}</span>
            <b className={store.exists ? '' : 'warn'}>{store.backend}{store.exists ? '' : ' missing'}</b>
          </div>
        ))}
        {visibleProviders.map((provider) => (
          <div className="runtime-provider" key={provider.id}>
            <div className="runtime-row">
              <span>{provider.label}</span>
              <b className={provider.ready ? '' : 'warn'}>{provider.value}{provider.ready ? '' : ' not ready'}</b>
            </div>
            {provider.detail && <small className="runtime-detail">{provider.detail}</small>}
            {provider.checks?.length ? (
              <div className="runtime-checks">
                {provider.checks.slice(0, 3).map((check) => (
                  <span className={check.ready ? 'ok' : 'warn'} key={check.id}>
                    {check.ready ? <Check size={10} /> : <X size={10} />}
                    {check.label}
                  </span>
                ))}
              </div>
            ) : null}
          </div>
        ))}
      </div>
      <LocalDoctorPanel doctor={localDoctor} />
      {(canWarmup || (speakerWarmup && speakerWarmup.state !== 'idle')) && (
        <div className={`speaker-warmup ${speakerWarmup?.state || 'idle'}`}>
          <div className="git-status-line">
            <span><Code size={14} />Speaker warmup</span>
            <small>{warmupStatusLabel(speakerWarmup)}</small>
          </div>
          <div className="warmup-row">
            <span>{latestStage?.message || speakerWarmup?.detail || 'Load WhisperX before the first live meeting'}</span>
            {canWarmup && (
              <button
                type="button"
                onClick={onWarmup}
                disabled={warmupBusy || speakerWarmup?.state === 'running'}
              >
                {speakerWarmup?.state === 'running' ? 'Running' : warmupTerminal ? 'Run again' : 'Warm'}
              </button>
            )}
          </div>
          {speakerWarmup?.stages?.length ? (
            <div className="stage-timeline warmup-stages">
              {speakerWarmup.stages.slice(-3).map((stage, index) => (
                <div className="stage-timeline-row" key={`${stage.stage}-${index}`}>
                  <span>{stageLabel(stage.stage)}</span>
                  <b>{formatDuration(stage.elapsed_ms)}</b>
                </div>
              ))}
            </div>
          ) : null}
          {speakerWarmup?.error && (
            <div className="runtime-warning" role="alert">
              <WarningCircle size={13} />
              <span>{speakerWarmup.error}</span>
            </div>
          )}
        </div>
      )}
      {(canVerify || (speakerVerification && speakerVerification.state !== 'idle')) && (
        <div className={`speaker-verification-job ${speakerVerification?.state || 'idle'}`}>
          <div className="git-status-line">
            <span><Waveform size={14} />Real audio verify</span>
            <small>{verificationSummary.label}</small>
          </div>
          <small className="runtime-detail">{verificationSummary.detail}</small>
          {(verificationSummary.elapsed || verificationSummary.timeout) && (
            <small className="runtime-detail">
              {verificationSummary.elapsed ? `elapsed ${verificationSummary.elapsed}` : ''}
              {verificationSummary.elapsed && verificationSummary.timeout ? ' · ' : ''}
              {verificationSummary.timeout ? `timeout ${verificationSummary.timeout}` : ''}
            </small>
          )}
          {verificationStages.length ? (
            <div className="stage-timeline warmup-stages">
              {verificationStages.slice(-3).map((stage, index) => (
                <div className="stage-timeline-row" key={`${stage.stage}-${index}`}>
                  <span>{stageLabel(stage.stage)}</span>
                  <b>{formatDuration(stage.elapsed_ms)}</b>
                </div>
              ))}
            </div>
          ) : null}
          {canVerify && (
            <>
              <form
                className="verification-upload"
                onSubmit={(event) => {
                  event.preventDefault()
                  onVerify?.(verificationFile, requireMultipleSpeakers)
                }}
              >
                <label className="verification-file">
                  <input
                    type="file"
                    accept="audio/*,.wav,.mp3,.webm,.m4a,.mp4,.aiff,.aif"
                    onChange={(event) => setVerificationFile(event.target.files?.[0] || null)}
                    disabled={verificationBusy || verificationRunning}
                  />
                  <span>{verificationFile ? verificationFile.name : 'Choose audio'}</span>
                </label>
                <label className="verification-strict">
                  <input
                    type="checkbox"
                    checked={requireMultipleSpeakers}
                    onChange={(event) => setRequireMultipleSpeakers(event.target.checked)}
                    disabled={verificationBusy || verificationRunning}
                  />
                  <span>2+ speakers</span>
                </label>
                <button type="submit" disabled={!verificationFile || verificationBusy || verificationRunning}>
                  {verificationRunning ? 'Running' : verificationBusy ? 'Starting' : 'Verify'}
                </button>
              </form>
              <button
                className="verification-generate"
                type="button"
                onClick={() => onVerifyGenerated?.(requireMultipleSpeakers)}
                disabled={verificationBusy || verificationRunning}
              >
                <Waveform size={13} />
                {verificationBusy || verificationRunning ? 'Running sample' : 'Generate sample'}
              </button>
            </>
          )}
          {speakerVerification?.verification?.error && (
            <div className="runtime-warning" role="alert">
              <WarningCircle size={13} />
              <span>{speakerVerification.verification.error}</span>
            </div>
          )}
        </div>
      )}
      {warnings[0] && (
        <div className="runtime-warning">
          <WarningCircle size={13} />
          <span>{warnings[0]}</span>
        </div>
      )}
    </div>
  )
}

function GitStatusPanel({ status, diff }) {
  if (!status) return null
  if (!status.is_git_repo) {
    return (
      <div className="git-status warning">
        <WarningCircle size={14} />
        <span>{status.warning || 'Not a git repo'}</span>
      </div>
    )
  }
  const changed = status.files?.length || 0
  const first = status.files?.[0]
  const diffLabel = compactFiles(diff?.files_changed || [])
  return (
    <div className="git-status">
      <div className="git-status-line">
        <span><GitBranch size={14} />{status.branch || 'detached'}</span>
        <small>{status.dirty ? `${changed} changed` : 'clean'}</small>
      </div>
      {first && (
        <div className="git-status-file">
          <span className="mono">{first.status}</span>
          <span>{first.path}</span>
        </div>
      )}
      {diffLabel && (
        <div className="git-status-file muted">
          <GitDiff size={13} />
          <span>{diffLabel}</span>
        </div>
      )}
    </div>
  )
}

function CacheStatusPanel({ status, busy, onClear, canManageRuntime = false }) {
  if (!status) return null
  return (
    <div className="git-status cache-status">
      <div className="git-status-line">
        <span><Code size={14} />Runtime cache</span>
        <small>{status.entries} entries</small>
      </div>
      <div className="cache-status-row">
        <span>
          {formatBytes(status.size_bytes)} · {status.fingerprinted_entries || 0}/{status.rebuildable_entries || status.entries} checked · git {status.git_ttl_seconds}s
        </span>
        {canManageRuntime && (
          <button type="button" onClick={onClear} disabled={busy}>
            {busy ? 'Clearing' : 'Clear'}
          </button>
        )}
      </div>
    </div>
  )
}

function compactFiles(files) {
  if (!files.length) return ''
  if (files.length === 1) return files[0]
  return `${files[0]} +${files.length - 1} more`
}

function formatBytes(value) {
  const bytes = Number(value || 0)
  if (bytes < 1024) return `${bytes} B`
  return `${(bytes / 1024).toFixed(1)} KB`
}

function defaultCommitMessage(action, agentName = APP_NAME) {
  const files = (action.files_changed || []).slice(0, 2).join(', ')
  const suffix = action.files_changed?.length > 2 ? ` +${action.files_changed.length - 2}` : ''
  return `${agentName}: ${files ? `update ${files}${suffix}` : 'approved patch'}`
}

function routeTraceLabel(trace) {
  if (!trace?.route) return ''
  const route = String(trace.route).replace(/_/g, ' ')
  const policy = String(trace.action_policy || '').replace(/_/g, ' ')
  return policy ? `${route} · ${policy}` : route
}

function ReviewQueue({ items = [], agentName = APP_NAME }) {
  const visibleItems = items.slice(-1).reverse()
  const hiddenItems = items.slice(0, -1).reverse()
  if (!visibleItems.length) return null
  const renderReviewItem = (item, compact = false) => {
    const routeLabel = routeTraceLabel(item.route_trace)
    const title = displayText(item.title)
    const detail = displayText(item.detail)
    const auditTitle = detail ? `${title} - ${detail}` : title
    return (
      <div className={`handoff-review-item ${item.kind}${compact ? ' compact' : ''}`} key={item.id} title={auditTitle}>
        <div>
          <span className="mono">{memoryLabel(item.kind)}</span>
          <b title={item.title}>{title}</b>
        </div>
        {!compact && <p title={item.detail}>{detail}</p>}
        <small>
          {displayActorName(item.actor_name, agentName)}
          {item.status ? ` · ${String(item.status).replace(/_/g, ' ')}` : ''}
        </small>
        {routeLabel && <small className="handoff-review-route">{routeLabel}</small>}
      </div>
    )
  }
  return (
    <div className="handoff-review" data-testid="handoff-review">
      <div className="handoff-review-head">
        <span>Open review</span>
        <small>{items.length}</small>
      </div>
      {visibleItems.map((item) => renderReviewItem(item, true))}
      {hiddenItems.length > 0 && (
        <details className="handoff-review-extra">
          <summary>{hiddenItems.length} more open item{hiddenItems.length === 1 ? '' : 's'}</summary>
          <div>
            {hiddenItems.map((item) => renderReviewItem(item, true))}
          </div>
        </details>
      )}
    </div>
  )
}

export function splitActionRows(actions = []) {
  const pending = []
  const history = []
  ;(actions || []).forEach((action) => {
    if (action.status === 'pending_approval' || action.pending_approval) pending.push(action)
    else history.push(action)
  })
  return { pending, history }
}

function ActionHistoryDetails({ actions = [], agentName, onApproveAction, onRejectAction, onCommitAction, onCreatePullRequest }) {
  if (!actions.length) return null
  return (
    <details className="action-history-details">
      <summary aria-label={`Action history: ${actions.length} recent`}>
        <span>Action history</span>
        <small>{actions.length} recent</small>
      </summary>
      <div className="action-history-body">
        {actions.map((action) => (
          <ActionLogItem
            action={action}
            agentName={agentName}
            key={action.id}
            onApproveAction={onApproveAction}
            onRejectAction={onRejectAction}
            onCommitAction={onCommitAction}
            onCreatePullRequest={onCreatePullRequest}
          />
        ))}
      </div>
    </details>
  )
}

export function pullRequestPlanState(plan) {
  if (!plan) return { canCreate: false, title: '', detail: '' }
  const preflight = plan.preflight || {}
  const canCreate = Boolean(plan.ready && preflight.real_creation_ready)
  if (plan.mode === 'created') {
    return { canCreate: false, title: 'GitHub PR created', detail: plan.pull_request_url || '' }
  }
  if (!plan.ready) {
    return { canCreate: false, title: 'GitHub PR blocked', detail: (plan.blockers || [])[0] || 'Resolve blockers before opening a PR.' }
  }
  if (canCreate) {
    return { canCreate: true, title: 'GitHub PR ready', detail: 'GitHub CLI is authenticated and real PR creation is enabled.' }
  }
  return {
    canCreate: false,
    title: 'GitHub PR plan ready',
    detail: (preflight.real_creation_blockers || [])[0]
      || preflight.github_cli_auth_detail
      || (preflight.real_creation_enabled ? 'GitHub CLI auth is not ready.' : 'Real GitHub PR creation is disabled; use the dry-run commands.'),
  }
}

export function pullRequestReadinessFacts(plan) {
  if (!plan) return []
  const preflight = plan.preflight || {}
  const ghReady = preflight.github_cli_available && preflight.github_cli_authenticated
  return [
    { key: 'branch', label: preflight.head_branch_present ? 'branch ready' : 'no branch', tone: preflight.head_branch_present ? 'ready' : 'blocked' },
    { key: 'commit', label: preflight.commit_present ? 'commit ready' : 'commit needed', tone: preflight.commit_present ? 'ready' : 'blocked' },
    {
      key: 'remote',
      label: preflight.remote_is_github ? 'GitHub remote' : preflight.remote_present ? 'non-GitHub remote' : 'no origin',
      tone: preflight.remote_is_github ? 'ready' : 'blocked',
    },
    {
      key: 'gh',
      label: ghReady ? 'gh authenticated' : preflight.github_cli_available ? 'gh login needed' : 'gh missing',
      tone: ghReady ? 'ready' : 'blocked',
    },
    {
      key: 'mode',
      label: preflight.real_creation_ready ? 'real PR enabled' : preflight.real_creation_enabled ? 'real PR blocked' : 'dry-run only',
      tone: preflight.real_creation_ready ? 'ready' : preflight.real_creation_enabled ? 'blocked' : 'manual',
    },
  ]
}

export function PullRequestReadiness({ plan }) {
  const facts = pullRequestReadinessFacts(plan)
  if (!facts.length) return null
  return (
    <div className="pr-readiness" aria-label="Pull request readiness">
      {facts.map((fact) => (
        <span className={fact.tone} key={fact.key}>{fact.label}</span>
      ))}
    </div>
  )
}

function ActionLogItem({ action, agentName, onApproveAction, onRejectAction, onCommitAction, onCreatePullRequest }) {
  const [expanded, setExpanded] = useState(action.status === 'pending_approval')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [commitMessage, setCommitMessage] = useState(() => defaultCommitMessage(action, agentName))
  const [commitEdited, setCommitEdited] = useState(false)
  const [prPlan, setPrPlan] = useState(null)
  const approval = action.approval || {}
  const routeTrace = approval.route_trace
  const git = approval.git || {}
  const pullRequest = approval.pull_request || {}
  const externalAgent = approval.external_agent || {}
  const externalAgentLabel = externalAgentDisplayLabel(externalAgent)
  const diff = approval.diff || ''
  const pending = action.status === 'pending_approval' || action.pending_approval
  const canDecide = pending && action.action === 'patch' && onApproveAction && onRejectAction
  const commitSha = git.commit_sha
  const canCommit = action.status === 'completed' && action.action === 'patch' && git.branch_name && !commitSha && onCommitAction
  const canPlanPr = action.status === 'completed' && action.action === 'patch' && git.branch_name && onCreatePullRequest
  const defaultMessage = defaultCommitMessage(action, agentName)
  const diffId = `diff-${action.id}`
  const requesterDisplay = actionRequesterDisplay(action)
  const summaryLabel = actionSummaryLabel(action)
  const gitAfterLabel = gitStatusAfterLabel(git)
  const summaryRaw = actionSummaryRaw(action)
  const lifecycleFacts = actionLifecycleFacts(action)
  const prState = pullRequestPlanState(prPlan)
  const duplicateLabel = action.duplicateCount > 1
    ? `${action.duplicateCount} similar actions`
    : ''
  const auditDetails = [
    duplicateLabel ? {
      key: 'repeat',
      label: duplicateLabel,
      detail: 'Compacted repeated completed actions.',
    } : null,
    routeTrace?.route ? {
      key: 'route',
      label: routeTraceLabel(routeTrace),
      detail: routeTrace.reason || '',
    } : null,
    action.files_changed?.length > 0 ? {
      key: 'files',
      label: `Changed ${action.files_changed.join(', ')}`,
      detail: '',
    } : null,
    approval.test_command ? {
      key: 'verification',
      label: `Verification ${approval.test_command}`,
      detail: '',
    } : null,
  ].filter(Boolean)
  const auditSummary = actionAuditSummary(auditDetails)

  useEffect(() => {
    if (!commitEdited) setCommitMessage(defaultMessage)
  }, [commitEdited, defaultMessage])

  async function decide(kind) {
    if (busy) return
    setBusy(kind)
    setError('')
    try {
      if (kind === 'approve') await onApproveAction?.(action.id)
      else await onRejectAction?.(action.id)
    } catch (err) {
      setError(err.message || `Could not ${kind} action`)
    } finally {
      setBusy('')
    }
  }

  async function commit(e) {
    e?.preventDefault()
    if (busy) return
    setBusy('commit')
    setError('')
    try {
      await onCommitAction?.(action.id, commitMessage.trim() || defaultMessage)
    } catch (err) {
      setError(err.message || 'Could not commit action')
    } finally {
      setBusy('')
    }
  }

  async function planPullRequest() {
    if (busy) return
    setBusy('pr')
    setError('')
    try {
      const plan = await onCreatePullRequest?.(action.id)
      setPrPlan(plan)
    } catch (err) {
      setError(err.message || 'Could not prepare pull request plan')
    } finally {
      setBusy('')
    }
  }

  async function createPullRequest() {
    if (busy) return
    setBusy('pr-create')
    setError('')
    try {
      const result = await onCreatePullRequest?.(action.id, { dry_run: false })
      setPrPlan(result)
    } catch (err) {
      setError(err.message || 'Could not create pull request')
    } finally {
      setBusy('')
    }
  }

  return (
    <div
      className={`action-log ${action.status || ''}`}
      data-testid={`agent-action-${action.status || 'logged'}`}
      key={action.id}
    >
      <div className="action-log-top">
        <div className="action-title">
          <b>{actionTypeLabel(action)}</b>
          <small className="action-requester">{requesterDisplay}</small>
          {externalAgentLabel && <small className="action-agent">Proposed by {externalAgentLabel}</small>}
        </div>
        <span className={`action-status ${action.status || ''}`}>{actionStatusLabel(action)}</span>
      </div>
      <p className="action-summary" title={summaryRaw}>{summaryLabel}</p>
      {lifecycleFacts.length > 0 && (
        <div className="action-lifecycle" aria-label="Action lifecycle summary">
          {lifecycleFacts.map((fact) => (
            <span className={fact.tone} title={fact.label} key={fact.key}>{fact.label}</span>
          ))}
        </div>
      )}
      {auditDetails.length > 0 && (
        <details className="action-audit-details">
          <summary aria-label={auditSummary ? `Action audit: ${auditSummary}` : 'Action audit'}>
            <span>Action audit</span>
            {auditSummary && <small>{auditSummary}</small>}
          </summary>
          <div className="action-audit-body">
            {auditDetails.map((item) => (
              <div className="action-route" key={item.key}>
                <span>{item.label}</span>
                {item.detail && <small>{item.detail}</small>}
              </div>
            ))}
          </div>
        </details>
      )}
      {git.branch_name && (
        <div className="action-git">
          <span><GitBranch size={13} />{git.branch_name}</span>
          {git.dirty_before && <span className="warn"><WarningCircle size={13} />dirty before approval</span>}
          {gitAfterLabel && <span>{gitAfterLabel}</span>}
          {commitSha && <span><GitCommit size={13} />{commitSha}</span>}
        </div>
      )}
      {git.warning && <div className="approval-warning">{git.warning}</div>}
      {pendingApprovalBrief(action) && (
        <div className="approval-brief" aria-label="Approval effect">
          {pendingApprovalBrief(action)}
        </div>
      )}
      {canDecide && (
        <div className="approval-actions">
          <button
            className="approval-btn approve"
            type="button"
            onClick={() => decide('approve')}
            disabled={!!busy}
          >
            <Check size={14} />
            {busy === 'approve' ? 'Applying' : 'Approve'}
          </button>
          <button
            className="approval-btn reject"
            type="button"
            onClick={() => decide('reject')}
            disabled={!!busy}
          >
            <X size={14} />
            {busy === 'reject' ? 'Rejecting' : 'Reject'}
          </button>
        </div>
      )}
      {pullRequest.url && (
        <div className="pr-plan ready">
          <div className="pr-plan-head">
            <span>GitHub PR created</span>
            <small>{pullRequest.head_branch || git.branch_name || 'branch'}</small>
          </div>
          <small>{pullRequest.url}</small>
        </div>
      )}
      {diff && (
        <button
          className="diff-toggle"
          type="button"
          onClick={() => setExpanded((value) => !value)}
          aria-expanded={expanded}
          aria-controls={diffId}
          aria-label={`Toggle diff preview for ${action.id}`}
        >
          {expanded ? <CaretDown size={13} /> : <CaretRight size={13} />}
          <GitDiff size={14} />
          Diff preview
        </button>
      )}
      {expanded && diff && <pre className="diff-preview" id={diffId}>{diff}</pre>}
      {canCommit && (
        <form className="commit-form" onSubmit={commit}>
          <label htmlFor={`commit-${action.id}`}>Commit message</label>
          <div className="commit-row">
            <input
              id={`commit-${action.id}`}
              value={commitMessage}
              onChange={(e) => {
                setCommitEdited(true)
                setCommitMessage(e.target.value)
              }}
              maxLength={120}
              placeholder={`${agentName || APP_NAME}: approved patch`}
              disabled={!!busy}
            />
            <button
              className="approval-btn commit"
              aria-label="Commit approved patch"
              type="submit"
              disabled={!!busy}
            >
              <GitCommit size={14} />
              {busy === 'commit' ? 'Committing' : 'Commit'}
            </button>
          </div>
        </form>
      )}
      {canPlanPr && (
        <div className={`approval-actions ${prState.canCreate && !pullRequest.url ? '' : 'single'}`}>
          <button
            className="approval-btn commit"
            aria-label="Prepare pull request plan"
            type="button"
            onClick={planPullRequest}
            disabled={!!busy}
          >
            <GitPullRequest size={14} />
            {busy === 'pr' ? 'Planning' : 'PR plan'}
          </button>
          {prState.canCreate && !pullRequest.url && (
            <button
              className="approval-btn approve"
              aria-label="Create GitHub pull request"
              type="button"
              onClick={createPullRequest}
              disabled={!!busy}
            >
              <ArrowUpRight size={14} />
              {busy === 'pr-create' ? 'Creating' : 'Create PR'}
            </button>
          )}
        </div>
      )}
      {prPlan && (
        <div className={`pr-plan ${prPlan.ready ? 'ready' : 'blocked'}`}>
          <div className="pr-plan-head">
            <span>{prState.title}</span>
            <small>{prPlan.head_branch || 'no branch'}</small>
          </div>
          {prState.detail && <p>{prState.detail}</p>}
          <PullRequestReadiness plan={prPlan} />
          {(prPlan.pull_request_url || prPlan.web_url) && <small>{prPlan.pull_request_url || prPlan.web_url}</small>}
          {(prPlan.blockers || []).map((blocker) => (
            <p key={blocker}>{blocker}</p>
          ))}
          {(prPlan.commands || []).slice(0, 2).map((command) => (
            <code key={command}>{command}</code>
          ))}
        </div>
      )}
      {error && <div className="approval-error" role="alert">{error}</div>}
    </div>
  )
}

function AgentTeamPanel({ runs = [], onCancelAgentRun }) {
  const latest = runs?.[0]
  const [cancelBusy, setCancelBusy] = useState(false)
  const [cancelError, setCancelError] = useState('')
  const exchanges = (latest?.exchanges || []).slice(-3)
  const control = latest?.metadata?.control || {}
  const controlResult = latest?.metadata?.control_result || {}
  const running = latest?.status === 'running'
  const roles = latest?.steps?.map((step) => step.role) || []
  async function cancelRun() {
    if (!latest?.id || cancelBusy) return
    setCancelBusy(true)
    setCancelError('')
    try {
      await onCancelAgentRun?.(latest.id)
    } catch (err) {
      setCancelError(err.message || 'Could not cancel agent run')
    } finally {
      setCancelBusy(false)
    }
  }
  return (
    <div>
      <p className="section-lab">Agent team</p>
      <div className="agent-team" data-testid="agent-team">
        <div className="agent-team-head">
          <GitBranch size={16} />
          <div>
            <b>{latest ? latest.route?.replace(/_/g, ' ') : 'ready'}</b>
            <small>{latest ? latest.status : 'no active run'}</small>
          </div>
          {running && (
            <button
              className="agent-cancel"
              type="button"
              onClick={cancelRun}
              disabled={cancelBusy}
            >
              <X size={13} />
              {cancelBusy ? 'Cancelling' : 'Cancel'}
            </button>
          )}
        </div>
        {latest && (
          <div className="agent-control-trace">
            {typeof control.max_steps === 'number' && <span>budget {latest.steps?.length || 0}/{control.max_steps}</span>}
            {typeof control.timeout_seconds === 'number' && <span>timeout {control.timeout_seconds}s</span>}
            {controlResult.status && <span>{String(controlResult.status).replace(/_/g, ' ')}</span>}
          </div>
        )}
        {latest && (roles.length > 0 || exchanges.length > 0) && (
          <details className="agent-trace-details">
            <summary>
              <span>Agent trace</span>
              <small>{roles.length} roles · {exchanges.length} handoff{exchanges.length === 1 ? '' : 's'}</small>
            </summary>
            {roles.length > 0 && (
              <div className="agent-role-grid">
                {roles.map((role) => {
                  const step = latest?.steps?.find((item) => item.role === role)
                  return (
                    <div className={`agent-role ${step?.status || 'idle'}`} key={role}>
                      <span>{role}</span>
                      <small>{step?.status || 'idle'}</small>
                    </div>
                  )
                })}
              </div>
            )}
            {exchanges.length > 0 && (
              <div className="agent-exchanges">
                {exchanges.map((exchange) => (
                  <div className="agent-exchange" key={exchange.id}>
                    <span className="mono">
                      {exchange.from_role} to {exchange.to_role}
                    </span>
                    <b>{exchange.title}</b>
                  </div>
                ))}
              </div>
            )}
          </details>
        )}
        {latest ? (
          <div className="agent-run-summary">
            <p title={latest.summary}>{displayText(latest.summary)}</p>
            {latest.action_id && <small className="mono">action {latest.action_id}</small>}
            {cancelError && <small className="approval-error" role="alert">{cancelError}</small>}
          </div>
        ) : (
          <div className="agent-run-summary muted">
            <p>Coordinator, Meeting, Memory, Code, Review, Test, and Git agents will appear here after a run.</p>
          </div>
        )}
      </div>
    </div>
  )
}

const EXTERNAL_AGENT_MODES = [
  { value: 'review', label: 'review' },
  { value: 'patch', label: 'patch' },
  { value: 'test', label: 'test' },
  { value: 'explain', label: 'explain' },
]

const AUTO_EXTERNAL_AGENT_ID = 'auto'

const CUSTOM_EXTERNAL_AGENT_MODEL = '__custom__'

const EXTERNAL_AGENT_PROVIDER_LABELS = {
  claude: 'Claude Code',
  codex: 'OpenAI Codex',
  cursor: 'Cursor',
}

function externalAgentDisplayLabel(agent = {}) {
  const raw = agent.label || agent.provider_label || agent.provider || ''
  const normalized = String(raw).trim()
  if (!normalized) return ''
  return EXTERNAL_AGENT_PROVIDER_LABELS[normalized] || normalized
}

const WORKDASH_ASSIGNMENT_PREVIEW_LIMIT = 3

function setupCountLabel(count) {
  const value = Math.max(0, Number(count) || 0)
  return `${value} ${value === 1 ? 'needs' : 'need'} setup`
}

function providerModeReadiness(provider, mode) {
  const readiness = provider?.mode_readiness?.[mode]
  if (readiness) return readiness
  if (!provider?.connected) {
    return {
      ready: false,
      reason: 'not_connected',
      detail: 'Connect this provider before assigning work.',
      severity: 'warning',
    }
  }
  return {
    ready: false,
    reason: 'unsupported_mode',
    detail: `${provider.label} does not support ${mode} yet.`,
    severity: 'warning',
  }
}

function readinessChipClass(readiness) {
  if (!readiness?.ready) return 'blocked'
  return readiness.severity === 'warning' ? 'warning' : 'ready'
}

function readinessSummary(readiness, mode) {
  const reason = String(readiness?.reason || 'ready').replace(/_/g, ' ')
  const execution = readiness?.execution_mode ? ` · ${String(readiness.execution_mode).replace(/_/g, ' ')}` : ''
  const cli = readiness?.cli_available === false ? ' · CLI missing' : ''
  return `${mode}: ${reason}${execution}${cli}`
}

function providerPreflight(provider) {
  if (provider?.preflight) return provider.preflight
  const modeEntries = Object.entries(provider?.mode_readiness || {})
  const blockers = modeEntries
    .filter(([, readiness]) => readiness && readiness.ready === false)
    .map(([, readiness]) => readiness.detail)
    .filter(Boolean)
  const warnings = modeEntries
    .filter(([, readiness]) => readiness?.ready && readiness.severity === 'warning')
    .map(([, readiness]) => readiness.detail)
    .filter(Boolean)
  const readyModes = modeEntries.filter(([, readiness]) => readiness?.ready).length
  return {
    state: blockers.length ? (readyModes ? 'degraded' : 'blocked') : (warnings.length ? 'warning' : 'ready'),
    summary: blockers.length
      ? `${readyModes}/${modeEntries.length || 4} modes ready; ${blockers.length} blocker${blockers.length === 1 ? '' : 's'}.`
      : `Ready for ${readyModes || 0}/${modeEntries.length || 4} modes.`,
    blockers,
    warnings,
    evidence: {
      tokens_returned_to_browser: false,
      preview_first_policy: 'required_for_patch',
    },
  }
}

function preflightClass(preflight) {
  if (preflight?.state === 'ready') return 'ready'
  if (preflight?.state === 'warning') return 'warning'
  return 'blocked'
}

function providerSetupGuide(provider) {
  if (provider?.setup_guide) return provider.setup_guide
  return {
    state: provider?.connected ? 'ready' : 'not_connected',
    recommended_path: provider?.connected ? 'connected' : 'local_cli_for_code_changes',
    next_step: provider?.connected
      ? 'Provider is connected. Use patch mode only when local CLI is ready.'
      : 'Connect local CLI for code-changing work, or API/OAuth for read-only modes.',
    steps: [
      {
        id: 'connect_local_cli',
        label: 'Connect local CLI',
        action: 'connect_local_cli',
        recommended: true,
      },
    ],
    constraints: [
      'Patch mode proposes a diff and waits for approval.',
    ],
  }
}

export function externalAgentCapabilityBoundary(provider) {
  const method = provider?.auth_method || ''
  if (method === 'local_cli') {
    return {
      label: 'native agent',
      detail: 'Patch work runs the local CLI in an isolated workspace and still needs approval.',
    }
  }
  if (method === 'oauth' || method === 'api_key') {
    return {
      label: 'read-only credential',
      detail: 'OAuth/API can explain, review, or plan tests; patch work still needs local CLI.',
    }
  }
  if ((provider?.auth_methods || []).includes('local_cli')) {
    return {
      label: 'local CLI needed',
      detail: 'Connect the local coding-agent CLI for code-changing work.',
    }
  }
  return {
    label: 'read-only only',
    detail: 'This provider is not configured for patch-producing code work.',
  }
}

export function externalAgentPanelBoundary() {
  return 'Code-changing work requires a local CLI agent already logged in on this machine. OAuth/API credentials are read-only; do not paste passwords, tokens, or browser cookies.'
}

function defaultCliCommand(providerId) {
  if (providerId === 'claude') return 'claude'
  if (providerId === 'cursor') return 'cursor-agent'
  if (providerId === 'local') return 'local-agent'
  return 'codex'
}

function defaultCliTemplate(providerId) {
  if (providerId === 'codex') return ''
  return `${defaultCliCommand(providerId)} --workspace {workspace} --prompt {prompt} --model {model}`
}

function externalAgentUserNextStep(provider) {
  const patchDetail = String(providerModeReadiness(provider, 'patch')?.detail || '')
  if (patchDetail && !/adapter|app\.py|configured workspace/i.test(patchDetail)) return patchDetail
  const setupGuide = providerSetupGuide(provider)
  if (setupGuide.next_step) return setupGuide.next_step
  const preflight = providerPreflight(provider)
  return preflight.blockers?.[0] || preflight.warnings?.[0] || preflight.summary
}

const EMPTY_EXTERNAL_AGENT_PROVIDERS = []

export function normalizeExternalAgentProviders(providers = []) {
  if (Array.isArray(providers)) return providers
  if (Array.isArray(providers?.providers)) return providers.providers
  return EMPTY_EXTERNAL_AGENT_PROVIDERS
}

export function externalAgentPanelSummary(providers = []) {
  const rows = normalizeExternalAgentProviders(providers)
  const total = rows.length
  if (!total) {
    return {
      tone: 'blocked',
      title: 'No coding agents',
      meta: 'connect one',
      detail: 'Connect Codex, Claude Code, Cursor, or a local CLI before assigning code work.',
    }
  }
  const connected = rows.filter((provider) => provider.connected)
  const patchReady = connected.filter((provider) => providerModeReadiness(provider, 'patch').ready)
  const readReady = connected.filter((provider) => (
    ['review', 'test', 'explain'].some((mode) => providerModeReadiness(provider, mode).ready)
  ))
  const firstBlocked = rows.find((provider) => provider.connected && !providerModeReadiness(provider, 'patch').ready)
    || rows.find((provider) => !provider.connected)
  const blocker = firstBlocked ? externalAgentUserNextStep(firstBlocked) : ''

  if (!connected.length) {
    return {
      tone: 'blocked',
      title: 'Connect a coding agent',
      meta: `0/${total} connected`,
      detail: blocker || 'Use local CLI for code-changing work; OAuth/API can support read-only modes.',
    }
  }
  if (!patchReady.length) {
    return {
      tone: 'warning',
      title: 'Read-only agent setup',
      meta: `${readReady.length} read-only · ${connected.length}/${total} connected`,
      detail: blocker || 'Patch work needs a local CLI connection. Read-only review and explain can still run.',
    }
  }
  return {
    tone: 'ready',
    title: 'Patch workflow ready',
    meta: `${patchReady.length} patch ready · ${connected.length}/${total} connected`,
    detail: 'Use Work dashboard to assign tasks. Patch output still requires approval before files change.',
  }
}

function ExternalAgentsPanel({
  providers = [],
  onStartOAuth,
  onConnectLocalCli,
  onRunExternalAgent,
  onRecommendExternalAgent,
}) {
  const providerRows = normalizeExternalAgentProviders(providers)
  const [busy, setBusy] = useState('')
  const [status, setStatus] = useState('')
  const [prompt, setPrompt] = useState('review dashboard')
  const [runMode, setRunMode] = useState('review')
  const [modelByProvider, setModelByProvider] = useState({})
  const [customModelByProvider, setCustomModelByProvider] = useState({})
  const [expandedCliProvider, setExpandedCliProvider] = useState('')
  const [cliDraftByProvider, setCliDraftByProvider] = useState({})
  const [recommendation, setRecommendation] = useState(null)

  useEffect(() => {
    setModelByProvider((prev) => {
      const next = { ...prev }
      for (const provider of providerRows) {
        if (!next[provider.provider]) {
          next[provider.provider] = provider.default_model || provider.supported_models?.[0] || ''
        }
      }
      return next
    })
    setCliDraftByProvider((prev) => {
      const next = { ...prev }
      for (const provider of providerRows) {
        const existing = next[provider.provider] || {}
        next[provider.provider] = {
          command: existing.command || provider.local_cli_command || defaultCliCommand(provider.provider),
          command_template: existing.command_template || provider.local_cli_command_template || defaultCliTemplate(provider.provider),
          account_label: existing.account_label || provider.account_label || `${provider.label} local CLI`,
        }
      }
      return next
    })
  }, [providerRows])

  async function act(key, fn) {
    if (busy) return
    setBusy(key)
    setStatus('')
    try {
      const result = await fn()
      if (result?.authorization_url) {
        setStatus(`${result.provider}: ${result.mode} OAuth URL prepared`)
      } else if (result?.action_id) {
        setStatus(`${result.provider}: ${result.model || 'default'} pending approval ${result.action_id}`)
      } else if (result?.provider_run_id) {
        setStatus(`${result.provider}: ${result.model || 'default'} ${result.mode} ${result.status}`)
      } else {
        setStatus('Connected')
      }
    } catch (err) {
      setStatus(err.message || 'External agent action failed')
    } finally {
      setBusy('')
    }
  }
  async function recommend() {
    if (busy || !prompt.trim() || !onRecommendExternalAgent) return
    setBusy('recommend')
    setStatus('')
    try {
      const result = await onRecommendExternalAgent(prompt, { mode: runMode })
      setRecommendation(result)
      setRunMode(result.mode || runMode)
      if (result.provider && result.model) {
        setModelByProvider((prev) => ({
          ...prev,
          [result.provider]: result.model,
        }))
      }
      setStatus(`${result.label}: ${String(result.task_kind || '').replace(/_/g, ' ')} routed to ${result.mode}`)
    } catch (err) {
      setRecommendation(null)
      setStatus(err.message || 'Agent recommendation failed')
    } finally {
      setBusy('')
    }
  }
  function updateCliDraft(providerId, patch) {
    setCliDraftByProvider((prev) => ({
      ...prev,
      [providerId]: {
        command: defaultCliCommand(providerId),
        command_template: defaultCliTemplate(providerId),
        account_label: '',
        ...(prev[providerId] || {}),
        ...patch,
      },
    }))
  }
  const panelSummary = externalAgentPanelSummary(providerRows)
  return (
    <div>
      <p className="section-lab">Connected agents</p>
      <div className="external-agent-panel">
        <div className={`external-agent-summary ${panelSummary.tone}`} aria-label="Coding agent setup summary" title={panelSummary.detail}>
          <div>
            {panelSummary.tone === 'ready' ? <CheckCircle size={13} /> : <WarningCircle size={13} />}
            <span>{panelSummary.title}</span>
            <b>{panelSummary.meta}</b>
          </div>
          <small>{panelSummary.detail}</small>
        </div>
        <small className="external-agent-panel-boundary">{externalAgentPanelBoundary()}</small>
        {!onStartOAuth && !onConnectLocalCli && !onRunExternalAgent && (
          <small className="agent-setup-readonly">Coding agent setup and runs are read-only for this role.</small>
        )}
        {providerRows.map((provider) => {
          const runReadiness = providerModeReadiness(provider, runMode)
          const runBlocked = !onRunExternalAgent || !provider.connected || !runReadiness.ready || !prompt.trim()
          const selectedModel = modelByProvider[provider.provider] || provider.default_model || provider.supported_models?.[0] || ''
          const customModel = customModelByProvider[provider.provider] || ''
          const effectiveModel = selectedModel === CUSTOM_EXTERNAL_AGENT_MODEL ? customModel.trim() : selectedModel
          const modelOptions = provider.supported_models?.length
            ? provider.supported_models
            : [provider.default_model || 'provider-default']
          const modelBlocked = selectedModel === CUSTOM_EXTERNAL_AGENT_MODEL && !customModel.trim()
          const cliDraft = cliDraftByProvider[provider.provider] || {
            command: provider.local_cli_command || defaultCliCommand(provider.provider),
            command_template: provider.local_cli_command_template || defaultCliTemplate(provider.provider),
            account_label: provider.account_label || `${provider.label} local CLI`,
          }
          const preflight = providerPreflight(provider)
          const setupGuide = providerSetupGuide(provider)
          const primaryBlocker = preflight.blockers?.[0]
          const primaryWarning = preflight.warnings?.[0]
          const primarySetupMessage = externalAgentUserNextStep(provider)
          const capabilityBoundary = externalAgentCapabilityBoundary(provider)
          const recommendedSteps = (setupGuide.steps || []).filter((step) => step.recommended).slice(0, 2)
          const authMethods = provider.auth_methods || ['oauth', 'api_key', 'local_cli']
          const supportsOAuth = authMethods.includes('oauth')
          const supportsLocalCli = authMethods.includes('local_cli')
          const canStartOAuth = supportsOAuth && onStartOAuth
          const canConfigureCli = supportsLocalCli && onConnectLocalCli
          const cliExpanded = expandedCliProvider === provider.provider
          const recommended = recommendation?.provider === provider.provider
          return (
            <div className={`external-agent-row ${provider.connected ? 'connected' : 'disconnected'} ${recommended ? 'recommended' : ''}`} key={provider.provider}>
              <div>
                <b>{provider.label}{recommended ? <span>Recommended</span> : null}</b>
                <small>
                  {provider.connected
                    ? `${provider.auth_method?.replace(/_/g, ' ')} ${provider.token_preview || ''}`.trim()
                    : 'not connected'}
                </small>
                <small
                  className={`external-agent-primary-setup ${preflightClass(preflight)}`}
                  title={primarySetupMessage}
                >
                  {primarySetupMessage}
                </small>
                <small className="external-agent-capability-boundary" title={capabilityBoundary.detail}>
                  <b>{capabilityBoundary.label}</b> {capabilityBoundary.detail}
                </small>
                <details className={`external-agent-details ${preflightClass(preflight)}`}>
                  <summary>
                    <span>Setup details</span>
                    <small>{preflight.state}</small>
                  </summary>
                  <div className="external-agent-modes" aria-label={`${provider.label} mode readiness`}>
                    {EXTERNAL_AGENT_MODES.map((item) => {
                      const readiness = providerModeReadiness(provider, item.value)
                      return (
                        <span
                          className={readinessChipClass(readiness)}
                          title={readiness.detail}
                          key={item.value}
                        >
                          {item.label}
                        </span>
                      )
                    })}
                  </div>
                  <small
                    className={`external-agent-readiness ${readinessChipClass(runReadiness)}`}
                    title={runReadiness.detail}
                  >
                    {readinessSummary(runReadiness, runMode)}
                  </small>
                  <div className={`external-agent-preflight ${preflightClass(preflight)}`}>
                    <div>
                      {preflight.state === 'ready' ? <CheckCircle size={13} /> : <WarningCircle size={13} />}
                      <span>Runtime preflight</span>
                      <b>{preflight.state}</b>
                    </div>
                    <small title={primaryBlocker || primaryWarning || preflight.summary}>
                      {preflight.summary}
                      {primaryBlocker ? ` ${primaryBlocker}` : ''}
                      {!primaryBlocker && primaryWarning ? ` ${primaryWarning}` : ''}
                    </small>
                    <div className="external-agent-preflight-evidence">
                      {preflight.evidence?.env_policy ? <span>{preflight.evidence.env_policy}</span> : null}
                      {preflight.evidence?.local_cli_runtime ? <span>{preflight.evidence.local_cli_runtime}</span> : null}
                      {preflight.evidence?.tokens_returned_to_browser === false ? <span>server-side tokens</span> : null}
                      {preflight.evidence?.preview_first_policy ? <span>{String(preflight.evidence.preview_first_policy).replace(/_/g, ' ')}</span> : null}
                    </div>
                  </div>
                  <div className="external-agent-setup-guide">
                    <div>
                      <ClipboardText size={13} />
                      <span>Next setup</span>
                      <b>{String(setupGuide.recommended_path || 'setup').replace(/_/g, ' ')}</b>
                    </div>
                    <small title={setupGuide.next_step}>{setupGuide.next_step}</small>
                    {recommendedSteps.length ? (
                      <div className="external-agent-setup-steps">
                        {recommendedSteps.map((step) => (
                          <span key={step.id}>{step.label}</span>
                        ))}
                      </div>
                    ) : null}
                  </div>
                </details>
                <details className="external-agent-run-settings" open={Boolean(cliExpanded || modelBlocked)}>
                  <summary>
                    <span>Run settings</span>
                    <small>{selectedModel === CUSTOM_EXTERNAL_AGENT_MODEL ? 'custom model' : selectedModel || 'default model'}</small>
                  </summary>
                  <label className="external-agent-model">
                    <span>Model</span>
                    <select
                      value={selectedModel}
                      onChange={(event) => setModelByProvider((prev) => ({
                        ...prev,
                        [provider.provider]: event.target.value,
                      }))}
                      aria-label={`${provider.label} model`}
                    >
                      {modelOptions.map((model) => (
                        <option value={model} key={model}>{model}</option>
                      ))}
                      <option value={CUSTOM_EXTERNAL_AGENT_MODEL}>custom model ID</option>
                    </select>
                  </label>
                  {selectedModel === CUSTOM_EXTERNAL_AGENT_MODEL ? (
                    <input
                      className="external-agent-custom-model"
                      value={customModel}
                      onChange={(event) => setCustomModelByProvider((prev) => ({
                        ...prev,
                        [provider.provider]: event.target.value,
                      }))}
                      placeholder="exact provider model ID"
                      aria-label={`${provider.label} custom model ID`}
                    />
                  ) : null}
                  {provider.auth_method === 'local_cli' && provider.local_cli_command && (
                    <div className="external-agent-cli-summary" title={provider.local_cli_command_template || provider.local_cli_command}>
                      <Package size={12} />
                      <span>{provider.local_cli_command}</span>
                    </div>
                  )}
                  <div className="external-agent-actions">
                    {canStartOAuth ? (
                      <button
                        type="button"
                        disabled={Boolean(busy)}
                        onClick={() => act(`${provider.provider}:oauth`, () => onStartOAuth(provider.provider))}
                      >
                        OAuth
                      </button>
                    ) : null}
                    {canConfigureCli ? (
                      <button
                        type="button"
                        disabled={Boolean(busy)}
                        onClick={() => setExpandedCliProvider((value) => value === provider.provider ? '' : provider.provider)}
                        aria-expanded={cliExpanded}
                      >
                        CLI setup
                      </button>
                    ) : null}
                    {onRunExternalAgent ? (
                      <button
                        type="button"
                        disabled={Boolean(busy) || runBlocked || modelBlocked}
                        title={modelBlocked ? 'Enter a custom model ID.' : (!runReadiness.ready ? runReadiness.detail : undefined)}
                        onClick={() => act(
                          `${provider.provider}:run`,
                          () => onRunExternalAgent(provider.provider, prompt, runMode, effectiveModel),
                        )}
                      >
                        Run
                      </button>
                    ) : null}
                  </div>
                  {cliExpanded && canConfigureCli && (
                    <form
                      className="external-agent-cli-form"
                      onSubmit={(event) => {
                        event.preventDefault()
                        act(`${provider.provider}:cli`, () => onConnectLocalCli(provider.provider, cliDraft))
                      }}
                    >
                      <label>
                        <span>Command</span>
                        <input
                          value={cliDraft.command}
                          onChange={(event) => updateCliDraft(provider.provider, { command: event.target.value })}
                          placeholder={defaultCliCommand(provider.provider)}
                          aria-label={`${provider.label} CLI command`}
                        />
                      </label>
                      <label>
                        <span>Template</span>
                        <textarea
                          value={cliDraft.command_template}
                          onChange={(event) => updateCliDraft(provider.provider, { command_template: event.target.value })}
                          placeholder="{workspace} {prompt} {model}"
                          aria-label={`${provider.label} CLI command template`}
                          rows={2}
                        />
                      </label>
                      <div className="external-agent-template-chips" aria-label="Template tokens">
                        {['{workspace}', '{prompt}', '{model}', '{provider_run_id}'].map((token) => (
                          <button
                            type="button"
                            key={token}
                            onClick={() => updateCliDraft(provider.provider, {
                              command_template: `${cliDraft.command_template || ''} ${token}`.trim(),
                            })}
                          >
                            {token}
                          </button>
                        ))}
                      </div>
                      <div className="external-agent-cli-footer">
                        <small>Runs in a temporary workspace. Patch output still needs approval.</small>
                        <button type="submit" disabled={Boolean(busy) || !cliDraft.command.trim()}>
                          {busy === `${provider.provider}:cli` ? 'Saving' : 'Save CLI'}
                        </button>
                      </div>
                    </form>
                  )}
                </details>
              </div>
            </div>
          )
        })}
        <details className="external-agent-run-details">
          <summary>
            <span>Advanced run control</span>
            <small>Work dashboard is the main flow</small>
          </summary>
          <div className="external-agent-prompt">
            <Code size={14} />
            <input
              value={prompt}
              onChange={(event) => {
                setPrompt(event.target.value)
                setRecommendation(null)
              }}
              aria-label="External agent prompt"
              placeholder={`Ask external agent to ${runMode}`}
            />
            <select
              value={runMode}
              onChange={(event) => {
                setRunMode(event.target.value)
                setRecommendation(null)
              }}
              aria-label="External agent mode"
            >
              {EXTERNAL_AGENT_MODES.map((item) => (
                <option value={item.value} key={item.value}>{item.label}</option>
              ))}
            </select>
            <button
              type="button"
              disabled={Boolean(busy) || !prompt.trim() || !onRecommendExternalAgent}
              onClick={recommend}
            >
              {busy === 'recommend' ? 'Checking' : 'Recommend'}
            </button>
          </div>
          {recommendation && (
            <div className={`external-agent-recommendation ${recommendation.ready ? 'ready' : 'blocked'}`}>
              <div>
                {recommendation.ready ? <CheckCircle size={13} /> : <WarningCircle size={13} />}
                <span>{recommendation.label}</span>
                <b>{recommendation.model}</b>
              </div>
              <small>
                {String(recommendation.task_kind || '').replace(/_/g, ' ')} · {recommendation.mode}
                {recommendation.approval_required ? ' · approval required' : ' · read only'}
                {recommendation.blockers?.length ? ` · ${recommendation.blockers[0]}` : ''}
              </small>
            </div>
          )}
        </details>
        <p className="external-agent-note">Read-only modes answer directly. Patch mode proposes a diff and still requires approval before workspace writes.</p>
        {status && <small className="external-agent-status">{status}</small>}
      </div>
    </div>
  )
}

function dashboardStatusLabel(status) {
  return String(status || 'noted').replace(/_/g, ' ')
}

function isTerminalAssignment(status) {
  return ['completed', 'failed', 'cancelled'].includes(status)
}

function assignmentFilterItems(assignments, filter) {
  if (filter === 'open') {
    return assignments.filter((assignment) => !isTerminalAssignment(assignment.status))
  }
  if (filter === 'done') {
    return assignments.filter((assignment) => isTerminalAssignment(assignment.status))
  }
  return assignments
}

function queueAssignmentKey(assignment) {
  return [
    String(assignment.task || '').trim().toLowerCase(),
    assignment.agent_id || '',
    assignment.agent_label || '',
    assignment.agent_kind || '',
    assignment.mode || '',
    assignment.status || '',
  ].join('::')
}

export function compactQueueAssignments(assignments = []) {
  const compacted = []
  const groups = new Map()
  assignments.forEach((assignment) => {
    const key = queueAssignmentKey(assignment)
    const existing = groups.get(key)
    if (!existing) {
      const row = {
        ...assignment,
        duplicateCount: 1,
        duplicateIds: [assignment.id].filter(Boolean),
      }
      groups.set(key, row)
      compacted.push(row)
      return
    }
    existing.duplicateCount += 1
    if (assignment.id) existing.duplicateIds.push(assignment.id)
  })
  return compacted
}

function shortId(value) {
  if (!value) return ''
  const text = String(value)
  return text.length > 10 ? text.slice(0, 10) : text
}

function assignmentAuditChips(assignment) {
  const meta = assignment.metadata || {}
  const chips = []
  if (assignment.requested_by_name) chips.push(`Req ${assignment.requested_by_name}`)
  if (meta.recommended_provider) chips.push(`Auto ${meta.recommended_provider}`)
  if (meta.model) chips.push(`Model ${meta.model}`)
  if (meta.dispatched_by_name) chips.push(`Run ${meta.dispatched_by_name}`)
  if (meta.cancelled_by_name) chips.push(`Cancel ${meta.cancelled_by_name}`)
  if (meta.retried_by_name) chips.push(`Retry ${meta.retried_by_name}`)
  if (meta.updated_by_name && !meta.cancelled_by_name) chips.push(`Update ${meta.updated_by_name}`)
  if (meta.retry_of) chips.push(`From ${shortId(meta.retry_of)}`)
  if (assignment.action_id) chips.push(`Action ${shortId(assignment.action_id)}`)
  if (assignment.run_id) chips.push(`Job ${shortId(assignment.run_id)}`)
  return chips.slice(0, 5)
}

function assignmentRouteSummary(assignment) {
  const meta = assignment.metadata || {}
  if (!meta.recommended_provider && !meta.recommendation_error) return null
  const provider = meta.recommended_label || meta.recommended_provider || 'Auto route'
  const mode = meta.recommended_mode || assignment.mode
  const model = meta.recommended_model || meta.model || 'default model'
  const confidence = Number(meta.recommendation_confidence)
  const confidenceLabel = Number.isFinite(confidence) ? ` · ${Math.round(confidence * 100)}%` : ''
  const blockers = Array.isArray(meta.recommendation_blockers) ? meta.recommendation_blockers : []
  const detail = meta.recommendation_error || blockers[0] || meta.recommendation_reason || 'Provider recommendation ready.'
  return {
    ready: meta.recommendation_ready !== false && !meta.recommendation_error && blockers.length === 0,
    label: `${provider} · ${mode} · ${model}${confidenceLabel}`,
    detail,
  }
}

function assignmentAgeMinutes(assignment) {
  const value = assignment.updated_at || assignment.created_at
  const timestamp = value ? Date.parse(value) : Number.NaN
  if (!Number.isFinite(timestamp)) return 0
  return Math.max(0, Math.floor((Date.now() - timestamp) / 60000))
}

function assignmentAgeLabel(assignment) {
  const minutes = assignmentAgeMinutes(assignment)
  if (minutes < 1) return 'Now'
  if (minutes < 60) return `${minutes}m`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h`
  return `${Math.floor(hours / 24)}d`
}

function assignmentIsStale(assignment) {
  return !isTerminalAssignment(assignment.status) && assignmentAgeMinutes(assignment) >= 30
}

function queueHealthSummary({ assignments, approvals, providers, providerReadinessById }) {
  const active = assignments.filter((assignment) => !isTerminalAssignment(assignment.status))
  const stale = active.filter((assignment) => assignmentIsStale(assignment))
  const blockedAssignments = active.filter((assignment) => {
    if (assignment.agent_kind !== 'external') return false
    return !readinessForQueuedAssignment(assignment, providerReadinessById).ready
  })
  const blockedProviders = (providers || []).filter((provider) => {
    if (!provider.connected) return false
    return Object.values(provider.mode_readiness || {}).some((readiness) => readiness && readiness.ready === false)
  })
  const pendingApprovals = approvals.filter((item) => item.status === 'pending_approval' || !item.status).length
  return [
    {
      label: 'Open',
      value: active.length,
      tone: active.length ? 'info' : 'neutral',
      detail: `${active.length} assignment${active.length === 1 ? '' : 's'} still open.`,
    },
    {
      label: 'Stale',
      value: stale.length,
      tone: stale.length ? 'warning' : 'neutral',
      detail: `${stale.length} open assignment${stale.length === 1 ? '' : 's'} older than 30 minutes.`,
    },
    {
      label: 'Blocked',
      value: blockedAssignments.length + blockedProviders.length,
      tone: blockedAssignments.length || blockedProviders.length ? 'danger' : 'neutral',
      detail: `${blockedAssignments.length} assignment${blockedAssignments.length === 1 ? '' : 's'} and ${blockedProviders.length} provider mode${blockedProviders.length === 1 ? '' : 's'} need attention.`,
    },
    {
      label: 'Approvals',
      value: pendingApprovals,
      tone: pendingApprovals ? 'warning' : 'neutral',
      detail: `${pendingApprovals} patch approval${pendingApprovals === 1 ? '' : 's'} waiting.`,
    },
  ]
}

function normalizeQueueHealth(items) {
  return items.map((item) => ({
    label: item.label,
    value: Number(item.value || 0),
    tone: item.tone || 'neutral',
    detail: item.detail || `${item.value || 0} ${item.label}`,
  }))
}

function queueHealthValue(items, label) {
  const item = items.find((entry) => entry.label?.toLowerCase() === label.toLowerCase())
  const value = Number(item?.value)
  return Number.isFinite(value) ? value : 0
}

function visibleQueueHealthSummary(items) {
  return items.filter((item) => item.label !== 'Open' && Number(item.value || 0) > 0)
}

export function shouldShowQueueHealth(visibleItems = [], readinessDetail = '') {
  const activeItems = visibleItems.filter((item) => Number(item.value || 0) > 0)
  if (!activeItems.length) return false
  if (activeItems.length > 1) return true
  const item = activeItems[0]
  const detail = String(readinessDetail || '').toLowerCase()
  const label = String(item.label || '').toLowerCase()
  const itemDetail = String(item.detail || '').toLowerCase()
  return Boolean(!detail || (!detail.includes(label) && detail !== itemDetail))
}

export function agentSetupSummary(providers = [], runs = []) {
  if (providers == null) return 'checking code-writing'
  const providerRows = normalizeExternalAgentProviders(providers)
  const total = providerRows.length
  const connectedRows = providerRows.filter((provider) => provider.connected)
  const connected = connectedRows.length
  const needsConnection = Math.max(0, total - connected)
  const running = (Array.isArray(runs) ? runs : []).filter((run) => run.status === 'running').length
  const patchReady = connectedRows.filter((provider) => providerModeReadiness(provider, 'patch')?.ready).length
  const readOnlyReady = connectedRows.filter((provider) => (
    !providerModeReadiness(provider, 'patch')?.ready
    && ['review', 'test', 'explain'].some((mode) => providerModeReadiness(provider, mode)?.ready)
  )).length

  if (!total || !connected) return 'connect coding agent'
  const setupSuffix = needsConnection > 0
    ? ` · ${needsConnection} ${needsConnection === 1 ? 'needs' : 'need'} setup`
    : ''
  const prefix = running > 0 ? `${running} running · ` : ''
  if (patchReady > 0) return `${prefix}${patchReady} patch ready${setupSuffix}`
  if (readOnlyReady > 0) return `${prefix}${readOnlyReady} read-only · connect local CLI${setupSuffix}`
  return `${prefix}connect local CLI${setupSuffix}`
}

export function workDashboardNextStep({
  approvalsCount = 0,
  activeAssignmentsCount = 0,
  availableAgentCount = 0,
  contextCount = 0,
  loadedQueueIsPartial = false,
} = {}) {
  if (approvalsCount > 0) {
    return {
      title: 'Review pending patch',
      lines: ['Approve or reject the diff before starting more code-changing work.'],
    }
  }
  if (loadedQueueIsPartial) {
    return {
      title: 'Queue preview ready',
      lines: ['Open queue details to run or cancel waiting work.'],
    }
  }
  if (activeAssignmentsCount > 0) {
    return {
      title: 'Run queued work',
      lines: ['Run ready assignments, or cancel stale ones before assigning more work.'],
    }
  }
  if (!availableAgentCount) {
    return {
      title: 'Use typed commands',
      lines: [
        'Ask memory or repo questions. Connect a coding agent later for review, test, or patch work.',
      ],
    }
  }
  if (contextCount > 0) {
    return {
      title: 'Session context ready',
      lines: ['Ask memory questions, review pending items, or assign a focused task.'],
    }
  }
  return {
    title: 'Start with a command',
    lines: ['Use text or live meeting first. Assign work only when a coding agent needs a task.'],
  }
}

export function partialQueueSummary(loadedCount = 0, totalCount = 0) {
  const loaded = Math.max(0, Number(loadedCount) || 0)
  const total = Math.max(loaded, Number(totalCount) || 0)
  const folded = Math.max(0, total - loaded)
  if (!folded) return `${loaded} shown`
  return `${loaded} shown · ${folded} more`
}

export function workDashboardDelegateMeta({
  queueRowsSyncing = false,
  loadedQueueIsPartial = false,
  activeAssignmentsCount = 0,
  agentAssignmentsCount = 0,
  availableAgentCount = 0,
  connectedProviderCount = 0,
  totalProviderCount = 0,
  patchReadyProviderCount = 0,
} = {}) {
  if (totalProviderCount > 0) {
    const connectionText = `${connectedProviderCount}/${totalProviderCount} connected`
    const patchText = patchReadyProviderCount > 0
      ? `${patchReadyProviderCount} patch ready`
      : connectedProviderCount > 0
        ? `${connectedProviderCount} ${connectedProviderCount === 1 ? 'needs' : 'need'} setup`
        : 'connect agent'
    if (queueRowsSyncing) return `queue syncing · ${connectionText}`
    return `${connectionText} · ${patchText}`
  }
  const agentText = `${availableAgentCount} agent${availableAgentCount === 1 ? '' : 's'}`
  if (queueRowsSyncing) return `queue syncing · ${agentText}`
  if (loadedQueueIsPartial) return `${activeAssignmentsCount} shown · ${agentText}`
  const assignmentText = activeAssignmentsCount
    ? `${activeAssignmentsCount} queued`
    : agentAssignmentsCount
      ? `${agentAssignmentsCount} loaded`
      : 'no queued work'
  return `${assignmentText} · ${agentText}`
}

export function workDashboardDelegateSummaryMeta({
  queueRowsSyncing = false,
  availableAgentCount = 0,
  connectedProviderCount = 0,
  totalProviderCount = 0,
  patchReadyProviderCount = 0,
} = {}) {
  if (queueRowsSyncing) return 'queue syncing'
  if (totalProviderCount > 0) {
    if (patchReadyProviderCount > 0) {
      return `${patchReadyProviderCount} patch ready`
    }
    if (connectedProviderCount > 0) return 'choose agent'
    return 'connect coding agent'
  }
  if (availableAgentCount <= 0) return 'connect coding agent'
  return `${availableAgentCount} agent${availableAgentCount === 1 ? '' : 's'} ready`
}

function readinessForAssignment(agent, mode, agentName = APP_NAME) {
  if (!agent) {
    return { ready: false, detail: 'Select an agent before assigning work.' }
  }
  if (agent.kind !== 'external') {
    return { ready: true, detail: '' }
  }
  if (agent.id === AUTO_EXTERNAL_AGENT_ID) {
    if (mode === 'memory') {
      return { ready: false, detail: `Memory assignments use the internal ${agentName} memory agent.` }
    }
    return {
      ready: true,
      reason: 'recommendation_route',
      detail: `${agentName} will recommend the external provider, mode, and model when the assignment runs.`,
    }
  }
  const readiness = agent.readiness?.[mode]
  if (readiness) {
    return readiness
  }
  return {
    ready: false,
    reason: 'unsupported_mode',
    detail: `${agent.label} does not support ${mode} assignments yet.`,
    severity: 'warning',
  }
}

const PATCH_ASSIGNMENT_BLOCKER = 'Patch work needs a patch-ready local CLI agent.'

export function assignmentReadinessMessage(readiness = {}, mode = '') {
  const detail = displayText(readiness?.detail).trim()
  if (mode === 'patch') {
    if (!detail) return PATCH_ASSIGNMENT_BLOCKER
    if (/local cli|patch-ready|patch ready/i.test(detail)) return detail
    return `${PATCH_ASSIGNMENT_BLOCKER} ${detail}`
  }
  return detail || 'This mode is not ready for the selected agent.'
}

function readinessForQueuedAssignment(assignment, providerReadinessById, agentName = APP_NAME) {
  if (assignment.agent_kind !== 'external') {
    return { ready: true, detail: '' }
  }
  if (assignment.agent_id === AUTO_EXTERNAL_AGENT_ID) {
    if (assignment.mode === 'memory') {
      return { ready: false, detail: `Memory assignments use the internal ${agentName} memory agent.` }
    }
    if (!Object.keys(providerReadinessById).length) {
      return { ready: false, detail: 'Connect at least one external provider before running auto assignment.' }
    }
    return {
      ready: true,
      reason: 'recommendation_route',
      detail: `${agentName} will recommend the external provider at dispatch.`,
    }
  }
  const readiness = providerReadinessById[assignment.agent_id]?.[assignment.mode]
  if (readiness) {
    return readiness
  }
  return {
    ready: false,
    reason: 'unsupported_mode',
    detail: `${assignment.agent_label} does not support ${assignment.mode} assignments yet.`,
    severity: 'warning',
  }
}

function DashboardItem({ item, agentName = APP_NAME }) {
  const files = item.files || []
  const title = displayText(item.title)
  const actor = displayActorName(item.actor_name, agentName, 'system')
  return (
    <div className={`workdash-item ${item.status || 'noted'}`}>
      <div className="workdash-item-main">
        <b title={item.title}>{title}</b>
        <small>
          {actor} · {dashboardStatusLabel(item.status)}
        </small>
      </div>
      {files.length > 0 && (
        <div className="workdash-files">
          {files.slice(0, 3).map((file) => <span key={file}>{file}</span>)}
        </div>
      )}
      {item.metadata?.branch && (
        <span className="workdash-branch">
          <GitBranch size={12} />
          {item.metadata.branch}
        </span>
      )}
    </div>
  )
}

function RailSectionDetails({ title, meta, children, open = false }) {
  const [isOpen, setIsOpen] = useState(open)

  useEffect(() => {
    if (open) setIsOpen(true)
  }, [open])

  return (
    <details
      className="rail-section-details"
      open={isOpen}
      onToggle={(event) => setIsOpen(event.currentTarget.open)}
    >
      <summary>
        <span>{title}</span>
        {meta && <small>{meta}</small>}
      </summary>
      <div className="rail-section-details-body">
        {children}
      </div>
    </details>
  )
}

export function handoffDisplay(handoff, context = {}) {
  if (!handoff) {
    return {
      title: 'Loading handoff',
      lines: ['Loading room handoff and review items.'],
      reviewItems: [],
    }
  }
  const hasVisibleActivity = Number(context.actionCount || 0) > 0
  const lines = Array.isArray(handoff.lines) ? handoff.lines : []
  const defaultEmptyLines = lines.length > 0
    && lines.every((line) => /no teammate activity/i.test(String(line || '')))
  return {
    title: handoff.action_count || handoff.message_count ? 'Since your last check-in' : 'Room memory',
    lines: lines.length && !(defaultEmptyLines && hasVisibleActivity)
      ? lines
      : hasVisibleActivity
        ? ['No handoff summary yet. Review recent agent actions below.']
        : ['No teammate activity to catch up on yet.'],
    reviewItems: handoff.open_review_items || [],
  }
}

export function compactHandoffLines(lines, limit = 2) {
  const uniqueLines = []
  const seen = new Set()
  ;(lines || []).forEach((line) => {
    const raw = String(line || '').trim()
    const text = displayHandoffText(raw)
    const signature = handoffLineSignature(text)
    if (!raw || !text || seen.has(signature)) return
    seen.add(signature)
    uniqueLines.push({ raw, text })
  })
  return {
    visible: uniqueLines.slice(0, limit),
    hidden: uniqueLines.slice(limit),
    duplicateCount: Math.max(0, (lines || []).length - uniqueLines.length),
  }
}

function handoffLineSignature(line) {
  return String(line || '')
    .replace(/\*\*/g, '')
    .replace(/`/g, '')
    .replace(/\s+Route:\s.*$/i, '')
    .toLowerCase()
    .replace(/^[^.?!]*\basked\b[^.?!]*[.?!]\s*/i, '')
    .replace(/^[a-z0-9 _-]{1,40}:\s*/i, '')
    .replace(/[^a-z0-9./_-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 180)
}

function DiagnosticDetails({ title, meta, children, open = false }) {
  const [isOpen, setIsOpen] = useState(open)
  const summaryLabel = meta ? `${title}: ${meta}` : title

  useEffect(() => {
    if (open) setIsOpen(true)
  }, [open])

  return (
    <details
      className="diagnostic-details"
      open={isOpen}
      onToggle={(event) => setIsOpen(event.currentTarget.open)}
    >
      <summary aria-label={summaryLabel}>
        <span>{title}</span>
        {meta && <small>{meta}</small>}
      </summary>
      <div className="diagnostic-details-body">
        {children}
      </div>
    </details>
  )
}

function AgentSetupSubdetails({ title, meta, children, open = false }) {
  const [isOpen, setIsOpen] = useState(open)
  const summaryLabel = meta ? `${title}: ${meta}` : title

  useEffect(() => {
    if (open) setIsOpen(true)
  }, [open])

  return (
    <details
      className="diagnostic-details agent-setup-subdetails"
      open={isOpen}
      onToggle={(event) => setIsOpen(event.currentTarget.open)}
    >
      <summary aria-label={summaryLabel}>
        <span>{title}</span>
        {meta && <small>{meta}</small>}
      </summary>
      <div className="diagnostic-details-body">
        {children}
      </div>
    </details>
  )
}

function externalAgentSetupMeta(providers = []) {
  if (providers == null) return 'checking'
  const rows = normalizeExternalAgentProviders(providers)
  const total = rows.length
  if (!total) return 'connect provider'
  const connected = rows.filter((provider) => provider.connected).length
  const needsConnection = Math.max(0, total - connected)
  const setupNeeded = rows.some((provider) => {
    if (!provider.connected) return true
    return Object.values(provider.mode_readiness || {}).some((readiness) => readiness?.ready === false)
  })
  const connection = needsConnection > 0
    ? `${connected} connected · ${needsConnection} to set up`
    : `${connected}/${total} connected`
  return `${connection}${setupNeeded && needsConnection === 0 ? ' · setup needed' : ''}`
}

function setupSectionAgentMeta(agentMeta = '') {
  return String(agentMeta || '')
    .replace(/\bchecking code-writing\b/g, 'checking code work')
    .replace(/\b(\d+) running · (\d+) connected · (\d+) to set up\b/g, (_, running, connected, setup) => (
      `${running} running · ${connected} agent${connected === '1' ? '' : 's'} connected · ${setup} ${setup === '1' ? 'needs' : 'need'} setup`
    ))
    .replace(/\b(\d+) connected · (\d+) to set up\b/g, (_, connected, setup) => (
      `${connected} agent${connected === '1' ? '' : 's'} connected · ${setup} ${setup === '1' ? 'needs' : 'need'} setup`
    ))
    .replace(/\b(\d+)\/(\d+) connected\b/g, '$1/$2 agents connected')
}

function setupSectionMeta(workspace, providers = [], runs = []) {
  const repoBlocked = workspace?.connected === false
  const repoConnected = workspace?.connected === true
  const agentMeta = setupSectionAgentMeta(agentSetupSummary(providers, runs))
  if (repoBlocked) return `repo not connected · ${agentMeta}`
  if (repoConnected) return `repo ok · ${agentMeta}`
  return agentMeta
}

function SystemSummaryPanel({ workspace, diagnostics }) {
  const wsConnected = Boolean(workspace?.connected)
  const micReady = diagnostics?.getUserMedia !== false
  const recorderReady = diagnostics?.mediaRecorder !== false
  const liveActive = Boolean(diagnostics?.active)
  const setupItems = liveSetupDiagnosticItems(diagnostics)
  const voiceSetupBlocked = setupItems.some((item) => (
    ['page', 'browser-api', 'browser-permission', 'mac-input', 'audio-session', 'recorder'].includes(item.id) && !item.ready
  ))
  const voiceReady = Boolean(micReady && recorderReady && !voiceSetupBlocked)
  const attention = Boolean(diagnostics?.lastErrorKind || !wsConnected || !voiceReady)
  const primarySetupItem = setupItems.find((item) => !item.ready) || setupItems[0]
  const blockedSetupCount = setupItems.filter((item) => !item.ready).length
  const nextStep = diagnostics?.lastErrorMessage
    ? diagnostics.recoveryHint || diagnostics.lastErrorMessage
    : !wsConnected
      ? 'Connect a local repository with VOICEOPS_WORKSPACE before code actions.'
      : !voiceReady
        ? 'Allow microphone access or type commands while voice is unavailable.'
        : liveActive
          ? 'Live meeting is streaming; speaker labels may refine after a short delay.'
          : 'Ready for typed commands, live meeting, and approved code actions.'
  const rows = [
    { label: 'Workspace', value: wsConnected ? 'connected' : 'not connected', ok: wsConnected },
    { label: 'Voice', value: voiceReady ? 'ready' : 'needs setup', ok: voiceReady },
    { label: 'Meeting', value: liveActive ? 'live' : diagnostics?.phase || 'idle', ok: !diagnostics?.lastErrorKind },
    {
      label: 'Last event',
      value: diagnosticsEventLabel(diagnostics?.lastEvent || 'idle'),
      rawValue: diagnostics?.lastEvent || 'idle',
      ok: !diagnostics?.lastErrorKind,
    },
  ]

  return (
    <div className={`system-summary ${attention ? 'attention' : 'ready'}`}>
      <div className="system-summary-head">
        <span>
          {attention ? <WarningCircle size={14} /> : <CheckCircle size={14} />}
          System summary
        </span>
        <small>{attention ? 'needs attention' : 'ready'}</small>
      </div>
      <div className="system-summary-grid">
        {rows.map((row) => (
          <div className={row.ok ? 'ok' : 'warn'} key={row.label}>
            <span>{row.label}</span>
            <b title={row.rawValue && row.rawValue !== row.value ? row.rawValue : undefined}>{row.value}</b>
          </div>
        ))}
      </div>
      <p>{nextStep}</p>
      {primarySetupItem && (
        <div
          className={`live-setup-primary ${primarySetupItem.ready ? 'ok' : 'warn'}`}
          aria-label="Primary live meeting setup issue"
        >
          <span>{primarySetupItem.ready ? <Check size={11} /> : <X size={11} />}{primarySetupItem.label}</span>
          <small>{primarySetupItem.detail}</small>
        </div>
      )}
      {setupItems.length > 1 && (
        <div className="live-setup-checklist" aria-label="Live meeting setup diagnostics">
          <details>
            <summary>
              <span>All setup checks</span>
              <small>{blockedSetupCount ? `${blockedSetupCount} blocked` : 'ready'}</small>
            </summary>
            {setupItems.map((item) => (
              <div className={item.ready ? 'ok' : 'warn'} key={item.id}>
                <span>{item.ready ? <Check size={11} /> : <X size={11} />}{item.label}</span>
                <small>{item.detail}</small>
              </div>
            ))}
          </details>
        </div>
      )}
    </div>
  )
}

export function systemDetailsMeta(workspace, diagnostics = {}) {
  if (diagnostics?.lastErrorKind) return diagnosticsMetaLabel(diagnostics.lastErrorKind)
  if (diagnostics?.active || diagnostics?.captionPreview || diagnostics?.phase === 'live') return 'live active'
  if (workspace?.connected) return 'ready'
  if (workspace?.connected === false) return 'repo not connected'
  return 'checking'
}

export function runtimeDiagnosticsMeta(workspace, diagnostics = {}) {
  if (diagnostics?.lastErrorKind) return diagnosticsMetaLabel(diagnostics.lastErrorKind)
  if (workspace?.connected === false) return 'repo setup'
  if (diagnostics?.active || diagnostics?.captionPreview || diagnostics?.phase === 'live') return 'live active'
  return 'details'
}

export function commandFallbackCopy(workspace, diagnostics = {}) {
  const errorLabel = diagnosticsErrorLabel(diagnostics?.lastErrorKind)
  const voiceIssue = errorLabel === 'Mic error'
    || errorLabel === 'Recorder error'
    || errorLabel === 'Audio session error'
    || diagnostics?.getUserMedia === false
    || diagnostics?.mediaRecorder === false
  const socketIssue = errorLabel === 'Socket error'
  const connected = Boolean(workspace?.connected)
  const workspaceSetup = workspace?.setup_issue || 'Set VOICEOPS_WORKSPACE in backend/.env to connect local code actions.'

  if (voiceIssue) {
    return {
      title: 'Type while voice is unavailable',
      detail: connected
        ? 'Use typed commands or retry live meeting. Code-changing requests still require approval.'
        : workspaceSetup,
    }
  }
  if (socketIssue) {
    return {
      title: 'Type while live reconnects',
      detail: connected
        ? 'Use typed commands while the live meeting connection recovers. Code-changing requests still require approval.'
        : workspaceSetup,
    }
  }
  return {
    title: 'Typed command fallback',
    detail: connected
      ? 'Use typed commands for memory, tests, and approved code actions.'
      : workspaceSetup,
  }
}

function RepoConnectForm({ onConnectWorkspace, label = 'Local repository path', submitLabel = 'Connect' }) {
  const [path, setPath] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function submit(event) {
    event.preventDefault()
    if (!onConnectWorkspace || busy || !path.trim()) return
    setBusy(true)
    setError('')
    try {
      await onConnectWorkspace(path.trim())
      setPath('')
    } catch (err) {
      setError(err.message || 'Workspace connect failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="repo-connect-form" onSubmit={submit}>
      <input
        value={path}
        onChange={(event) => setPath(event.target.value)}
        placeholder="/absolute/path/to/local/repo"
        aria-label={label}
        disabled={busy}
      />
      <button type="submit" disabled={busy || !path.trim()}>
        {busy ? 'Connecting' : submitLabel}
      </button>
      {error && <small role="alert">{error}</small>}
    </form>
  )
}

function RepoCloneForm({ onCloneWorkspace }) {
  const [remoteUrl, setRemoteUrl] = useState('')
  const [targetPath, setTargetPath] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function submit(event) {
    event.preventDefault()
    if (!onCloneWorkspace || busy || !remoteUrl.trim()) return
    setBusy(true)
    setError('')
    try {
      await onCloneWorkspace(remoteUrl.trim(), targetPath.trim())
      setRemoteUrl('')
      setTargetPath('')
    } catch (err) {
      setError(err.message || 'Workspace clone failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="repo-clone-form" onSubmit={submit}>
      <input
        value={remoteUrl}
        onChange={(event) => setRemoteUrl(event.target.value)}
        placeholder="https://github.com/org/repo.git"
        aria-label="GitHub repository URL"
        disabled={busy}
      />
      <input
        value={targetPath}
        onChange={(event) => setTargetPath(event.target.value)}
        placeholder="/absolute/path/under/clone-root"
        aria-label="Optional absolute clone target path"
        disabled={busy}
      />
      <button type="submit" disabled={busy || !remoteUrl.trim()}>
        {busy ? 'Cloning' : 'Clone'}
      </button>
      {error && <small role="alert">{error}</small>}
      <small>Leave blank for the managed clone root; custom paths must be absolute and inside it.</small>
      <small>Private HTTPS repos use your local <code>gh auth login</code>; SSH URLs use your SSH keys.</small>
    </form>
  )
}

function RepositorySetupPanel({ workspace, canConnectWorkspace = false, onConnectWorkspace, onCloneWorkspace }) {
  if (workspace?.connected !== false) return null
  const setupIssue = workspace?.setup_issue
  const commands = repoSetupCommands(workspace)
  const canUseAdminConnect = canConnectWorkspace && (onConnectWorkspace || onCloneWorkspace)

  return (
    <div className="repo-setup">
      <div className="repo-setup-head">
        <span><GitBranch size={14} /> Repository setup</span>
        <small>local first</small>
      </div>
      <p>{setupIssue || 'Connect code actions by pointing the backend at a local clone of your GitHub repo.'}</p>
      <div className="repo-setup-steps" aria-label="Repository setup steps">
        <div>
          <b>1</b>
          <span>Clone the GitHub repo, or open an existing local clone</span>
        </div>
        <div>
          <b>2</b>
          <span>Set <code>VOICEOPS_WORKSPACE</code> to that local folder, not the GitHub URL</span>
        </div>
        <div>
          <b>3</b>
          <span>{canUseAdminConnect ? 'Use Connect or Clone below; restart only if you edit env' : 'Restart backend, then refresh this console'}</span>
        </div>
      </div>
      <div className="readiness-command mono repo-command">
        {commands.clone}
        <br />
        {commands.env}
      </div>
      {canConnectWorkspace && onConnectWorkspace && (
        <RepoConnectForm onConnectWorkspace={onConnectWorkspace} />
      )}
      {canConnectWorkspace && onCloneWorkspace && (
        <details className="repo-clone">
          <summary>
            <span>Clone from GitHub</span>
            <small>admin</small>
          </summary>
          <RepoCloneForm onCloneWorkspace={onCloneWorkspace} />
        </details>
      )}
      <small>GitHub URL is only for clone/remote. VoiceOps edits the local folder so approvals can create branches and diffs.</small>
    </div>
  )
}

export function repoSetupCommands(workspace) {
  const remote = String(workspace?.configured_workspace || '').trim()
  const cloneUrl = /^(?:https?:\/\/|git@|ssh:\/\/|git:\/\/)/i.test(remote)
    ? remote
    : 'https://github.com/org/repo.git'
  const repoName = repoNameFromRemote(cloneUrl) || 'repo'
  const localPath = `/absolute/path/to/${repoName}`
  return {
    clone: `git clone ${cloneUrl} ${localPath}`,
    env: `VOICEOPS_WORKSPACE=${localPath}`,
  }
}

function repoNameFromRemote(remote) {
  const clean = String(remote || '').replace(/[#?].*$/, '').replace(/\/$/, '')
  const match = clean.match(/[:/]([^/:]+?)(?:\.git)?$/)
  return match?.[1]?.replace(/[^\w.-]/g, '') || ''
}

export function repositoryFlowCopy(workspace) {
  if (!workspace) return null
  if (!workspace.connected) {
    return {
      tone: 'blocked',
      title: 'Repo not connected',
      meta: workspace.setup_issue ? 'use local clone path' : 'local setup required',
      detail: workspace.setup_issue || 'Set VOICEOPS_WORKSPACE to a cloned local folder, not a GitHub URL, before code actions can propose patches.',
    }
  }
  if (workspace.is_git_repo === false) {
    return {
      tone: 'blocked',
      title: 'Local folder connected',
      meta: 'git not initialized',
      detail: 'VoiceOps can read files. Initialize git in this folder before approved patches create branches or PR plans.',
    }
  }
  const branch = workspace.branch || 'branch pending'
  const remote = String(workspace.remote_url || '').trim()
  const isGitHub = workspace.remote_kind === 'github' || /github\.com[:/]/i.test(remote)
  const remoteLabel = remote ? (isGitHub ? 'GitHub remote' : 'git remote') : 'no remote'
  return {
    tone: remote ? 'ready' : 'local',
    title: remote ? (isGitHub ? 'GitHub remote detected' : 'Git remote detected') : 'Local git repo connected',
    meta: `${branch} · ${remoteLabel}`,
    detail: remote
      ? (isGitHub
        ? 'VOICEOPS_WORKSPACE points to the local clone. Approved patches create local branches; PR creation stays explicit.'
        : 'Approved patches create local branches. GitHub PR flow requires origin to point at GitHub.')
      : 'Approved patches can create local branches. Add a GitHub remote later when PRs are needed.',
  }
}

export function repositoryNextSteps(workspace) {
  if (!workspace?.connected) return []
  if (workspace.is_git_repo === false) {
    return [
      { key: 'local', label: 'Local folder connected', tone: 'ready' },
      { key: 'git', label: 'Initialize git', tone: 'blocked' },
    ]
  }
  const remote = String(workspace.remote_url || '').trim()
  const isGitHub = workspace.remote_kind === 'github' || /github\.com[:/]/i.test(remote)
  return [
    { key: 'branch', label: 'Approved patch creates branch', tone: 'ready' },
    { key: 'commit', label: 'Commit approved patch', tone: 'next' },
    isGitHub
      ? { key: 'auth', label: 'Run gh auth login', tone: 'next' }
      : { key: 'remote', label: 'Add GitHub origin remote', tone: 'blocked' },
    isGitHub
      ? { key: 'pr', label: 'Create PR explicitly', tone: 'manual' }
      : { key: 'pr', label: 'PR after GitHub remote', tone: 'manual' },
  ]
}

export function repositoryBindingSummary(workspace) {
  if (!workspace?.connected) return null
  const sourceLabels = {
    room: 'room repo',
    configured: 'env workspace',
    runtime: 'admin switch',
    saved: 'saved workspace',
  }
  const source = sourceLabels[workspace.source] || 'workspace'
  const path = workspace.path || workspace.configured_workspace || workspace.name || ''
  const remote = workspace.remote_url || (workspace.remote_kind === 'github' ? 'GitHub remote' : 'no remote')
  return { source, path, remote }
}

export function repositoryAuthBoundary(workspace) {
  if (!workspace?.connected) return {
    mode: 'local clone',
    detail: 'GitHub URL is used only to clone. VoiceOps needs a local folder path.',
  }
  const remote = String(workspace.remote_url || '').trim()
  const isGitHub = workspace.remote_kind === 'github' || /github\.com[:/]/i.test(remote)
  return isGitHub
    ? {
      mode: 'local gh',
      detail: 'PRs use your local GitHub CLI session; VoiceOps does not collect GitHub OAuth tokens.',
    }
    : {
      mode: 'local git',
      detail: 'Approved patches stay local until you add a GitHub origin remote.',
    }
}

function RepositoryFlowPanel({ workspace, canConnectWorkspace = false, onConnectWorkspace, onCloneWorkspace }) {
  const copy = repositoryFlowCopy(workspace)
  if (!copy) return null
  const compact = workspace?.connected
  const canChangeWorkspace = compact && canConnectWorkspace && onConnectWorkspace
  const nextSteps = repositoryNextSteps(workspace)
  const binding = repositoryBindingSummary(workspace)
  const authBoundary = repositoryAuthBoundary(workspace)
  return (
    <div className={`repo-flow ${copy.tone}${compact ? ' compact' : ''}`} aria-label="Repository workflow status" title={copy.detail}>
      <div className="repo-flow-head">
        <span><GitBranch size={14} />{copy.title}</span>
        <small>{copy.meta}</small>
      </div>
      <p>{copy.detail}</p>
      {binding && (
        <div className="repo-flow-binding" aria-label="Active repository binding">
          <span><b>scope</b><small>{binding.source}</small></span>
          <span><b>path</b><small title={binding.path}>{binding.path || '-'}</small></span>
          <span><b>remote</b><small title={binding.remote}>{binding.remote}</small></span>
        </div>
      )}
      {nextSteps.length > 0 && (
        <div className="repo-flow-steps" aria-label="Repository workflow steps">
          {nextSteps.map((step) => (
            <span className={step.tone} key={step.key}>{step.label}</span>
          ))}
        </div>
      )}
      <small className="repo-auth-boundary"><b>{authBoundary.mode}</b> {authBoundary.detail}</small>
      {workspace?.persistence_note && <small className="repo-persistence-note">{workspace.persistence_note}</small>}
      {!workspace?.connected && (
        <RepositorySetupPanel
          workspace={workspace}
          canConnectWorkspace={canConnectWorkspace}
          onConnectWorkspace={onConnectWorkspace}
          onCloneWorkspace={onCloneWorkspace}
        />
      )}
      {canChangeWorkspace && (
        <details className="repo-change">
          <summary>
            <span>Change local repo</span>
            <small>admin</small>
          </summary>
          <RepoConnectForm
            onConnectWorkspace={onConnectWorkspace}
            label="New local repository path"
            submitLabel="Switch"
          />
          {onCloneWorkspace && (
            <details className="repo-clone">
              <summary>
                <span>Clone GitHub repo</span>
                <small>optional</small>
              </summary>
              <RepoCloneForm onCloneWorkspace={onCloneWorkspace} />
            </details>
          )}
        </details>
      )}
    </div>
  )
}

export function agentRoutingFlowCopy(providers = [], agentName = APP_NAME) {
  if (providers == null) {
    return {
      tone: 'local',
      title: 'Checking coding agents',
      meta: 'routes loading',
      detail: 'Repo, memory, and typed commands work now. Agent assignment unlocks when provider routes finish loading.',
    }
  }
  const rows = normalizeExternalAgentProviders(providers)
  const total = rows.length
  const connected = rows.filter((provider) => provider.connected)
  const patchReady = connected.filter((provider) => providerModeReadiness(provider, 'patch')?.ready)
  const readReady = connected.filter((provider) => (
    providerModeReadiness(provider, 'review')?.ready
    || providerModeReadiness(provider, 'explain')?.ready
    || providerModeReadiness(provider, 'test')?.ready
  ))
  if (!total || !connected.length) {
    return {
      tone: 'blocked',
      title: 'Agent assignment off',
      meta: 'connect coding agent',
      detail: `${agentName} can still answer memory and repo questions. Connect a coding agent before assigning review, test, or patch work.`,
    }
  }
  if (!patchReady.length) {
    return {
      tone: 'local',
      title: 'Patch agent needed',
      meta: `${readReady.length} read-only · ${connected.length}/${total} connected`,
      detail: 'Review, explain, and tests can run. Connect a local coding agent before assigning patch work.',
    }
  }
  const labels = patchReady.slice(0, 2).map((provider) => provider.label || provider.provider).join(', ')
  const suffix = patchReady.length > 2 ? ` +${patchReady.length - 2}` : ''
  return {
    tone: 'ready',
    title: 'Coding agents ready',
    meta: `${patchReady.length} patch · ${connected.length}/${total} connected`,
    detail: `${labels}${suffix} can propose patches. Every file change still waits for approval.`,
  }
}

export function setupActionStripItems({
  workspace,
  providers = [],
  actions = [],
  dashboard = null,
  deploymentHardening = null,
  agentName = APP_NAME,
} = {}) {
  const repo = repositoryFlowCopy(workspace)
  const agents = agentRoutingFlowCopy(providers, agentName)
  const deployment = deploymentHardeningFlowCopy(deploymentHardening)
  const actionRows = compactRecentActions(actions || [], 5)
  const pendingApprovals = actionRows.filter((action) => action.status === 'pending_approval' || action.pending_approval)
  const failedActions = actionRows.filter((action) => action.status === 'failed')
  const dashboardApprovals = dashboard?.approvals || []
  const approvalCount = Math.max(pendingApprovals.length, dashboardApprovals.length)
  const latestPending = pendingApprovals[0] || dashboardApprovals[0]

  return [
    repo ? {
      key: 'repo',
      label: 'Repo',
      tone: repo.tone === 'blocked' ? 'blocked' : 'ready',
      value: repo.tone === 'blocked' ? 'Connect repo' : repo.title,
      detail: repo.meta,
    } : {
      key: 'repo',
      label: 'Repo',
      tone: 'attention',
      value: 'Checking',
      detail: 'workspace',
    },
    agents ? {
      key: 'agents',
      label: 'Agents',
      tone: agents.tone === 'ready' ? 'ready' : agents.tone === 'blocked' ? 'blocked' : 'attention',
      value: agents.tone === 'ready' ? 'Ready' : agents.title,
      detail: agents.meta,
    } : {
      key: 'agents',
      label: 'Agents',
      tone: 'attention',
      value: 'Checking',
      detail: 'providers',
    },
    deployment ? {
      key: 'deploy',
      label: 'Deploy',
      tone: deployment.tone === 'ready' ? 'ready' : deployment.tone === 'blocked' ? 'blocked' : 'attention',
      value: deployment.title,
      detail: deployment.meta,
    } : null,
    {
      key: 'actions',
      label: 'Actions',
      tone: approvalCount ? 'attention' : failedActions.length ? 'blocked' : 'ready',
      value: approvalCount
        ? `${approvalCount} approval${approvalCount === 1 ? '' : 's'}`
        : failedActions.length
          ? `${failedActions.length} failed`
          : 'Clear',
      detail: latestPending?.summary || latestPending?.title || (approvalCount ? 'review pending' : 'approval queue'),
    },
  ].filter(Boolean)
}

export function deploymentHardeningFlowCopy(hardening) {
  if (!hardening) return null
  const checks = hardening.checks || []
  const environment = hardening.environment || 'local'
  const blocked = checks.filter((check) => !check.ready)
  const critical = blocked.find((check) => check.severity === 'critical')
  const high = blocked.find((check) => check.severity === 'high')
  const firstBlocker = critical || high || blocked[0]
  const score = typeof hardening.score === 'number' ? `${hardening.score}%` : 'checking'
  const totalCount = hardening.total_count ?? checks.length ?? 0
  const counts = `${hardening.ready_count ?? 0}/${totalCount}`

  if (environment !== 'production') {
    return {
      tone: 'ready',
      title: 'Local mode',
      meta: `${score} · ${counts} production checks`,
      detail: 'Production hardening is tracked in system details before deployment.',
    }
  }

  if (hardening.ready) {
    return {
      tone: 'ready',
      title: 'Production ready',
      meta: `${score} · startup gate enforced`,
      detail: 'Deployment hardening checks are passing.',
    }
  }

  return {
    tone: critical ? 'blocked' : 'attention',
    title: critical ? 'Production blocked' : 'Hardening needed',
    meta: firstBlocker?.action || firstBlocker?.detail || `${blocked.length} check${blocked.length === 1 ? '' : 's'} need attention`,
    detail: firstBlocker?.label || 'Deployment hardening needs attention.',
  }
}

export function setupRunwaySummary({
  workspace,
  providers = [],
  actions = [],
  dashboard = null,
  deploymentHardening = null,
  agentName = APP_NAME,
} = {}) {
  const items = setupActionStripItems({ workspace, providers, actions, dashboard, deploymentHardening, agentName })
  const agentFlow = agentRoutingFlowCopy(providers, agentName)
  const repo = items.find((item) => item.key === 'repo') || {}
  const agents = items.find((item) => item.key === 'agents') || {}
  const deployment = items.find((item) => item.key === 'deploy') || {}
  const action = items.find((item) => item.key === 'actions') || {}
  const evidence = [repo.value, agents.value, deployment.value, action.value].filter(Boolean)

  if (deployment.tone === 'blocked') {
    return {
      tone: 'blocked',
      label: 'Production blocker',
      title: deployment.value,
      detail: deployment.detail || deployment.meta,
      actionLabel: 'Details',
      targetId: 'system-details',
      evidence,
    }
  }

  if (action.tone === 'attention') {
    return {
      tone: 'attention',
      label: 'Next decision',
      title: 'Review pending patch',
      detail: action.detail || 'Approve or reject the proposed diff before files change.',
      actionLabel: 'Review',
      targetId: 'agent-actions',
      evidence,
    }
  }

  if (action.tone === 'blocked') {
    return {
      tone: 'blocked',
      label: 'Next review',
      title: 'Review failed action',
      detail: action.detail || 'Check the failed action audit before assigning more work.',
      actionLabel: 'Review',
      targetId: 'agent-actions',
      evidence,
    }
  }

  if (repo.tone === 'blocked') {
    return {
      tone: 'blocked',
      label: 'Next setup',
      title: 'Connect local repo',
      detail: 'Point VOICEOPS_WORKSPACE at a cloned folder before code actions can propose patches.',
      actionLabel: 'Setup',
      targetId: 'agent-setup',
      evidence,
    }
  }

  if (agents.tone === 'blocked') {
    return {
      tone: 'attention',
      label: 'Next setup',
      title: 'Connect coding agent',
      detail: `${agentName} can answer memory and repo questions now. Connect a coding agent for review, tests, or patch proposals.`,
      actionLabel: 'Setup',
      targetId: 'agent-setup',
      evidence,
    }
  }

  if (agents.tone === 'attention') {
    return {
      tone: 'attention',
      label: 'Next setup',
      title: agents.value || 'Complete agent setup',
      detail: agentFlow?.detail || 'Complete coding agent setup before assigning patch work.',
      actionLabel: 'Setup',
      targetId: 'agent-setup',
      evidence,
    }
  }

  return {
    tone: 'ready',
    label: 'Ready',
    title: 'Code collaboration ready',
    detail: 'Use meeting memory, assign focused work, or request an approval-first patch.',
    evidence,
  }
}

export function aiCoworkerPresence({
  agentName = APP_NAME,
  workspace,
  liveDiagnostics = {},
  actions = [],
  agentRuns = [],
  dashboard = null,
} = {}) {
  const actionRows = compactRecentActions(Array.isArray(actions) ? actions : [], 5)
  const pendingApprovals = actionRows.filter((action) => action.status === 'pending_approval' || action.pending_approval)
  const failedActions = actionRows.filter((action) => action.status === 'failed')
  const dashboardApprovals = dashboard?.approvals || []
  const approvalCount = Math.max(pendingApprovals.length, dashboardApprovals.length)
  const latestApproval = pendingApprovals[0] || dashboardApprovals[0]
  const running = (agentRuns || []).find((run) => run.status === 'running')
  const latestRun = running || (agentRuns || [])[0]
  const liveActive = Boolean(liveDiagnostics?.active || liveDiagnostics?.phase === 'live' || liveDiagnostics?.captionPreview)
  const voiceIssue = Boolean(liveDiagnostics?.lastErrorKind || liveDiagnostics?.getUserMedia === false || liveDiagnostics?.mediaRecorder === false)
  const repoKnown = typeof workspace?.connected === 'boolean'
  const repoConnected = Boolean(workspace?.connected)

  if (approvalCount) {
    return {
      tone: 'attention',
      status: 'Waiting approval',
      title: `${agentName} proposed a patch`,
      detail: latestApproval?.summary || latestApproval?.title || 'Review the pending patch before any workspace files change.',
      chips: [`${approvalCount} approval${approvalCount === 1 ? '' : 's'}`, 'preview first'],
    }
  }

  if (running) {
    return {
      tone: 'active',
      status: 'Working',
      title: `${agentName} is coordinating agents`,
      detail: displayText(running.summary || running.route || 'Running the current agent task.'),
      chips: [
        `${running.steps?.length || 0} step${running.steps?.length === 1 ? '' : 's'}`,
        running.route ? String(running.route).replace(/_/g, ' ') : 'agent run',
      ],
    }
  }

  if (liveActive) {
    return {
      tone: 'active',
      status: 'Listening',
      title: `${agentName} is in the meeting`,
      detail: 'Live transcript is active. Speaker labels may refine a few seconds later.',
      chips: [
        liveDiagnostics?.provider || 'live audio',
        liveDiagnostics?.captionPreview ? 'captions' : 'streaming',
      ],
    }
  }

  if (voiceIssue) {
    return {
      tone: 'attention',
      status: 'Voice fallback',
      title: `${agentName} can still work by text`,
      detail: liveDiagnostics?.recoveryHint || 'Microphone is unavailable. Typed commands, memory, and approvals remain available.',
      chips: ['type instead', 'voice fallback'],
    }
  }

  if (failedActions.length) {
    return {
      tone: 'blocked',
      status: 'Needs review',
      title: `${agentName} hit an action error`,
      detail: failedActions[0]?.summary || 'Review the failed action before continuing.',
      chips: [`${failedActions.length} failed`, 'audit kept'],
    }
  }

  if (repoKnown && !repoConnected) {
    return {
      tone: 'attention',
      status: 'Ready, repo needed',
      title: `${agentName} is online`,
      detail: 'Meeting, memory, and planning work now. Connect a local repo for code patches.',
      chips: ['memory ready', 'patches blocked'],
    }
  }

  if (latestRun) {
    return {
      tone: latestRun.status === 'failed' ? 'blocked' : 'ready',
      status: String(latestRun.status || 'Ready').replace(/_/g, ' '),
      title: `${agentName} is online`,
      detail: displayText(latestRun.summary || 'No active agent run.'),
      chips: [latestRun.route ? String(latestRun.route).replace(/_/g, ' ') : 'last run'],
    }
  }

  return {
    tone: 'ready',
    status: repoKnown ? 'Ready' : 'Checking',
    title: `${agentName} is online`,
    detail: repoKnown
      ? 'Ask meeting memory, request review, run tests, or propose an approval-first patch.'
      : 'Loading workspace and room state. Typed commands remain available.',
    chips: [repoConnected ? 'repo connected' : 'room ready', 'approval first'],
  }
}

function AICoworkerPresencePanel({ agentName, workspace, liveDiagnostics, actions, agentRuns, dashboard }) {
  const presence = aiCoworkerPresence({ agentName, workspace, liveDiagnostics, actions, agentRuns, dashboard })
  return (
    <div className={`ai-coworker ${presence.tone}`} aria-label={`AI coworker status: ${presence.status}. ${presence.detail}`}>
      <div className="ai-coworker-head">
        <span className="ai-coworker-avatar"><Robot size={15} /></span>
        <div>
          <span>AI coworker</span>
          <b>{presence.title}</b>
        </div>
        <small>{presence.status}</small>
      </div>
      <p title={presence.detail}>{presence.detail}</p>
      {presence.chips?.length > 0 && (
        <div className="ai-coworker-chips">
          {presence.chips.slice(0, 3).map((chip) => <span key={chip}>{chip}</span>)}
        </div>
      )}
    </div>
  )
}

function SetupActionStrip({ workspace, providers, actions, dashboard, deploymentHardening, agentName }) {
  const items = setupActionStripItems({ workspace, providers, actions, dashboard, deploymentHardening, agentName })
  const runway = setupRunwaySummary({ workspace, providers, actions, dashboard, deploymentHardening, agentName })
  if (runway.tone === 'ready') return null
  const detailSummary = items.map((item) => `${item.label}: ${item.detail}`).join(' · ')
  return (
    <div
      className={`setup-action-strip ${runway.tone}`}
      aria-label={`Setup and action status: ${runway.title}. ${runway.detail}`}
    >
      <div
        className={`setup-runway-card ${runway.tone}`}
        aria-label={`Setup runway: ${runway.title}. ${runway.detail}`}
        title={runway.detail}
      >
        <span>{runway.label}</span>
        <div>
          <b>{runway.title}</b>
          <small>{runway.detail}</small>
        </div>
        <small>{runway.evidence.slice(0, 3).join(' · ')}</small>
        {runway.targetId && (
          <a className="setup-runway-link" href={`#${runway.targetId}`}>{runway.actionLabel || 'Open'}</a>
        )}
      </div>
      <details className="setup-action-details" aria-label="Setup status details">
        <summary>
          <span>Status details</span>
          <small>{detailSummary}</small>
        </summary>
        <div className="setup-action-detail-grid">
          {items.map((item) => (
            <div className={`setup-action-cell ${item.tone}`} title={`${item.value} · ${item.detail}`} key={item.key}>
              <span>{item.label}</span>
              <b>{item.value}</b>
              <small>{item.detail}</small>
            </div>
          ))}
        </div>
      </details>
    </div>
  )
}

function AgentRoutingFlowPanel({ providers, agentName }) {
  const copy = agentRoutingFlowCopy(providers, agentName)
  if (!copy || copy.tone === 'ready') return null
  const compact = copy.tone !== 'blocked' && copy.tone !== 'local'
  return (
    <div className={`repo-flow agent-route-flow ${copy.tone}${compact ? ' compact' : ''}`} aria-label="Agent routing status" title={copy.detail}>
      <div className="repo-flow-head">
        <span><Code size={14} />{copy.title}</span>
        <small>{copy.meta}</small>
      </div>
      <p>{copy.detail}</p>
    </div>
  )
}

export function WorkDashboardPanel({
  dashboard,
  agentRuns = [],
  agentAssignments = [],
  externalAgentProviders = [],
  agentName = APP_NAME,
  onCreateAgentAssignment,
  onDispatchAgentAssignment,
  onCancelAgentAssignment,
  onRetryAgentAssignment,
  onClearCompletedAgentAssignments,
}) {
  const providerRows = normalizeExternalAgentProviders(externalAgentProviders)
  const [task, setTask] = useState('Review the next pending patch')
  const [agentId, setAgentId] = useState('')
  const [mode, setMode] = useState('review')
  const [modelByAgent, setModelByAgent] = useState({})
  const [busy, setBusy] = useState(false)
  const [dispatching, setDispatching] = useState('')
  const [queueBusy, setQueueBusy] = useState('')
  const [queueFilter, setQueueFilter] = useState('all')
  const [error, setError] = useState('')
  const dashboardLoading = !dashboard
  const metrics = dashboard?.metrics || []
  const internalAgents = dashboard?.agents || []
  const runningRuns = (agentRuns || []).filter((run) => run.status === 'running')
  const connectedProviders = providerRows.filter((provider) => provider.connected)
  const patchReadyProviders = connectedProviders.filter((provider) => providerModeReadiness(provider, 'patch')?.ready)
  const readOnlyProviders = connectedProviders.filter((provider) => (
    !providerModeReadiness(provider, 'patch')?.ready
    && ['review', 'test', 'explain'].some((mode) => providerModeReadiness(provider, mode)?.ready)
  ))
  const patchSetupNeeded = Boolean(providerRows.length && connectedProviders.length && !patchReadyProviders.length)
  const providerReadinessById = Object.fromEntries(
    connectedProviders.map((provider) => [provider.provider, provider.mode_readiness || {}])
  )
  const assignableAgents = [
    ...internalAgents.map((agent) => ({
      id: agent.id,
      label: agent.name,
      kind: 'internal',
      readiness: {},
    })),
    ...connectedProviders.map((provider) => ({
      id: provider.provider,
      label: provider.label,
      kind: 'external',
      readiness: provider.mode_readiness || {},
    })),
    ...(connectedProviders.length ? [{
      id: AUTO_EXTERNAL_AGENT_ID,
      label: 'Auto external agent',
      kind: 'external',
      readiness: {},
      auto: true,
    }] : []),
  ]
  const selectedAgent = assignableAgents.find((agent) => agent.id === agentId) || assignableAgents[0] || null
  const selectedProvider = selectedAgent?.kind === 'external'
    && selectedAgent.id !== AUTO_EXTERNAL_AGENT_ID
    ? connectedProviders.find((provider) => provider.provider === selectedAgent.id)
    : null
  const selectedModel = selectedProvider
    ? (modelByAgent[selectedProvider.provider] || selectedProvider.default_model || selectedProvider.supported_models?.[0] || '')
    : ''
  const selectedModeReadiness = readinessForAssignment(selectedAgent, mode, agentName)
  const assignmentBlocked = Boolean(selectedAgent && !selectedModeReadiness.ready)
  const assignmentBlockedMessage = assignmentBlocked
    ? assignmentReadinessMessage(selectedModeReadiness, mode)
    : ''
  const approvals = dashboard?.approvals || []
  const openItems = dashboard?.open_items || []
  const recentActions = dashboard?.recent_actions || []
  const decisions = dashboard?.recent_decisions || []
  const readiness = dashboard?.readiness || null
  const readinessDetail = readiness?.blockers?.[0] || readiness?.warnings?.[0] || readiness?.summary || ''
  const terminalAssignments = agentAssignments.filter((assignment) => isTerminalAssignment(assignment.status))
  const activeAssignments = agentAssignments.filter((assignment) => !isTerminalAssignment(assignment.status))
  const visibleAssignments = assignmentFilterItems(agentAssignments, queueFilter)
  const compactedAssignments = compactQueueAssignments(visibleAssignments)
  const previewAssignments = compactedAssignments.slice(0, WORKDASH_ASSIGNMENT_PREVIEW_LIMIT)
  const hiddenAssignmentCount = Math.max(0, compactedAssignments.length - previewAssignments.length)
  const groupedAssignmentCount = Math.max(0, visibleAssignments.length - compactedAssignments.length)
  const queueHealth = dashboard?.queue_health?.length
      ? normalizeQueueHealth(dashboard.queue_health)
      : queueHealthSummary({
        assignments: agentAssignments,
        approvals,
        providers: providerRows,
        providerReadinessById,
      })
  const visibleQueueHealth = visibleQueueHealthSummary(queueHealth)
  const totalOpenAssignments = Math.max(activeAssignments.length, queueHealthValue(queueHealth, 'Open'))
  const loadedQueueIsPartial = totalOpenAssignments > activeAssignments.length
  const remainingQueueCount = Math.max(0, totalOpenAssignments - activeAssignments.length)
  const queueRowsSyncing = loadedQueueIsPartial && activeAssignments.length === 0
  const canCreateAssignments = Boolean(onCreateAgentAssignment)
  const canDispatchAssignments = Boolean(onDispatchAgentAssignment)
  const canCancelAssignments = Boolean(onCancelAgentAssignment)
  const canRetryAssignments = Boolean(onRetryAgentAssignment)
  const canClearCompletedAssignments = Boolean(onClearCompletedAgentAssignments)
  const showQueueControls = agentAssignments.length > 0 && terminalAssignments.length > 0
  const workQueueLabel = loadedQueueIsPartial
    ? queueRowsSyncing
      ? `${totalOpenAssignments} indexed`
      : partialQueueSummary(activeAssignments.length, totalOpenAssignments)
    : `${activeAssignments.length} open`
  const queueStatusLabel = loadedQueueIsPartial && !queueRowsSyncing
    ? `${activeAssignments.length} shown`
    : workQueueLabel
  const compactPartialQueue = loadedQueueIsPartial
  const showQueueHealth = !compactPartialQueue && shouldShowQueueHealth(visibleQueueHealth, readinessDetail)
  const visibleAgents = [...internalAgents, ...connectedProviders.map((provider) => ({
    id: provider.provider,
    name: provider.label,
    kind: 'external',
    status: provider.connected ? provider.auth_method?.replace(/_/g, ' ') : 'offline',
  }))].slice(0, 5)
  const contextCount = openItems.length + recentActions.length + decisions.length
  const showRoomContext = runningRuns.length > 0
  const primaryRun = runningRuns[0] || null
  const primaryAssignment = activeAssignments[0] || null
  const agentActivity = primaryRun
    ? {
      tone: 'running',
      title: displayText(primaryRun.summary || primaryRun.route || 'Agent run active'),
      meta: `${runningRuns.length} running`,
      detail: primaryRun.route ? String(primaryRun.route).replace(/_/g, ' ') : 'multi-agent run',
    }
    : primaryAssignment
      ? {
        tone: primaryAssignment.status || 'queued',
        title: activeAssignments.length === 1 ? 'Assignment queued' : `${activeAssignments.length} open assignments`,
        meta: `${activeAssignments.length} open`,
        detail: `${primaryAssignment.agent_label || 'Agent'} · ${dashboardStatusLabel(primaryAssignment.status)} · ${primaryAssignment.mode || 'work'}`,
      }
      : null
  const availableAgentCount = assignableAgents.length
  const nextStep = workDashboardNextStep({
    approvalsCount: approvals.length,
    activeAssignmentsCount: activeAssignments.length,
    availableAgentCount,
    contextCount,
    loadedQueueIsPartial,
  })
  const queueDetailsMeta = queueRowsSyncing
    ? `${totalOpenAssignments} indexed`
    : loadedQueueIsPartial
      ? `${remainingQueueCount} more`
      : workQueueLabel
  const statusTone = readiness ? (readiness.ready ? 'ready' : 'attention') : 'ready'
  const nextStepDetail = nextStep?.title ? `${nextStep.title}: ${nextStep.lines.join(' ')}` : ''
  const nextStepTone = approvals.length || statusTone === 'attention'
    ? 'attention'
    : (activeAssignments.length || loadedQueueIsPartial) ? 'pending' : 'ready'
  const statusDetail = readinessDetail || 'Commands, memory, and approvals are available.'
  const statusTitleDetail = readinessDetail || (approvals.length ? nextStepDetail : '')
  const queueCellMeta = queueRowsSyncing
    ? 'rows syncing'
    : loadedQueueIsPartial
    ? `${remainingQueueCount} more`
    : activeAssignments.length
      ? 'open assignments'
      : 'no open work'
  const ariaQueueSummary = workQueueLabel
  const ariaQueueMeta = loadedQueueIsPartial && !queueRowsSyncing ? '' : queueCellMeta
  const dashboardStatusParts = approvals.length
    ? [nextStep.title, ...nextStep.lines]
    : statusTone === 'attention'
      ? [statusDetail]
      : []
  const reviewSummary = approvals.length ? `${approvals.length} pending` : 'No patch'
  const reviewMeta = approvals.length ? 'approval required' : 'approval clear'
  const agentSummary = providerRows.length
    ? patchReadyProviders.length
      ? `${patchReadyProviders.length} patch ready`
      : connectedProviders.length
        ? readOnlyProviders.length
          ? `${readOnlyProviders.length} read-only`
          : 'Patch setup'
        : 'None'
    : availableAgentCount
      ? `${availableAgentCount} ready`
      : 'Off'
  const agentMeta = providerRows.length
    ? patchReadyProviders.length
      ? `${connectedProviders.length}/${providerRows.length} connected`
      : connectedProviders.length
        ? 'patch setup needed'
        : 'connect coding agent'
    : availableAgentCount
      ? 'agents ready'
      : 'connect coding agent'
  const agentAriaMeta = agentMeta
  const dashboardStatusSummary = dashboardStatusParts.filter(Boolean).join('. ')
  const delegateMeta = workDashboardDelegateMeta({
    queueRowsSyncing,
    loadedQueueIsPartial,
    activeAssignmentsCount: activeAssignments.length,
    agentAssignmentsCount: agentAssignments.length,
    availableAgentCount,
    connectedProviderCount: connectedProviders.length,
    totalProviderCount: providerRows.length,
    patchReadyProviderCount: patchReadyProviders.length,
  })
  const delegateSummaryMeta = workDashboardDelegateSummaryMeta({
    queueRowsSyncing,
    availableAgentCount,
    connectedProviderCount: connectedProviders.length,
    totalProviderCount: providerRows.length,
    patchReadyProviderCount: patchReadyProviders.length,
  })
  async function submitAssignment(event) {
    event.preventDefault()
    if (!canCreateAssignments || !selectedAgent || !task.trim() || busy || assignmentBlocked) return
    setBusy(true)
    setError('')
    try {
      await onCreateAgentAssignment?.({
        agent_id: selectedAgent.id,
        agent_label: selectedAgent.label,
        agent_kind: selectedAgent.kind,
        task: task.trim(),
        mode,
        model: selectedAgent.kind === 'external' ? selectedModel || null : null,
      })
      setTask('')
    } catch (err) {
      setError(err.message || 'Assignment failed')
    } finally {
      setBusy(false)
    }
  }
  async function dispatchAssignment(assignment) {
    if (!canDispatchAssignments || !assignment?.id || dispatching) return
    setDispatching(assignment.id)
    setError('')
    try {
      await onDispatchAgentAssignment?.(assignment.id)
    } catch (err) {
      setError(err.message || 'Dispatch failed')
    } finally {
      setDispatching('')
    }
  }
  async function cancelAssignment(assignment) {
    if (!canCancelAssignments || !assignment?.id || queueBusy) return
    setQueueBusy(`cancel:${assignment.id}`)
    setError('')
    try {
      await onCancelAgentAssignment?.(assignment.id)
    } catch (err) {
      setError(err.message || 'Cancel failed')
    } finally {
      setQueueBusy('')
    }
  }
  async function retryAssignment(assignment) {
    if (!canRetryAssignments || !assignment?.id || queueBusy) return
    setQueueBusy(`retry:${assignment.id}`)
    setError('')
    try {
      await onRetryAgentAssignment?.(assignment.id)
    } catch (err) {
      setError(err.message || 'Retry failed')
    } finally {
      setQueueBusy('')
    }
  }
  async function clearCompletedAssignments() {
    if (!canClearCompletedAssignments || queueBusy) return
    setQueueBusy('clear')
    setError('')
    try {
      await onClearCompletedAgentAssignments?.()
    } catch (err) {
      setError(err.message || 'Clear failed')
    } finally {
      setQueueBusy('')
    }
  }
  if (dashboardLoading) {
    return (
      <div>
        <p className="section-lab">Work dashboard</p>
        <div className="workdash">
          <div className="workdash-next syncing" aria-label="Current work step: Syncing dashboard">
            <span>Now</span>
            <b>Syncing dashboard</b>
            <small>Syncing room, queue, and agents.</small>
          </div>
          <div className="workdash-status-strip" aria-label="Work dashboard status: syncing room, queue, and agents.">
            <div className="workdash-status-cell">
              <span>Queue</span>
              <b>Syncing</b>
              <small>room updates</small>
            </div>
            <div className="workdash-status-cell ready">
              <span>Review</span>
              <b>No patch</b>
              <small>approval clear</small>
            </div>
            <div className="workdash-status-cell">
              <span>Agents</span>
              <b>Checking</b>
              <small>providers</small>
            </div>
          </div>
        </div>
      </div>
    )
  }
  return (
    <div>
      <p className="section-lab">Work dashboard</p>
      <div className="workdash">
        <div
          className={`workdash-next ${nextStepTone}`}
          title={nextStepDetail}
          aria-label={`Current work step: ${nextStep.title}. ${nextStep.lines.join(' ')}`}
        >
          <span>Now</span>
          <b>{nextStep.title}</b>
          <small>{nextStep.lines[0]}</small>
        </div>
        <div
          className={`workdash-status-strip secondary ${statusTone}`}
          title={statusTitleDetail || readiness?.summary || statusDetail}
          aria-label={`Work dashboard status: Queue ${ariaQueueSummary}${ariaQueueMeta ? `, ${ariaQueueMeta}` : ''}. Review ${reviewSummary}, ${reviewMeta}. Agents ${agentSummary}, ${agentAriaMeta}. ${dashboardStatusSummary}`}
        >
          <div className="workdash-status-cell">
            <span>Queue</span>
            <b>{queueStatusLabel}</b>
            {queueCellMeta && <small>{queueCellMeta}</small>}
          </div>
          <div className={`workdash-status-cell ${approvals.length ? 'attention' : 'ready'}`}>
            <span>Review</span>
            <b>{reviewSummary}</b>
            <small>{reviewMeta}</small>
          </div>
          <div className={`workdash-status-cell ${availableAgentCount && !patchSetupNeeded ? 'ready' : 'attention'}`}>
            <span>Agents</span>
            <b>{agentSummary}</b>
            <small>{agentMeta}</small>
          </div>
        </div>
        {agentActivity && (
          <div
            className={`workdash-agent-activity ${agentActivity.tone}`}
            aria-label={`Agent activity: ${agentActivity.title}. ${agentActivity.detail}. ${agentActivity.meta}.`}
            title={`${agentActivity.detail} · ${agentActivity.meta}`}
          >
            <span>Agent activity</span>
            <b>{agentActivity.title}</b>
            <small>{agentActivity.detail}</small>
          </div>
        )}
        {showQueueHealth && (
          <div className="workdash-health" aria-label="Queue health summary">
            {visibleQueueHealth.map((item) => (
              <div className={`workdash-health-item ${item.tone}`} title={item.detail} key={item.label}>
                <b>{item.value}</b>
                <span>{item.label}</span>
              </div>
            ))}
          </div>
        )}

        {approvals.length > 0 && (
          <div className="workdash-lane urgent">
            <div className="workdash-subhead">
              <span>Needs review</span>
              <small>{approvals.length}</small>
            </div>
            {approvals.slice(0, 3).map((item) => <DashboardItem item={item} agentName={agentName} key={item.id} />)}
          </div>
        )}

        {(agentAssignments.length > 0 || totalOpenAssignments > 0) && (
          <details className="workdash-queue-details">
            <summary>
              <span>Assignments</span>
              <small>{queueDetailsMeta}</small>
            </summary>
            <div className="workdash-queue-body">
              {terminalAssignments.length > 0 && canClearCompletedAssignments && (
                <button
                  className="workdash-subhead-action"
                  type="button"
                  onClick={clearCompletedAssignments}
                  disabled={!terminalAssignments.length || Boolean(queueBusy)}
                >
                  Clear done
                </button>
              )}
            {showQueueControls && (
              <div className="workdash-queue-toolbar" aria-label="Assignment queue filter">
                {[
                  ['all', `All ${agentAssignments.length}`],
                  ['open', `Open ${activeAssignments.length}`],
                  ['done', `Done ${terminalAssignments.length}`],
                ].map(([value, label]) => (
                  <button
                    type="button"
                    className={queueFilter === value ? 'on' : ''}
                    onClick={() => setQueueFilter(value)}
                    key={value}
                  >
                    {label}
                  </button>
                ))}
              </div>
            )}
            {groupedAssignmentCount > 0 && (
              <div className="workdash-queue-summary">
                Grouped {groupedAssignmentCount} repeated assignment{groupedAssignmentCount === 1 ? '' : 's'}.
              </div>
            )}
              {previewAssignments.length ? previewAssignments.map((assignment) => {
              const readiness = readinessForQueuedAssignment(assignment, providerReadinessById, agentName)
              const runBlocked = assignment.agent_kind === 'external' && !readiness.ready
              const runBlockedMessage = runBlocked ? assignmentReadinessMessage(readiness, assignment.mode) : ''
              const active = ['queued', 'running'].includes(assignment.status)
              const terminal = isTerminalAssignment(assignment.status)
              const stale = assignmentIsStale(assignment)
              const route = assignmentRouteSummary(assignment)
              return (
                <div className={`workdash-item ${assignment.status} ${stale ? 'stale' : ''}`} key={assignment.id}>
                  <div className="workdash-item-main">
                    <b>{assignment.task}</b>
                    <small>{assignment.agent_label} · {dashboardStatusLabel(assignment.status)} · {assignment.mode}</small>
                    {route && (
                      <div className={`workdash-route ${route.ready ? 'ready' : 'attention'}`} title={route.detail}>
                        {route.ready ? <CheckCircle size={12} /> : <WarningCircle size={12} />}
                        <span>{route.label}</span>
                      </div>
                    )}
                    <div className="workdash-audit-chips" aria-label="Assignment audit">
                      {assignmentAuditChips(assignment).map((chip) => (
                        <span key={chip}>{chip}</span>
                      ))}
                      <span className={stale ? 'warn' : ''}>{assignmentAgeLabel(assignment)}</span>
                      {assignment.duplicateCount > 1 && (
                        <span className="repeat">Repeated {assignment.duplicateCount} times</span>
                      )}
                    </div>
                    {runBlocked && <small className="workdash-readiness">{runBlockedMessage}</small>}
                  </div>
                  <div className="workdash-queue-actions">
                  {assignment.status === 'queued' && canDispatchAssignments && (
                    <button
                      className="workdash-run"
                      type="button"
                      onClick={() => dispatchAssignment(assignment)}
                      disabled={Boolean(dispatching) || runBlocked}
                      title={runBlocked ? runBlockedMessage : undefined}
                    >
                      {dispatching === assignment.id ? 'Running' : 'Run'}
                    </button>
                  )}
                  {active && canCancelAssignments && (
                    <button
                      className="workdash-run ghost"
                      type="button"
                      onClick={() => cancelAssignment(assignment)}
                      disabled={Boolean(queueBusy)}
                    >
                      {queueBusy === `cancel:${assignment.id}` ? 'Canceling' : 'Cancel'}
                    </button>
                  )}
                  {terminal && canRetryAssignments && (
                    <button
                      className="workdash-run ghost"
                      type="button"
                      onClick={() => retryAssignment(assignment)}
                      disabled={Boolean(queueBusy)}
                    >
                      {queueBusy === `retry:${assignment.id}` ? 'Retrying' : 'Retry'}
                    </button>
                  )}
                  </div>
                </div>
              )
            }) : loadedQueueIsPartial ? (
              <div className="workdash-empty syncing">
                Queue index has {totalOpenAssignments} open assignment{totalOpenAssignments === 1 ? '' : 's'}. Rows have not synced yet.
              </div>
            ) : (
              <div className="workdash-empty">No assignments match this filter.</div>
            )}
            {hiddenAssignmentCount > 0 && (
              <div className="workdash-overflow">
                +{hiddenAssignmentCount} more in this filter
              </div>
            )}
            </div>
          </details>
        )}

        {assignableAgents.length && canCreateAssignments ? (
          <details className="workdash-assignment-details" open={Boolean(error)}>
            <summary aria-label={`Assign work: ${delegateMeta}`} title={delegateMeta}>
              <span>Assign work</span>
              <small>{delegateSummaryMeta}</small>
            </summary>
            <form className="workdash-assign" onSubmit={submitAssignment}>
              <div className="workdash-assign-controls">
                <select
                  value={selectedAgent?.id || ''}
                  onChange={(event) => setAgentId(event.target.value)}
                  aria-label="Assignment agent"
                  disabled={busy}
                >
                  {assignableAgents.map((agent) => (
                    <option value={agent.id} key={`${agent.kind}:${agent.id}`}>
                      {agent.label}{agent.auto ? ' · recommended route' : ''}
                    </option>
                  ))}
                </select>
                <select value={mode} onChange={(event) => setMode(event.target.value)} aria-label="Assignment mode" disabled={busy}>
                  <option value="review">Review</option>
                  <option value="patch">Patch</option>
                  <option value="explain">Explain</option>
                  <option value="test">Test</option>
                  <option value="memory">Memory</option>
                </select>
              </div>
              {selectedProvider && (
                <label className="workdash-assign-model">
                  <span>Model</span>
                  <select
                    value={selectedModel}
                    onChange={(event) => setModelByAgent((prev) => ({
                      ...prev,
                      [selectedProvider.provider]: event.target.value,
                    }))}
                    aria-label="Assignment model"
                    disabled={busy}
                  >
                    {(selectedProvider.supported_models?.length
                      ? selectedProvider.supported_models
                      : [selectedProvider.default_model || 'provider-default']
                    ).map((model) => (
                      <option value={model} key={model}>{model}</option>
                    ))}
                  </select>
                </label>
              )}
              {selectedAgent?.id === AUTO_EXTERNAL_AGENT_ID && (
                <small className="workdash-auto-route">
                  Uses provider recommendation at run time. Patch results still wait for approval.
                </small>
              )}
              <div className="workdash-assign-task">
                <input
                  value={task}
                  onChange={(event) => setTask(event.target.value)}
                  placeholder="Describe the task"
                  aria-label="Assignment task"
                  disabled={busy}
                />
                <button
                  type="submit"
                  disabled={!selectedAgent || !task.trim() || busy || assignmentBlocked}
                  title={assignmentBlockedMessage || undefined}
                  aria-describedby={assignmentBlocked ? 'workdash-assignment-readiness' : undefined}
                >
                  {busy ? 'Assigning' : 'Assign'}
                </button>
              </div>
              {assignmentBlocked && (
                <small className="workdash-readiness" id="workdash-assignment-readiness">
                  {assignmentBlockedMessage}
                </small>
              )}
              {error && <small className="workdash-error" role="alert">{error}</small>}
            </form>
          </details>
        ) : null}

        {showRoomContext && (
          <details className="workdash-context">
            <summary>
              <span>Room context</span>
              <small>{contextCount} items · {runningRuns.length} running</small>
            </summary>
            <div className="workdash-context-body">
              {metrics.length > 0 && (
                <div className="workdash-metrics" aria-label="Work dashboard metrics">
                  {metrics.map((metric) => (
                    <div className={`workdash-metric ${metric.tone || 'neutral'}`} key={metric.label}>
                      <b>{metric.value}</b>
                      <span>{metric.label}</span>
                    </div>
                  ))}
                </div>
              )}

              <div className="workdash-agents">
                <div className="workdash-subhead">
                  <span>Agents</span>
                  <small>{runningRuns.length} running</small>
                </div>
                {visibleAgents.map((agent) => (
                  <div className="workdash-agent" key={`${agent.kind}:${agent.id}`}>
                    <span className={`workdash-dot ${agent.status === 'online' ? 'online' : ''}`} />
                    <b>{agent.name}</b>
                    <small>{agent.kind} · {agent.status}</small>
                  </div>
                ))}
                {!internalAgents.length && !connectedProviders.length && (
                  <div className="workdash-empty">Connect an agent provider or join the room to populate the roster.</div>
                )}
              </div>

              <div className="workdash-grid">
                <div className="workdash-lane">
                  <div className="workdash-subhead">
                    <span>Open work</span>
                    <small>{openItems.length}</small>
                  </div>
                  {openItems.length
                    ? openItems.slice(0, 3).map((item) => <DashboardItem item={item} agentName={agentName} key={item.id} />)
                    : <div className="workdash-empty">No open tasks, questions, or risks.</div>}
                </div>

                <div className="workdash-lane">
                  <div className="workdash-subhead">
                    <span>Recent closure</span>
                    <small>{recentActions.length}</small>
                  </div>
                  {recentActions.length
                    ? recentActions.slice(0, 3).map((item) => <DashboardItem item={item} agentName={agentName} key={item.id} />)
                    : <div className="workdash-empty">Approved patches and test runs will appear here.</div>}
                </div>
              </div>

              {decisions.length > 0 && (
                <div className="workdash-decisions">
                  <div className="workdash-subhead">
                    <span>Decisions</span>
                    <small>{decisions.length}</small>
                  </div>
                  {decisions.slice(0, 2).map((item) => <DashboardItem item={item} agentName={agentName} key={item.id} />)}
                </div>
              )}
            </div>
          </details>
        )}
      </div>
    </div>
  )
}

export default function RightRail({
  roomId = 'main',
  user = null,
  workspace,
  actionDone,
  artifacts,
  metrics,
  handoff,
  workDashboard,
  actions,
  agentRuns,
  agentAssignments,
  agentLLMRouting,
  llmProviders,
  externalAgentProviders,
  projectUsers,
  messages,
  auditEvents,
  memory,
  agentName = APP_NAME,
  liveDiagnostics,
  onApproveAction,
  onRejectAction,
  onCommitAction,
  onCreatePullRequest,
  onCancelAgentRun,
  onStartExternalAgentOAuth,
  onConnectExternalAgentLocalCli,
  onRunExternalAgent,
  onRecommendExternalAgent,
  onUpdateAgentLLMRoute,
  onPreflightAgentLLMRoute,
  onConnectLLMProvider,
  onPreflightLLMProvider,
  onDisconnectLLMProvider,
  onConnectWorkspace,
  onCloneWorkspace,
  onCreateAgentAssignment,
  onDispatchAgentAssignment,
  onCancelAgentAssignment,
  onRetryAgentAssignment,
  onClearCompletedAgentAssignments,
}) {
  const ws = workspace || {}
  const [deploymentHardening, setDeploymentHardening] = useState(null)
  const canViewDeployment = Boolean(user?.permissions?.includes('admin:manage'))
  const canConnectWorkspace = canViewDeployment
  const canApproveActions = Boolean(user?.permissions?.includes('agent:approve'))
  const canRunAgents = Boolean(user?.permissions?.includes('agent:run'))
  const canManageCredentials = Boolean(user?.permissions?.includes('credentials:manage_own'))
  const canInspectCode = Boolean(user?.permissions?.includes('voice:use'))
  const providerRows = normalizeExternalAgentProviders(externalAgentProviders)
  const actionsLoading = !Array.isArray(actions)
  const roomSnapshotLoading = actionsLoading && !Array.isArray(memory) && !handoff
  const recentActions = actionsLoading ? [] : compactRecentActions(actions, 5)
  const actionRows = splitActionRows(recentActions)
  const gitRefreshKey = (actionsLoading ? [] : actions)
    .map((action) => `${action.id}:${action.status}:${action.updated_at || action.created_at || ''}`)
    .join('|')
  const demoGateRefreshKey = (messages || [])
    .filter((message) => ['demo_gate_run', 'demo_gate_result'].includes(message.metadata?.source))
    .map((message) => `${message.id}:${message.metadata?.gate_job_id || ''}:${message.metadata?.gate_status || ''}`)
    .join('|')
  const demoGateResult = latestDemoGateResult(messages)
  const visibleAuditEvents = auditEvents?.length ? auditEvents : buildAuditEvents({ messages, actions })
  const handoffState = handoffDisplay(handoff, { actionCount: recentActions.length })
  const handoffLines = compactHandoffLines(handoffState.lines, 1)
  const handoffMeta = handoffState.reviewItems.length
    ? `${handoffState.reviewItems.length} open items`
    : handoffLines.hidden.length
      ? `${handoffLines.hidden.length + handoffLines.visible.length} lines`
      : handoffState.title
  const fallbackCopy = commandFallbackCopy(ws, liveDiagnostics)
  const agentSetupMeta = agentSetupSummary(externalAgentProviders, agentRuns)
  const showRuntimeDiagnostics = Boolean(
    liveDiagnostics?.smokeMode
    || liveDiagnostics?.active
    || liveDiagnostics?.captionPreview
    || liveDiagnostics?.lastErrorKind
  )
  const showDeepDiagnostics = Boolean(showRuntimeDiagnostics || ws.connected === false)

  useEffect(() => {
    if (!canViewDeployment) {
      setDeploymentHardening(null)
      return undefined
    }
    let alive = true
    fetchDeploymentHardening()
      .then((result) => {
        if (alive) setDeploymentHardening(result)
      })
      .catch(() => {
        if (alive) setDeploymentHardening(null)
      })
    return () => {
      alive = false
    }
  }, [canViewDeployment, ws.connected, ws.name])

  return (
    <aside className="rail rail--right">
      <div className="scroll">
        <AICoworkerPresencePanel
          agentName={agentName}
          workspace={ws}
          liveDiagnostics={liveDiagnostics}
          actions={actionsLoading ? [] : actions}
          agentRuns={agentRuns || []}
          dashboard={workDashboard}
        />

        <SetupActionStrip
          workspace={ws}
          providers={externalAgentProviders}
          actions={actionsLoading ? [] : actions}
          dashboard={workDashboard}
          deploymentHardening={deploymentHardening}
          agentName={agentName}
        />

        <div className="rail-section rail-section--workdash">
          <WorkDashboardPanel
            dashboard={workDashboard}
            agentRuns={agentRuns || []}
            agentAssignments={agentAssignments || []}
            externalAgentProviders={providerRows}
            agentName={agentName}
            onCreateAgentAssignment={canRunAgents ? onCreateAgentAssignment : undefined}
            onDispatchAgentAssignment={canRunAgents ? onDispatchAgentAssignment : undefined}
            onCancelAgentAssignment={canRunAgents ? onCancelAgentAssignment : undefined}
            onRetryAgentAssignment={canRunAgents ? onRetryAgentAssignment : undefined}
            onClearCompletedAgentAssignments={canRunAgents ? onClearCompletedAgentAssignments : undefined}
          />
        </div>

        {!roomSnapshotLoading && (
          <div id="agent-actions" className={`rail-section rail-section--actions ${!recentActions.length ? 'rail-section--empty-actions' : ''}`}>
            <p className="section-lab">Agent actions</p>
            {actionRows.pending.length ? (
              <div className="approval-queue" aria-label={`Approval queue: ${actionRows.pending.length} pending`}>
                <div className="approval-queue-head">
                  <span>Needs approval</span>
                  <small>{actionRows.pending.length} pending</small>
                </div>
                {actionRows.pending.map((a) => (
                  <ActionLogItem
                    action={a}
                    agentName={agentName}
                    key={a.id}
                    onApproveAction={canApproveActions ? onApproveAction : undefined}
                    onRejectAction={canApproveActions ? onRejectAction : undefined}
                    onCommitAction={canApproveActions ? onCommitAction : undefined}
                    onCreatePullRequest={canApproveActions ? onCreatePullRequest : undefined}
                  />
                ))}
              </div>
            ) : recentActions.length ? (
              <div className="approval-clear" aria-label="Approval queue: clear">
                <CheckCircle size={14} />
                <span>No approval waiting</span>
              </div>
            ) : null}
            {actionRows.history.length ? (
              <ActionHistoryDetails
                actions={actionRows.history}
                agentName={agentName}
                onApproveAction={canApproveActions ? onApproveAction : undefined}
                onRejectAction={canApproveActions ? onRejectAction : undefined}
                onCommitAction={canApproveActions ? onCommitAction : undefined}
                onCreatePullRequest={canApproveActions ? onCreatePullRequest : undefined}
              />
            ) : !actionRows.pending.length && actionsLoading ? (
              <div className="panel-empty compact">
                <ClipboardText size={22} color="var(--ink-ghost)" />
                Loading agent actions and approvals.
              </div>
            ) : !actionRows.pending.length && !recentActions.length ? (
              <div className="panel-empty compact">
                <ClipboardText size={22} color="var(--ink-ghost)" />
                Agent edits, tests, and approvals will appear here.
              </div>
            ) : null}
          </div>
        )}

        {!roomSnapshotLoading && (
          <div className="rail-section rail-section--memory" id="meeting-memory">
            <MemoryPanel key={roomId || 'main'} memory={memory} agentName={agentName} roomId={roomId} />
          </div>
        )}

        {!roomSnapshotLoading && (
          <div className="rail-section rail-section--handoff">
            <RailSectionDetails title="Handoff" meta={handoffMeta}>
              <div className="handoff">
                <div className="handoff-head">
                  <ClipboardText size={16} />
                  <b>{handoffState.title}</b>
                </div>
                <div className="handoff-lines">
                  {handoffLines.visible.map((line, i) => (
                    <p title={line.raw} key={i}>{line.text}</p>
                  ))}
                  {handoffLines.hidden.length > 0 && (
                    <details className="handoff-extra">
                      <summary>{handoffLines.hidden.length} more handoff line{handoffLines.hidden.length === 1 ? '' : 's'}</summary>
                      {handoffLines.hidden.map((line, i) => (
                        <p title={line.raw} key={i}>{line.text}</p>
                      ))}
                    </details>
                  )}
                </div>
                <ReviewQueue items={handoffState.reviewItems} agentName={agentName} />
              </div>
            </RailSectionDetails>
          </div>
        )}

        <div id="agent-setup" className="rail-section rail-section--setup rail-section--secondary">
          <RailSectionDetails
            title="Agent setup"
            meta={setupSectionMeta(ws, externalAgentProviders, agentRuns)}
          >
            <RepositoryFlowPanel
              workspace={workspace}
              canConnectWorkspace={canConnectWorkspace}
              onConnectWorkspace={onConnectWorkspace}
              onCloneWorkspace={onCloneWorkspace}
            />
            <AgentRoutingFlowPanel providers={externalAgentProviders} agentName={agentName} />

            <AgentTeamPanel runs={agentRuns || []} onCancelAgentRun={onCancelAgentRun} />

            {canViewDeployment && (
              <AgentSetupSubdetails
                title="Project access"
                meta={projectAccessMeta(projectUsers)}
              >
                <ProjectAccessPanel initialUsers={projectUsers} />
              </AgentSetupSubdetails>
            )}

            <AgentSetupSubdetails
              title="LLM routing"
              meta={agentLLMRouting?.routes?.length ? `${agentLLMRouting.routes.length} roles · ${llmProviderSummary(llmProviders)}` : llmProviderSummary(llmProviders)}
            >
              <LLMCredentialsPanel
                providers={llmProviders}
                readOnly={!canManageCredentials}
                onConnect={canManageCredentials ? onConnectLLMProvider : undefined}
                onPreflight={canManageCredentials ? onPreflightLLMProvider : undefined}
                onDisconnect={canManageCredentials ? onDisconnectLLMProvider : undefined}
              />
              <AgentLLMRoutingPanel
                routing={agentLLMRouting}
                onUpdateRoute={canApproveActions ? onUpdateAgentLLMRoute : undefined}
                onPreflightRoute={canApproveActions ? onPreflightAgentLLMRoute : undefined}
              />
            </AgentSetupSubdetails>

            <AgentSetupSubdetails
              title="Coding agents"
              meta={externalAgentSetupMeta(externalAgentProviders)}
            >
              <ExternalAgentsPanel
                providers={providerRows}
                onStartOAuth={canManageCredentials ? onStartExternalAgentOAuth : undefined}
                onConnectLocalCli={canManageCredentials ? onConnectExternalAgentLocalCli : undefined}
                onRunExternalAgent={canRunAgents ? onRunExternalAgent : undefined}
                onRecommendExternalAgent={canRunAgents ? onRecommendExternalAgent : undefined}
              />
            </AgentSetupSubdetails>
          </RailSectionDetails>
        </div>

        <div id="system-details" className="rail-section rail-section--system rail-section--secondary">
          <RailSectionDetails
            title="System details"
            meta={systemDetailsMeta(ws, liveDiagnostics)}
          >
            <SystemSummaryPanel workspace={ws} diagnostics={liveDiagnostics} />

            <WorkspacePanel
              roomId={roomId}
              workspace={ws}
              gitRefreshKey={gitRefreshKey}
              demoGateRefreshKey={demoGateRefreshKey}
              demoGateResult={demoGateResult}
              canViewDeployment={canViewDeployment}
              canInspectCode={canInspectCode}
              canManageRuntime={canViewDeployment}
            />

          {showDeepDiagnostics && (
            <DiagnosticDetails
              title="Runtime diagnostics"
              meta={runtimeDiagnosticsMeta(ws, liveDiagnostics)}
            >
              <LiveDiagnosticsPanel diagnostics={liveDiagnostics} canManageRuntime={canViewDeployment} />

              <div>
                <p className="section-lab">Recent audit</p>
                <AuditFeed events={visibleAuditEvents} />
              </div>

              <div>
                <p className="section-lab">Command fallback</p>
                <div className={`action${actionDone ? ' resolved' : ''}`}>
                  <div className="ah">
                    <span className="ribbon"><span className="lvdot" />READY</span>
                    <h3>{fallbackCopy.title}</h3>
                  </div>
                  <p className="sub">{fallbackCopy.detail}</p>
                  <div className="preview mono">
                    <div className="row"><span className="k">repository</span><span className="v">{ws.remote_url || ws.name || 'not connected'}</span></div>
                    <div className="row"><span className="k">workspace</span><span className="v">{ws.name || '-'}</span></div>
                    <div className="row"><span className="k">status</span><span className="v">{ws.connected ? 'listening' : 'disconnected'}</span></div>
                  </div>
                  {actionDone && (
                    <div className="doneline">
                      <CheckCircle size={15} color="var(--ok)" />
                      {actionDone}
                    </div>
                  )}
                </div>
              </div>

              {metrics?.length > 0 && (
                <div>
                  <p className="section-lab">{ws.name} · live</p>
                  <div className="metrics">
                    {metrics.map((m) => (
                      <div className="metric" key={m.label}>
                        <div className="k">{m.label}</div>
                        <div className={`v${m.status ? ' ' + m.status : ''}`}>{m.value}<small>{m.unit}</small></div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <div>
                <p className="section-lab">Artifacts</p>
                {artifacts?.length ? (
                  <div className="artifact-list" role="list" aria-label="Recorded artifacts">
                    {artifacts.map((a, i) => {
                      const Icon = ARTIFACT_ICON[a.type] || Package
                      return (
                        <div className="artifact" key={i} role="listitem">
                          <Icon className="lead" size={18} />
                          <span className="af"><b>{a.title}</b><small>{a.subtitle}</small></span>
                          <span className="artifact-state">recorded</span>
                        </div>
                      )
                    })}
                  </div>
                ) : (
                  <div className="panel-empty compact">
                    <Package size={22} color="var(--ink-ghost)" />
                    No artifacts yet. Approved patches, branches, test output, and handoff links will appear here.
                  </div>
                )}
              </div>
            </DiagnosticDetails>
          )}
          </RailSectionDetails>
        </div>
      </div>
    </aside>
  )
}
