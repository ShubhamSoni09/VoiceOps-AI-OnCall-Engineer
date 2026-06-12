export default function IncidentHeader({ workspace, user }) {
  if (!workspace?.connected) {
    return (
      <div className="inc-header">
        <div className="top">
          <span className="sev-badge"><span className="dot" />STANDBY</span>
          <span className="mono" style={{ fontSize: 12, color: 'var(--ink-faint)' }}>—</span>
        </div>
        <h1>No repository connected</h1>
        <div className="inc-facts">
          <div className="fact"><span className="k">Repository</span><span className="v mono">—</span></div>
          <div className="fact"><span className="k">Branch</span><span className="v mono">—</span></div>
          <div className="fact"><span className="k">On-call</span><span className="v">{user?.name || '—'}</span></div>
          <div className="fact"><span className="k">Agent</span><span className="v">idle</span></div>
        </div>
      </div>
    )
  }

  return (
    <div className="inc-header">
      <div className="top">
        <span className="sev-badge ok"><span className="dot" />READY</span>
        <span className="mono" style={{ fontSize: 12, color: 'var(--ink-faint)' }}>
          {workspace.remote_url?.replace(/^https?:\/\//, '') || workspace.name}
        </span>
      </div>
      <h1>{workspace.readme_line || `${workspace.name} workspace`}</h1>
      <div className="inc-facts">
        <div className="fact"><span className="k">Repository</span><span className="v mono">{workspace.name}</span></div>
        <div className="fact"><span className="k">Branch</span><span className="v mono">{workspace.branch || '—'}</span></div>
        <div className="fact"><span className="k">On-call</span><span className="v">{user?.name || '—'}</span></div>
        <div className="fact"><span className="k">Agent</span><span className="v">idle</span></div>
      </div>
    </div>
  )
}
