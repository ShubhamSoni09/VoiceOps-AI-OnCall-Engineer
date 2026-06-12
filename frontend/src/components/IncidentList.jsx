import { GithubLogo, Cloud, Database, SlackLogo } from '@phosphor-icons/react'
import { INCIDENTS, INTEGRATIONS } from '../data.js'

const INTEG_ICON = {
  github: GithubLogo,
  render: Cloud,
  clickhouse: Database,
  slack: SlackLogo,
}

export default function IncidentList({ activeId, onSelect }) {
  return (
    <aside className="rail rail--left">
      <div className="rail-head">
        <h2>Incidents</h2>
        <span className="count">3 open</span>
      </div>

      <div className="scroll" style={{ flex: 1 }}>
        {INCIDENTS.map((inc) => (
          <button
            key={inc.id}
            className={`inc${inc.live ? ' live' : ''}`}
            aria-current={inc.id === activeId ? 'true' : undefined}
            onClick={() => onSelect(inc.id)}
          >
            <span className={`sev ${inc.sev}`} />
            <span className="inc-main">
              <span className="inc-title">{inc.title}</span>
              <span className="inc-meta">
                <span className="mono svc">{inc.svc}</span>
                <span className="inc-sev-tag">{inc.tag}</span>
              </span>
            </span>
            <span className="age">{inc.age}</span>
          </button>
        ))}
      </div>

      <div className="rail-foot">
        {INTEGRATIONS.map((it) => {
          const Icon = INTEG_ICON[it.id]
          return (
            <div className="integ" key={it.id}>
              <Icon size={15} color="var(--ink-faint)" />
              {it.label}
              <span className={`ind${it.on ? '' : ' off'}`} />
            </div>
          )
        })}
      </div>
    </aside>
  )
}
