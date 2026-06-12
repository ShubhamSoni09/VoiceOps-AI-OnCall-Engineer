// In dev the Vite proxy forwards /auth, /voice, /console to localhost:8001.
// In production (separate Render services) set VITE_API_URL to the backend URL,
// e.g. https://voiceops-api.onrender.com  — no trailing slash.
const API_BASE = (import.meta.env.VITE_API_URL || '').replace(/\/$/, '')

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
  const res = await fetch(API_BASE + url, { ...options, headers })
  if (res.status === 401) {
    clearToken()
    window.location.reload()
    throw new Error('Session expired')
  }
  return res
}

export async function login(email, password) {
  const res = await fetch(API_BASE + '/auth/login', {
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

/**
 * Stream workspace events from the backend.
 * onEvent(event) is called for each SSE event object.
 * Resolves with the final VoiceProcessResponse data when done.
 */
export async function processTextStream(text, sessionId, incidentContext, onEvent) {
  const token = getToken()
  const res = await fetch(API_BASE + '/voice/process-text-stream', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ text, session_id: sessionId, incident_context: incidentContext }),
  })
  if (res.status === 401) {
    clearToken()
    window.location.reload()
    throw new Error('Session expired')
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || res.statusText)
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  let finalData = null

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    const parts = buf.split('\n\n')
    buf = parts.pop()
    for (const part of parts) {
      const line = part.trim()
      if (!line.startsWith('data:')) continue
      try {
        const event = JSON.parse(line.slice(5).trim())
        onEvent(event)
        if (event.type === 'done') finalData = event.data
        if (event.type === 'error') throw new Error(event.message)
      } catch (e) {
        if (e.message && !e.message.startsWith('JSON')) throw e
      }
    }
  }
  return finalData
}

export function incidentContextFromBootstrap(bootstrap) {
  const ws = bootstrap?.workspace || {}
  const active = (bootstrap?.incidents || []).filter(
    (i) => String(i.status || '').toLowerCase() !== 'resolved',
  )
  const primary = active[0] || null
  return {
    incident_id: primary?.id || null,
    service: primary?.service || ws.name || null,
    title: primary?.title || ws.readme_line || ws.name || '',
    severity: primary?.severity || null,
    workspace: ws.name || null,
    branch: ws.branch || null,
    repository_connected: !!ws.connected,
    open_incidents: active.map((i) => ({
      id: i.id,
      title: i.title,
      service: i.service,
      severity: i.severity,
    })),
  }
}
