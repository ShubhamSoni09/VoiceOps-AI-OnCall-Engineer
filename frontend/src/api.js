const TOKEN_KEY = 'voiceops_token'
export const DEFAULT_API_TIMEOUT_MS = 15000
export const API_RUNTIME_UNAVAILABLE =
  'Browser API runtime unavailable: fetch is missing. Open the console in a modern browser or restart the dev server.'

export function browserFetch() {
  if (typeof globalThis === 'undefined' || typeof globalThis.fetch !== 'function') {
    throw new Error(API_RUNTIME_UNAVAILABLE)
  }
  return globalThis.fetch.bind(globalThis)
}

export function getToken() {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token) {
  localStorage.setItem(TOKEN_KEY, token)
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY)
}

export function notifySessionExpired() {
  if (typeof window !== 'undefined' && typeof window.dispatchEvent === 'function') {
    window.dispatchEvent(new CustomEvent('voiceops:session-expired'))
  }
}

export async function fetchWithTimeout(url, options = {}) {
  const { timeoutMs = DEFAULT_API_TIMEOUT_MS, ...fetchOptions } = options
  const fetchImpl = browserFetch()
  const canTimeout = timeoutMs > 0 && !fetchOptions.signal && typeof AbortController !== 'undefined'
  if (!canTimeout) return fetchImpl(url, fetchOptions)

  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    return await fetchImpl(url, { ...fetchOptions, signal: controller.signal })
  } catch (err) {
    if (err?.name === 'AbortError' || controller.signal.aborted) {
      throw new Error(`Request timed out after ${timeoutMs}ms: ${url}`)
    }
    throw err
  } finally {
    clearTimeout(timer)
  }
}

export async function authFetch(url, options = {}) {
  const headers = { ...options.headers }
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`
  const res = await fetchWithTimeout(url, { ...options, headers })
  if (res.status === 401) {
    clearToken()
    notifySessionExpired()
    throw new Error('Session expired')
  }
  return res
}

export async function login(email, password) {
  const res = await fetchWithTimeout('/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
    timeoutMs: 45000,
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || 'Login failed')
  setToken(data.access_token)
  return data.user
}

export async function fetchUsers() {
  const res = await authFetch('/auth/users', { timeoutMs: 45000 })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Users load failed (${res.status})`)
  return data
}

export async function updateUserProjects(userId, projects) {
  const res = await authFetch(`/auth/users/${encodeURIComponent(userId)}/projects`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ projects }),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Project access update failed (${res.status})`)
  return data
}

export async function fetchBootstrap() {
  const res = await authFetch('/console/bootstrap', { timeoutMs: 45000 })
  if (!res.ok) throw new Error(`Bootstrap failed (${res.status})`)
  return res.json()
}

export async function connectWorkspace(path) {
  const res = await authFetch('/console/workspace', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path }),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Workspace connect failed (${res.status})`)
  return data
}

export async function cloneWorkspace(remoteUrl, targetPath) {
  const res = await authFetch('/console/workspace/clone', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ remote_url: remoteUrl, target_path: targetPath || null }),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Workspace clone failed (${res.status})`)
  return data
}

export async function connectRoomWorkspace(roomId, path) {
  const res = await authFetch(`/collab/rooms/${encodeURIComponent(roomId)}/workspace`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path }),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Room workspace connect failed (${res.status})`)
  return data
}

export async function cloneRoomWorkspace(roomId, remoteUrl, targetPath) {
  const res = await authFetch(`/collab/rooms/${encodeURIComponent(roomId)}/workspace/clone`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ remote_url: remoteUrl, target_path: targetPath || null }),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Room workspace clone failed (${res.status})`)
  return data
}

export async function joinRoom(roomId, body = {}) {
  const res = await authFetch(`/collab/rooms/${encodeURIComponent(roomId)}/join`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || 'Join room failed')
  return data
}

export async function fetchRoom(roomId) {
  const res = await authFetch(`/collab/rooms/${encodeURIComponent(roomId)}`)
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Room load failed (${res.status})`)
  return data
}

export async function fetchRoomHandoff(roomId) {
  const res = await authFetch(`/collab/rooms/${encodeURIComponent(roomId)}/handoff`)
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Handoff load failed (${res.status})`)
  return data
}

export async function fetchWorkDashboard(roomId) {
  const res = await authFetch(`/collab/rooms/${encodeURIComponent(roomId)}/work-dashboard`)
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Work dashboard load failed (${res.status})`)
  return data
}

