import { MoonStars, Sun, SignOut } from '@phosphor-icons/react'
import { clearToken } from '../api.js'
import { APP_INITIAL, APP_NAME } from '../branding.js'
import { teammateRoleLabel } from '../roleLabels.js'

function compactToken(value, maxLength) {
  const text = String(value || '').trim()
  if (!text || text.length <= maxLength) return text
  return `${text.slice(0, Math.max(1, maxLength - 1))}…`
}

export function workspaceStatusLabels(workspace) {
  if (!workspace) {
    return { label: 'Checking repo', title: 'Checking repo' }
  }
  if (!workspace.connected) {
    return { label: 'No repo connected', title: 'No repo connected' }
  }
  const repo = workspace.name || 'Repository'
  const branch = workspace.branch || ''
  const title = branch ? `${repo} · ${branch}` : repo
  const label = branch
    ? `${compactToken(repo, 20)} · ${compactToken(branch, 18)}`
    : compactToken(repo, 28)
  return { label, title }
}

export default function TopBar({
  theme,
  onToggleTheme,
  user,
  statusCounts,
  workspace,
  roomSyncStatus,
  meetingClosureSmoke,
}) {
  const workspaceKnown = Boolean(workspace)
  const workspaceStatus = workspaceStatusLabels(workspace)
  const syncLive = roomSyncStatus === 'live'
  const syncLabel = !workspaceKnown || roomSyncStatus === 'connecting'
    ? 'Connecting'
    : syncLive
      ? 'Room live'
      : 'Reconnecting'
  const smokeTone = meetingClosureSmoke?.status === 'completed' || meetingClosureSmoke?.status === 'ready'
    ? 'ok'
    : meetingClosureSmoke?.status === 'failed'
      ? 'crit'
      : 'act'

  return (
    <header className="topbar">
      <div className="brand">
        <div className="logo">{APP_INITIAL}</div>
        <b>{APP_NAME}</b>
        <span className="tag">meeting code workspace</span>
      </div>

      <div className="statusbar">
        <span
          className={`pill workspace ${workspace?.connected ? 'ok' : 'act'}`}
          title={workspaceStatus.title}
          aria-label={`Workspace: ${workspaceStatus.title}`}
        >
          <span className="dot" />
          <span className="pill-label">{workspaceStatus.label}</span>
        </span>
        <span className={`pill sync ${syncLive ? 'ok' : 'act'}`}>
          <span className="dot" />
          {syncLabel}
        </span>
        {meetingClosureSmoke && (
          <span
            className={`pill smoke ${smokeTone}`}
            data-testid="meeting-closure-smoke-status"
            title={(meetingClosureSmoke.steps || []).join(' > ')}
          >
            <span className="dot" />
            E2E gate {meetingClosureSmoke.status || 'running'}
          </span>
        )}
      </div>

      <div className="topright">
        <button className="icon-btn" onClick={onToggleTheme} title="Toggle light / dark" aria-label="Toggle theme">
          {theme === 'dark' ? <MoonStars size={18} /> : <Sun size={18} />}
        </button>
        {user && (
          <div className="oncall">
            <span className="who">
              <small>{teammateRoleLabel(user.role_label)}</small>
              <span>{user.name}</span>
            </span>
            <span className="avatar">{user.initials}</span>
          </div>
        )}
        <button
          className="icon-btn"
          onClick={() => { clearToken(); window.location.reload() }}
          title="Sign out"
          aria-label="Sign out"
        >
          <SignOut size={18} />
        </button>
      </div>
    </header>
  )
}
