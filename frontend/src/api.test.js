import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  API_RUNTIME_UNAVAILABLE,
  authFetch,
  browserFetch,
  cloneRoomWorkspace,
  connectRoomWorkspace,
  connectLLMProviderApiKey,
  disconnectLLMProvider,
  fetchLLMProviders,
  fetchGitDiff,
  fetchGitStatus,
  fetchRoomAudit,
  fetchUsers,
  fetchWorkspaceTree,
  fetchWithTimeout,
  preflightLLMProvider,
  updateUserProjects,
} from './api.js'

describe('api runtime guard', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('returns the browser fetch implementation when available', () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    browserFetch()('/health')

    expect(fetchMock).toHaveBeenCalledWith('/health')
  })

  it('throws a clear error when fetch is missing', () => {
    vi.stubGlobal('fetch', undefined)

    expect(() => browserFetch()).toThrow(API_RUNTIME_UNAVAILABLE)
  })

  it('clears auth and notifies the app on 401 without forcing reload', async () => {
    const removeItem = vi.fn()
    const dispatchEvent = vi.fn()
    vi.stubGlobal('localStorage', {
      getItem: vi.fn(() => 'expired-token'),
      removeItem,
    })
    vi.stubGlobal('window', {
      dispatchEvent,
    })
    vi.stubGlobal('CustomEvent', function CustomEvent(type) {
      this.type = type
    })
    vi.stubGlobal('fetch', vi.fn(async () => ({ status: 401 })))

    await expect(authFetch('/console/bootstrap')).rejects.toThrow('Session expired')

    expect(removeItem).toHaveBeenCalledWith('voiceops_token')
    expect(dispatchEvent.mock.calls[0][0].type).toBe('voiceops:session-expired')
    expect(window.location?.reload).toBeUndefined()
  })

  it('fails hanging API requests with a clear timeout', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('fetch', vi.fn((_url, options = {}) => new Promise((_resolve, reject) => {
      options.signal?.addEventListener('abort', () => {
        const error = new Error('aborted')
        error.name = 'AbortError'
        reject(error)
      })
    })))

    const request = fetchWithTimeout('/console/bootstrap', { timeoutMs: 25 })
    const assertion = expect(request).rejects.toThrow('Request timed out after 25ms: /console/bootstrap')
    await vi.advanceTimersByTimeAsync(25)

    await assertion
    vi.useRealTimers()
  })

  it('does not override caller-provided abort signals', async () => {
    const controller = new AbortController()
    const fetchMock = vi.fn(async (_url, options = {}) => ({
      status: 200,
      signal: options.signal,
    }))
    vi.stubGlobal('fetch', fetchMock)

    const response = await fetchWithTimeout('/workspace/tree', {
      signal: controller.signal,
      timeoutMs: 25,
    })

    expect(response.signal).toBe(controller.signal)
  })

  it('uses resource-oriented LLM provider credential endpoints', async () => {
    vi.stubGlobal('localStorage', { getItem: vi.fn(() => 'token'), removeItem: vi.fn() })
    const fetchMock = vi.fn(async (url, options = {}) => ({
      ok: true,
      status: 200,
      json: async () => {
        if (url === '/llm/providers') return [{ provider: 'anthropic', connected: false }]
        if (url.includes('/preflight')) return { ready: true, provider: 'anthropic' }
        return { provider: 'anthropic', model: 'claude-sonnet-4-5' }
      },
      options,
    }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(fetchLLMProviders()).resolves.toEqual([{ provider: 'anthropic', connected: false }])
    await connectLLMProviderApiKey('anthropic', {
      api_key: 'sk-ant-test',
      model: 'claude-sonnet-4-5',
      account_label: 'Team Claude',
    })
    await preflightLLMProvider('anthropic', { model: 'claude-sonnet-4-5', require_json: true })

    expect(fetchMock.mock.calls[0][0]).toBe('/llm/providers')
    expect(fetchMock.mock.calls[1][0]).toBe('/llm/providers/anthropic/credentials/api-key')
    expect(fetchMock.mock.calls[1][1]).toMatchObject({ method: 'POST', headers: { 'Content-Type': 'application/json' } })
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toMatchObject({
      api_key: 'sk-ant-test',
      model: 'claude-sonnet-4-5',
    })
    expect(fetchMock.mock.calls[2][0]).toBe('/llm/providers/anthropic/preflight')
    expect(JSON.parse(fetchMock.mock.calls[2][1].body)).toMatchObject({ require_json: true })
  })

  it('uses room-scoped workspace endpoints for repo connect and clone', async () => {
    vi.stubGlobal('localStorage', { getItem: vi.fn(() => 'token'), removeItem: vi.fn() })
    const fetchMock = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ room: { id: 'main', workspace_path: '/tmp/repo' } }),
    }))
    vi.stubGlobal('fetch', fetchMock)

    await connectRoomWorkspace('main', '/tmp/repo')
    await cloneRoomWorkspace('main', 'https://github.com/team/app.git', '')

    expect(fetchMock.mock.calls[0][0]).toBe('/collab/rooms/main/workspace')
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: 'PUT' })
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ path: '/tmp/repo' })
    expect(fetchMock.mock.calls[1][0]).toBe('/collab/rooms/main/workspace/clone')
    expect(fetchMock.mock.calls[1][1]).toMatchObject({ method: 'POST' })
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({
      remote_url: 'https://github.com/team/app.git',
      target_path: null,
    })
  })

  it('uses room-scoped workspace endpoints for workspace reads when a room id is provided', async () => {
    vi.stubGlobal('localStorage', { getItem: vi.fn(() => 'token'), removeItem: vi.fn() })
    const fetchMock = vi.fn(async (url) => ({
      ok: true,
      status: 200,
      json: async () => {
        if (url.includes('/tree')) return { files: [] }
        if (url.includes('/diff')) return { diff: '', files_changed: [] }
        return { branch: 'main', dirty: false, files: [] }
      },
    }))
    vi.stubGlobal('fetch', fetchMock)

    await fetchWorkspaceTree(60, 'alpha-room')
    await fetchGitStatus('alpha-room')
    await fetchGitDiff('alpha-room')

    expect(fetchMock.mock.calls[0][0]).toBe('/collab/rooms/alpha-room/workspace/tree?limit=60')
    expect(fetchMock.mock.calls[1][0]).toBe('/collab/rooms/alpha-room/workspace/git/status')
    expect(fetchMock.mock.calls[2][0]).toBe('/collab/rooms/alpha-room/workspace/git/diff')
  })

  it('uses admin auth endpoints for project access management', async () => {
    vi.stubGlobal('localStorage', { getItem: vi.fn(() => 'token'), removeItem: vi.fn() })
    const fetchMock = vi.fn(async (url) => ({
      ok: true,
      status: 200,
      json: async () => (url === '/auth/users'
        ? [{ id: 'user-priya', projects: ['alpha'] }]
        : { id: 'user-priya', projects: ['alpha', 'mobile'] }),
    }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(fetchUsers()).resolves.toEqual([{ id: 'user-priya', projects: ['alpha'] }])
    await expect(updateUserProjects('user-priya', ['alpha', 'mobile'])).resolves.toEqual({
      id: 'user-priya',
      projects: ['alpha', 'mobile'],
    })

    expect(fetchMock.mock.calls[0][0]).toBe('/auth/users')
    expect(fetchMock.mock.calls[1][0]).toBe('/auth/users/user-priya/projects')
    expect(fetchMock.mock.calls[1][1]).toMatchObject({ method: 'PUT', headers: { 'Content-Type': 'application/json' } })
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ projects: ['alpha', 'mobile'] })
  })

  it('loads room audit through the room-scoped audit endpoint', async () => {
    vi.stubGlobal('localStorage', { getItem: vi.fn(() => 'token'), removeItem: vi.fn() })
    const fetchMock = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => [{ id: 'evt-1' }],
    }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(fetchRoomAudit('main', { limit: 8 })).resolves.toEqual([{ id: 'evt-1' }])

    expect(fetchMock.mock.calls[0][0]).toBe('/collab/rooms/main/audit?limit=8')
  })

  it('disconnects an LLM provider without requiring a response body', async () => {
    vi.stubGlobal('localStorage', { getItem: vi.fn(() => 'token'), removeItem: vi.fn() })
    const fetchMock = vi.fn(async () => ({ ok: true, status: 204 }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(disconnectLLMProvider('openai')).resolves.toEqual({ disconnected: true, provider: 'openai' })

    expect(fetchMock).toHaveBeenCalledWith('/llm/providers/openai/credential', expect.objectContaining({
      method: 'DELETE',
    }))
  })
})
