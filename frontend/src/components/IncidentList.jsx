import { GithubLogo, Cloud, Database, SlackLogo, Code, PlugsConnected } from '@phosphor-icons/react'

const INTEG_ICON = {
  github: GithubLogo,
  mcp: Code,
  render: Cloud,
  clickhouse: Database,
  slack: SlackLogo,
}

export default function IncidentList({ incidents, integrations, workspace }) {
  const open = (incidents || []).filter((i) => String(i.status || '').toLowerCase() !== 'resolved')

  return (
    <aside className="rail rail--left">
      <div className="rail-head">
        <h2>Incidents</h2>
        <span className="count">{open.length} open</span>
      </div>

      <div className="scroll" style={{ flex: 1 }}>
        {!workspace?.connected || open.length === 0 ? (
          <div className="rail-empty">
            <PlugsConnected size={28} color="var(--ink-ghost)" />
            {workspace?.connected
              ? 'No active incidents. Use voice commands to investigate your repository.'
              : 'Connect a repository via MCP to see incidents here.'}
          </div>
        ) : (
          open.map((inc, idx) => (
            <button key={inc.id || idx} className={`inc${idx === 0 ? ' live' : ''}`} type="button">
              <span className="sev act" />
              <span className="inc-main">
                <span className="inc-title">{inc.title}</span>
                <span className="inc-meta">
                  <span className="mono svc">{inc.service}</span>
                  <span className="inc-sev-tag">{inc.severity || inc.status}</span>
                </span>
              </span>
            </button>
          ))
        )}
      </div>

      <div className="rail-foot">
        {(integrations || []).map((it) => {
          const Icon = INTEG_ICON[it.id] || PlugsConnected
          return (
            <div className={`integ${it.connected ? '' : ' off'}`} key={it.id} title={it.detail || ''}>
              <Icon size={15} color="var(--ink-faint)" />
              {it.label}
              <span className="ind" />
            </div>
          )
        })}
      </div>
    </aside>
  )
}