export async function fetchSpeakerRoom(roomId) {
  const res = await authFetch(`/speakers/rooms/${encodeURIComponent(roomId)}`)
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Speaker state failed (${res.status})`)
  return data
}

export async function fetchSpeakerValidation(roomId) {
  const res = await authFetch(`/speakers/rooms/${encodeURIComponent(roomId)}/validation`)
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Speaker validation failed (${res.status})`)
  return data
}

export async function fetchAgentSettings() {
  const res = await authFetch('/collab/agent-settings')
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Agent settings failed (${res.status})`)
  return data
}

export async function fetchAgentRuns(roomId, limit = 8) {
  const res = await authFetch(
    `/agents/rooms/${encodeURIComponent(roomId)}/runs?limit=${encodeURIComponent(String(limit))}`,
  )
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Agent runs failed (${res.status})`)
  return data.runs || []
}

export async function fetchAgentAssignments(roomId, limit = 8) {
  const res = await authFetch(
    `/agents/rooms/${encodeURIComponent(roomId)}/assignments?limit=${encodeURIComponent(String(limit))}`,
  )
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Agent assignments failed (${res.status})`)
  return data.assignments || []
}

export async function fetchAgentLLMRouting(roomId) {
  const res = await authFetch(`/agents/rooms/${encodeURIComponent(roomId)}/llm-routing`)
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Agent LLM routing failed (${res.status})`)
  return data
}

export async function updateAgentLLMRoute(roomId, route) {
  const res = await authFetch(`/agents/rooms/${encodeURIComponent(roomId)}/llm-routing`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(route),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Agent LLM route update failed (${res.status})`)
  return data
}

export async function preflightAgentLLMRoute(roomId, route) {
  const res = await authFetch(`/agents/rooms/${encodeURIComponent(roomId)}/llm-routing/preflight`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(route),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Agent LLM route preflight failed (${res.status})`)
  return data
}

export async function fetchLLMProviders() {
  const res = await authFetch('/llm/providers')
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `LLM providers failed (${res.status})`)
  return data
}

export async function connectLLMProviderApiKey(provider, body) {
  const res = await authFetch(`/llm/providers/${encodeURIComponent(provider)}/credentials/api-key`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `LLM provider connection failed (${res.status})`)
  return data
}

export async function preflightLLMProvider(provider, body = {}) {
  const res = await authFetch(`/llm/providers/${encodeURIComponent(provider)}/preflight`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `LLM provider preflight failed (${res.status})`)
  return data
}

export async function disconnectLLMProvider(provider) {
  const res = await authFetch(`/llm/providers/${encodeURIComponent(provider)}/credential`, {
    method: 'DELETE',
  })
  if (!res.ok) {
    let detail = ''
    try {
      detail = (await res.json()).detail
    } catch {
      detail = ''
    }
    throw new Error(detail || `LLM provider disconnect failed (${res.status})`)
  }
  return { disconnected: true, provider }
}

export async function createAgentAssignment(roomId, assignment) {
  const res = await authFetch(`/agents/rooms/${encodeURIComponent(roomId)}/assignments`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(assignment),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Agent assignment failed (${res.status})`)
  return data
}

export async function dispatchAgentAssignment(roomId, assignmentId) {
  const res = await authFetch(
    `/agents/rooms/${encodeURIComponent(roomId)}/assignments/${encodeURIComponent(assignmentId)}/dispatch`,
    { method: 'POST' },
  )
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Agent assignment dispatch failed (${res.status})`)
  return data
}

export async function cancelAgentAssignment(roomId, assignmentId, reason = 'cancelled from queue') {
  const res = await authFetch(
    `/agents/rooms/${encodeURIComponent(roomId)}/assignments/${encodeURIComponent(assignmentId)}/cancel`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason }),
    },
  )
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Agent assignment cancel failed (${res.status})`)
  return data
}

export async function retryAgentAssignment(roomId, assignmentId) {
  const res = await authFetch(
    `/agents/rooms/${encodeURIComponent(roomId)}/assignments/${encodeURIComponent(assignmentId)}/retry`,
    { method: 'POST' },
  )
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Agent assignment retry failed (${res.status})`)
  return data
}

