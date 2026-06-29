import { workspaceTitle } from '../workspaceHeaderModel.js'
import { APP_NAME } from '../branding.js'

function roomStatusCopy(roomStatus) {
  const state = String(roomStatus?.state || 'ready')
  if (state === 'issue') {
    return {
      badge: 'ROOM SYNC ISSUE',
      badgeClass: 'act',
      detail: roomStatus?.message || 'meeting state unavailable',
      codeActions: 'check room sync',
    }
  }
  if (state === 'checking' || state === 'connecting' || state === 'syncing') {
    return {
      badge: 'ROOM SYNCING',
      badgeClass: 'act',
      detail: roomStatus?.message || 'loading meeting state',
      codeActions: 'syncing approvals',
    }
  }
  return {
    badge: 'SESSION READY',
    badgeClass: 'ok',
    detail: roomStatus?.dashboardSyncing ? roomStatus?.message || 'work dashboard syncing' : null,
    codeActions: roomStatus?.dashboardSyncing ? 'dashboard syncing' : 'approval first',
  }
}

function consoleTitle(workspace, agentName) {
  const name = String(agentName || '').trim()
  if (name && name.toLowerCase() !== APP_NAME.toLowerCase()) return `${name} - AI Teammate Console`
  return workspace?.connected ? workspaceTitle(workspace) : `${APP_NAME} - AI Teammate Console`
}

function workspaceLinkDetail(workspace) {
  if (workspace?.connected && workspace?.is_git_repo === false) return 'local folder'
  if (workspace?.remote_kind === 'github') return 'GitHub remote'
  if (workspace?.remote_kind === 'git') return 'git remote'
  const remote = String(workspace?.remote_url || '').trim()
  if (/github\.com/i.test(remote)) return 'GitHub remote'
  if (remote) return 'remote linked'
  return workspace?.is_git_repo ? 'local git repo' : 'local workspace'
}

export default function IncidentHeader({ workspace, user, roomStatus, agentName = APP_NAME }) {
  const workspaceName = workspace?.name || 'workspace'
  const title = consoleTitle(workspace, agentName)
  const teammateName = user?.name || 'current teammate'

  if (!workspace) {
    return (
      <div className="inc-header">
        <div className="top">
          <span className="sev-badge act"><span className="dot" />CHECKING</span>
          <span className="mono" style={{ fontSize: 12, color: 'var(--ink-faint)' }}>loading workspace</span>
        </div>
        <h1>{title}</h1>
        <div className="inc-facts">
          <div className="fact"><span className="k">Repository</span><span className="v mono">checking</span></div>
          <div className="fact"><span className="k">Branch</span><span className="v mono">checking</span></div>
          <div className="fact"><span className="k">Signed in</span><span className="v">{teammateName}</span></div>
          <div className="fact"><span className="k">Code actions</span><span className="v">checking workspace</span></div>
        </div>
      </div>
    )
  }

  if (!workspace?.connected) {
    return (
      <div className="inc-header">
        <div className="top">
          <span className="sev-badge"><span className="dot" />SETUP NEEDED</span>
          <span className="mono" style={{ fontSize: 12, color: 'var(--ink-faint)' }}>repo required for patches</span>
        </div>
        <h1>{title}</h1>
        <div className="inc-facts">
          <div className="fact"><span className="k">Repository</span><span className="v mono warnv">not connected</span></div>
          <div className="fact"><span className="k">Branch</span><span className="v mono">connect repo first</span></div>
          <div className="fact"><span className="k">Signed in</span><span className="v">{teammateName}</span></div>
          <div className="fact"><span className="k">Code actions</span><span className="v">connect repo first</span></div>
        </div>
      </div>
    )
  }

  const roomCopy = roomStatusCopy(roomStatus)
  return (
    <div className="inc-header">
      <div className="top">
        <span className={`sev-badge ${roomCopy.badgeClass}`}><span className="dot" />{roomCopy.badge}</span>
        <span className="mono" style={{ fontSize: 12, color: 'var(--ink-faint)' }}>
          {roomCopy.detail || workspaceLinkDetail(workspace)}
        </span>
      </div>
      <h1>{title}</h1>
      <div className="inc-facts">
        <div className="fact"><span className="k">Repository</span><span className="v mono" title={workspaceName}>{workspaceName}</span></div>
        <div className="fact"><span className="k">Branch</span><span className="v mono" title={workspace.branch || '-'}>{workspace.branch || '-'}</span></div>
        <div className="fact"><span className="k">Signed in</span><span className="v">{teammateName}</span></div>
        <div className="fact"><span className="k">Code actions</span><span className="v">{roomCopy.codeActions}</span></div>
      </div>
    </div>
  )
}
