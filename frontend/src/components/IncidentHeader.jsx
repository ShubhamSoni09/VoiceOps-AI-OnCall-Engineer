export default function IncidentHeader({ workspace, user, github, onSelectRepo }) {
  const showRepoPicker = github?.connected && onSelectRepo

  if (!workspace?.connected) {
    return (
      <div className="inc-header">
        <div className="top">
          <span className="sev-badge"><span className="dot" />STANDBY</span>
          <span className="mono" style={{ fontSize: 12, color: 'var(--ink-faint)' }}>—</span>
        </div>
        <h1>{github?.repo_required ? 'Select a repository' : 'No repository connected'}</h1>
        <div className="inc-facts">
          <div className="fact">
            <span className="k">Repository</span>
            <span className="v mono">
              {github?.selected_repo || '—'}
              {showRepoPicker && (
                <button type="button" className="linkish" onClick={onSelectRepo}>Select</button>
              )}
            </span>
          </div>
          <div className="fact"><span className="k">Branch</span><span className="v mono">—</span></div>
          <div className="fact"><span className="k">On-call</span><span className="v">{user?.name || '—'}</span></div>
          <div className="fact"><span className="k">GitHub</span><span className="v">{github?.login || '—'}</span></div>
        </div>
      </div>
    )
  }

  return (
    <div className="inc-header">
      <div className="top">
        <span className="sev-badge ok"><span className="dot" />READY</span>
        <span className="mono" style={{ fontSize: 12, color: 'var(--ink-faint)' }}>
          {github?.selected_repo || workspace.remote_url?.replace(/^https?:\/\//, '') || workspace.name}
        </span>
      </div>
      <h1>{workspace.readme_line || `${workspace.name} workspace`}</h1>
      <div className="inc-facts">
        <div className="fact">
          <span className="k">Repository</span>
          <span className="v mono">
            {github?.selected_repo || workspace.name}
            {showRepoPicker && (
              <button type="button" className="linkish" onClick={onSelectRepo}>Change</button>
            )}
          </span>
        </div>
        <div className="fact"><span className="k">Branch</span><span className="v mono">{workspace.branch || '—'}</span></div>
        <div className="fact"><span className="k">On-call</span><span className="v">{user?.name || '—'}</span></div>
        <div className="fact"><span className="k">GitHub</span><span className="v">{github?.login || '—'}</span></div>
      </div>
    </div>
  )
}
