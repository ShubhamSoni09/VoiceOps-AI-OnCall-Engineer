import {
  CheckCircle,
  Package,
  GitPullRequest,
  ListMagnifyingGlass,
  BookOpenText,
  ArrowUpRight,
} from '@phosphor-icons/react'

const ARTIFACT_ICON = {
  pr: GitPullRequest,
  logs: ListMagnifyingGlass,
  book: BookOpenText,
  runbook: BookOpenText,
}

export default function RightRail({ workspace, actionDone, artifacts, metrics }) {
  const ws = workspace || {}

  return (
    <aside className="rail rail--right">
      <div className="scroll">
        <div>
          <p className="section-lab">Awaiting your call</p>
          <div className={`action${actionDone ? ' resolved' : ''}`}>
            <div className="ah">
              <span className="ribbon"><span className="lvdot" />STANDING BY</span>
              <h3>Waiting for a voice command</h3>
            </div>
            <p className="sub">
              {ws.connected
                ? 'Hold space or tap the mic to investigate, test, or patch your sandbox repo.'
                : 'Set VOICEOPS_WORKSPACE in backend/.env to connect the MCP workspace.'}
            </p>
            <div className="preview mono">
              <div className="row"><span className="k">repository</span><span className="v">{ws.remote_url || ws.name || 'not connected'}</span></div>
              <div className="row"><span className="k">workspace</span><span className="v">{ws.name || '—'}</span></div>
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
            artifacts.map((a, i) => {
              const Icon = ARTIFACT_ICON[a.type] || Package
              return (
                <button className="artifact" key={i} type="button">
                  <Icon className="lead" size={18} />
                  <span className="af"><b>{a.title}</b><small>{a.subtitle}</small></span>
                  <ArrowUpRight className="go" size={14} />
                </button>
              )
            })
          ) : (
            <div className="panel-empty">
              <Package size={28} color="var(--ink-ghost)" />
              No artifacts yet. They will appear after the agent works on your repo.
            </div>
          )}
        </div>
      </div>
    </aside>
  )
}
