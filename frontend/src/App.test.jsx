import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { activeWorkspaceInfo } from './App.jsx'

describe('activeWorkspaceInfo', () => {
  it('keeps bootstrap git metadata when the room uses the same workspace path', () => {
    const workspace = {
      connected: true,
      path: '/repo/sandbox',
      configured_workspace: '/repo/sandbox',
      name: 'sandbox',
      branch: 'feat/ui-incident-dashboard',
      remote_url: 'https://github.com/team/voiceops.git',
      remote_kind: 'github',
      remote_web_url: 'https://github.com/team/voiceops',
      is_git_repo: true,
    }

    expect(activeWorkspaceInfo(workspace, { workspace_path: '/repo/sandbox' })).toMatchObject({
      branch: 'feat/ui-incident-dashboard',
      remote_kind: 'github',
      remote_web_url: 'https://github.com/team/voiceops',
      is_git_repo: true,
    })
  })
})

describe('external agent OAuth', () => {
  it('opens the provider authorization URL from the connected agents flow', () => {
    const source = readFileSync(new URL('./App.jsx', import.meta.url), 'utf8')
    expect(source).toContain('window.open(result.authorization_url')
    expect(source).toContain('refreshExternalAgents, 5000')
  })
})