export async function clearCompletedAgentAssignments(roomId) {
  const res = await authFetch(
    `/agents/rooms/${encodeURIComponent(roomId)}/assignments/clear-completed`,
    { method: 'POST' },
  )
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Agent assignment clear failed (${res.status})`)
  return data
}

export async function startAgentRun(roomId, prompt, source = 'manual') {
  const res = await authFetch(`/agents/rooms/${encodeURIComponent(roomId)}/runs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt, source }),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Agent run failed (${res.status})`)
  return data
}

export async function cancelAgentRun(roomId, runId, reason = 'cancelled from console') {
  const res = await authFetch(
    `/agents/rooms/${encodeURIComponent(roomId)}/runs/${encodeURIComponent(runId)}/cancel`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason }),
    },
  )
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Agent cancel failed (${res.status})`)
  return data
}

export async function fetchExternalAgentProviders() {
  const res = await authFetch('/external-agents/providers')
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `External agents failed (${res.status})`)
  return data
}

export async function fetchExternalAgentPreflight() {
  const res = await authFetch('/external-agents/preflight')
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `External agent preflight failed (${res.status})`)
  return data
}

export async function fetchExternalAgentSetupGuide() {
  const res = await authFetch('/external-agents/setup-guide')
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `External agent setup guide failed (${res.status})`)
  return data
}

export async function fetchExternalAgentCapabilities() {
  const res = await authFetch('/external-agents/capabilities')
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `External agent capabilities failed (${res.status})`)
  return data
}

export async function recommendExternalAgent(task, options = {}) {
  const res = await authFetch('/external-agents/recommendations', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      task,
      preferred_provider: options.preferredProvider || null,
      mode: options.mode || null,
      model: options.model || null,
    }),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `External agent recommendation failed (${res.status})`)
  return data
}

export async function startExternalAgentOAuth(provider, scopes = []) {
  const res = await authFetch(`/external-agents/providers/${encodeURIComponent(provider)}/oauth/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ scopes }),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `External OAuth start failed (${res.status})`)
  return data
}

export async function connectExternalAgentLocalCli(provider, options = {}) {
  const res = await authFetch(`/external-agents/providers/${encodeURIComponent(provider)}/credentials/local-cli`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      command: options.command || '',
      command_template: options.command_template || null,
      account_label: options.account_label || null,
    }),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `External local CLI connect failed (${res.status})`)
  return data
}

export async function runExternalAgent(roomId, provider, prompt, mode = 'patch', model = '') {
  const res = await authFetch(`/external-agents/rooms/${encodeURIComponent(roomId)}/runs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ provider, prompt, mode, model: model || null, source: 'console' }),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `External agent run failed (${res.status})`)
  return data
}

export async function updateAgentSettings(roomId, settings) {
  const res = await authFetch(`/collab/rooms/${encodeURIComponent(roomId)}/agent-settings`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(settings),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Agent settings update failed (${res.status})`)
  return data
}

export async function fetchRoomMemory(roomId, filters = {}) {
  const params = new URLSearchParams()
  if (filters.q) params.set('q', filters.q)
  if (filters.kind) params.set('kind', filters.kind)
  if (filters.status) params.set('status', filters.status)
  if (filters.limit) params.set('limit', String(filters.limit))
  const query = params.toString()
  const res = await authFetch(
    `/collab/rooms/${encodeURIComponent(roomId)}/memory${query ? `?${query}` : ''}`,
  )
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Memory load failed (${res.status})`)
  return data
}

export async function fetchRoomAudit(roomId, filters = {}) {
  const params = new URLSearchParams()
  if (filters.kind) params.set('kind', filters.kind)
  if (filters.limit) params.set('limit', String(filters.limit))
  const query = params.toString()
  const res = await authFetch(
    `/collab/rooms/${encodeURIComponent(roomId)}/audit${query ? `?${query}` : ''}`,
    { timeoutMs: 45000 },
  )
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Audit load failed (${res.status})`)
  return data
}

export async function queryRoomMemory(roomId, question, limit = 8) {
  const res = await authFetch(`/collab/rooms/${encodeURIComponent(roomId)}/memory/query`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, limit }),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Memory query failed (${res.status})`)
  return data
}

export async function fetchRoomMemoryHealth(roomId, staleDays = 7) {
  const params = new URLSearchParams({ stale_days: String(staleDays) })
  const res = await authFetch(
    `/collab/rooms/${encodeURIComponent(roomId)}/memory/health?${params.toString()}`,
  )
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Memory health failed (${res.status})`)
  return data
}

export async function queryRoomRag(roomId, question, limit = 8) {
  const res = await authFetch(`/collab/rooms/${encodeURIComponent(roomId)}/rag/query`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, limit, include_code: true }),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `RAG query failed (${res.status})`)
  return data
}

export async function queryRoomProvenance(roomId, question, limit = 8) {
  const res = await authFetch(`/collab/rooms/${encodeURIComponent(roomId)}/provenance/query`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, limit, include_code: true, include_ontology: true }),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Provenance query failed (${res.status})`)
  return data
}

