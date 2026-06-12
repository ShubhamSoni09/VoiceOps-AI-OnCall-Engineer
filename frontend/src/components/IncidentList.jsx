import { Cloud, Database, SlackLogo, Code, PlugsConnected } from '@phosphor-icons/react'

const INTEG_ICON = {
  workspace: Code,
  render: Cloud,
  clickhouse: Database,
  slack: SlackLogo,
}

export default function IncidentList({ incidents, integrations, workspace }) {
  const sorted = (incidents || []).slice().sort((a, b) => {
    const ar = String(a.status || '').toLowerCase() === 'resolved'
    const br = String(b.status || '').toLowerCase() === 'resolved'
    if (ar !== br) return ar ? 1 : -1
    return (a.age_minutes || 99) - (b.age_minutes || 99)
  })
  const open = sorted.filter((i) => String(i.status || '').toLowerCase() !== 'resolved')

  return (
    <aside className="rail rail--left">
      <div className="rail-head">
        <h2>Incidents</h2>
        <span className="count">{open.length} open</span>
      </div>

      <div className="scroll" style={{ flex: 1 }}>
        {!workspace?.connected || sorted.length === 0 ? (
          <div className="rail-empty">
            <PlugsConnected size={28} color="var(--ink-ghost)" />
            {workspace?.connected
              ? 'No active incidents. Use voice commands to investigate your repository.'
              : 'Set VOICEOPS_WORKSPACE in backend/.env to connect a folder.'}
          </div>
        ) : (
          sorted.map((inc, idx) => {
            const isResolved = String(inc.status || '').toLowerCase() === 'resolved'
            const firstOpen = sorted.findIndex((i) => String(i.status || '').toLowerCase() !== 'resolved')
            const live = !isResolved && idx === firstOpen
            const sev = isResolved ? 'ok' : (String(inc.severity || '').includes('1') ? 'crit' : 'act')
            const tag = isResolved ? 'resolved' : (inc.severity || inc.status)
            return (
              <button key={inc.id || idx} className={`inc${live ? ' live' : ''}`} type="button">
                <span className={`sev ${sev}`} />
                <span className="inc-main">
                  <span className="inc-title">{inc.title}</span>
                  <span className="inc-meta">
                    <span className="mono svc">{inc.service}</span>
                    <span className="inc-sev-tag">{tag}</span>
                  </span>
                </span>
                <span className="age">{inc.age_minutes ? `${inc.age_minutes}m` : '—'}</span>
              </button>
            )
          })
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
