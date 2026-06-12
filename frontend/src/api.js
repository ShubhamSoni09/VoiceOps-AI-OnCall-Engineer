const TOKEN_KEY = 'voiceops_token'

export function getToken() {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token) {
  localStorage.setItem(TOKEN_KEY, token)
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY)
}

export async function authFetch(url, options = {}) {
  const headers = { ...options.headers }
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`
  const res = await fetch(url, { ...options, headers })
  if (res.status === 401) {
    clearToken()
    window.location.reload()
    throw new Error('Session expired')
  }
  return res
}

export async function login(email, password) {
  const res = await fetch('/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || 'Login failed')
  setToken(data.access_token)
  return data.user
}

export async function fetchBootstrap() {
  const res = await authFetch('/console/bootstrap')
  if (!res.ok) throw new Error(`Bootstrap failed (${res.status})`)
  return res.json()
}

export async function processText(text, sessionId, incidentContext) {
  const res = await authFetch('/voice/process-text', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      text,
      session_id: sessionId,
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