export async function rebuildRoomRagIndex(roomId) {
  const res = await authFetch(`/collab/rooms/${encodeURIComponent(roomId)}/rag/index`, {
    method: 'POST',
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `RAG index failed (${res.status})`)
  return data
}

export async function queryRoomAudit(roomId, question, limit = 8) {
  const res = await authFetch(`/collab/rooms/${encodeURIComponent(roomId)}/audit/query`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, limit }),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Audit query failed (${res.status})`)
  return data
}

export async function routeRoomCommand(roomId, text, memoryQuestionMode = 'broad') {
  const res = await authFetch(`/collab/rooms/${encodeURIComponent(roomId)}/commands/route`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, memory_question_mode: memoryQuestionMode }),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Command route failed (${res.status})`)
  return data
}

export async function approveAction(roomId, actionId, note = '') {
  const res = await authFetch(
    `/collab/rooms/${encodeURIComponent(roomId)}/actions/${encodeURIComponent(actionId)}/approve`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ note }),
    },
  )
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Action approval failed (${res.status})`)
  return data
}

export async function rejectAction(roomId, actionId, note = '') {
  const res = await authFetch(
    `/collab/rooms/${encodeURIComponent(roomId)}/actions/${encodeURIComponent(actionId)}/reject`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ note }),
    },
  )
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Action rejection failed (${res.status})`)
  return data
}

export async function commitAction(roomId, actionId, message = '') {
  const res = await authFetch(
    `/collab/rooms/${encodeURIComponent(roomId)}/actions/${encodeURIComponent(actionId)}/commit`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message }),
    },
  )
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Action commit failed (${res.status})`)
  return data
}

export async function createPullRequestPlan(roomId, actionId, options = {}) {
  const res = await authFetch(
    `/collab/rooms/${encodeURIComponent(roomId)}/actions/${encodeURIComponent(actionId)}/pull-request`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ dry_run: true, ...options }),
    },
  )
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Pull request plan failed (${res.status})`)
  return data
}

export async function fetchWorkspaceTree(limit = 80, roomId = null) {
  const base = roomId
    ? `/collab/rooms/${encodeURIComponent(roomId)}/workspace/tree`
    : '/workspace/tree'
  const res = await authFetch(`${base}?limit=${encodeURIComponent(String(limit))}`)
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Workspace tree failed (${res.status})`)
  return data
}

export async function fetchGitStatus(roomId = null) {
  const path = roomId
    ? `/collab/rooms/${encodeURIComponent(roomId)}/workspace/git/status`
    : '/workspace/git/status'
  const res = await authFetch(path)
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Git status failed (${res.status})`)
  return data
}

export async function fetchGitDiff(roomId = null) {
  const path = roomId
    ? `/collab/rooms/${encodeURIComponent(roomId)}/workspace/git/diff`
    : '/workspace/git/diff'
  const res = await authFetch(path)
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Git diff failed (${res.status})`)
  return data
}

export async function fetchCacheStatus() {
  const res = await authFetch('/system/cache/status')
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Cache status failed (${res.status})`)
  return data
}

export async function fetchRuntimeStatus() {
  const res = await authFetch('/system/runtime/status')
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Runtime status failed (${res.status})`)
  return data
}

export async function fetchSystemReadiness() {
  const res = await authFetch('/system/readiness')
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `System readiness failed (${res.status})`)
  return data
}

export async function fetchLocalDoctor() {
  const res = await authFetch('/system/local-doctor')
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Local doctor failed (${res.status})`)
  return data
}

export async function fetchTargetReadiness() {
  const res = await authFetch('/system/target-readiness')
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Target readiness failed (${res.status})`)
  return data
}

export async function fetchEventStoreReadiness() {
  const res = await authFetch('/system/event-store/readiness')
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Event store readiness failed (${res.status})`)
  return data
}

export async function fetchDeploymentHardening() {
  const res = await authFetch('/system/deployment/hardening')
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Deployment hardening failed (${res.status})`)
  return data
}

export async function fetchProductionCutover() {
  const res = await authFetch('/system/deployment/cutover')
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Production cutover failed (${res.status})`)
  return data
}

export async function fetchOperatorAcceptance({ runHarnesses = false } = {}) {
  const query = runHarnesses ? '?run_harnesses=true' : ''
  const res = await authFetch(`/system/operator-acceptance${query}`)
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Operator acceptance failed (${res.status})`)
  return data
}

export async function fetchDemoGateRun() {
  const res = await authFetch('/system/demo/gates/runs/current')
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Demo gate status failed (${res.status})`)
  return data
}

export async function startDemoGateRun(gateId) {
  const res = await authFetch(`/system/demo/gates/${encodeURIComponent(gateId)}/runs`, { method: 'POST' })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Demo gate start failed (${res.status})`)
  return data
}

export async function fetchSpeakerWarmup() {
  const res = await authFetch('/system/speaker/warmup')
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Speaker warmup failed (${res.status})`)
  return data
}

export async function startSpeakerWarmup() {
  const res = await authFetch('/system/speaker/warmup', { method: 'POST' })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Speaker warmup start failed (${res.status})`)
  return data
}

export async function fetchSpeakerVerification() {
  const res = await authFetch('/system/speaker/verification')
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Speaker verification failed (${res.status})`)
  return data
}

export async function startSpeakerVerification(file, { requireMultipleSpeakers = true } = {}) {
  const form = new FormData()
  form.append('audio', file)
  form.append('require_multiple_speakers', requireMultipleSpeakers ? 'true' : 'false')
  const res = await authFetch('/system/speaker/verification', {
    method: 'POST',
    body: form,
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Speaker verification start failed (${res.status})`)
  return data
}

export async function startGeneratedSpeakerVerification({ requireMultipleSpeakers = true } = {}) {
  const form = new FormData()
  form.append('require_multiple_speakers', requireMultipleSpeakers ? 'true' : 'false')
  const res = await authFetch('/system/speaker/verification/generated', {
    method: 'POST',
    body: form,
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Generated speaker verification failed (${res.status})`)
  return data
}

export async function clearCache() {
  const res = await authFetch('/system/cache/clear', { method: 'POST' })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Cache clear failed (${res.status})`)
  return data
}

export async function searchWorkspace(query, limit = 8) {
  const params = new URLSearchParams({ q: query, limit: String(limit) })
  const res = await authFetch(`/workspace/search?${params.toString()}`)
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Workspace search failed (${res.status})`)
  return data
}

export async function queryRoomCode(roomId, question, limit = 8) {
  const res = await authFetch(`/collab/rooms/${encodeURIComponent(roomId)}/code/query`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, limit }),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Code query failed (${res.status})`)
  return data
}

export async function mapSpeaker(roomId, speakerLabel, userId) {
  const res = await authFetch(`/speakers/rooms/${encodeURIComponent(roomId)}/mappings`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      speaker_label: speakerLabel,
      user_id: userId,
      confidence: 1,
      source: 'manual',
    }),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Speaker mapping failed (${res.status})`)
  return data
}

export async function ingestSpeakerSegments(roomId, body) {
  const res = await authFetch(`/speakers/rooms/${encodeURIComponent(roomId)}/segments`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || `Speaker segment ingest failed (${res.status})`)
  return data
}

export function speakerLiveUrl(roomId) {
  const token = encodeURIComponent(getToken() || '')
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}/speakers/rooms/${encodeURIComponent(roomId)}/live?token=${token}`
}

export function roomEventsUrl(roomId) {
  const token = encodeURIComponent(getToken() || '')
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}/collab/rooms/${encodeURIComponent(roomId)}/events?token=${token}`
}

export async function leaveRoom(roomId) {
  const res = await authFetch(`/collab/rooms/${encodeURIComponent(roomId)}/leave`, {
    method: 'POST',
  })
  if (!res.ok) throw new Error(`Leave room failed (${res.status})`)
  return res.json()
}

export async function processText(text, sessionId, roomId, incidentContext) {
  const res = await authFetch('/voice/process-text', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      text,
      session_id: sessionId,
      room_id: roomId,
      incident_context: incidentContext,
      include_tts: true,
    }),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || res.statusText)
  return data
}

export function incidentContextFromBootstrap(bootstrap) {
  const ws = bootstrap?.workspace || {}
  return {
    incident_id: null,
    service: ws.name || null,
    title: ws.readme_line || ws.name || '',
    workspace: ws.name || null,
    branch: ws.branch || null,
    repository_connected: !!ws.connected,
  }
}
