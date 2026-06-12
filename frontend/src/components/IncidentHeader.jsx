export default function IncidentHeader() {
  return (
    <div className="inc-header">
      <div className="top">
        <span className="sev-badge act"><span className="dot" />SEV1 · ACTIVE</span>
        <span className="mono" style={{ fontSize: 12, color: 'var(--ink-faint)' }}>
          INC-2026-0612-04
        </span>
      </div>
      <h1>checkout-api returning 500s on /v2/charge</h1>
      <div className="inc-facts">
        <div className="fact"><span className="k">Service</span><span className="v mono">checkout-api</span></div>
        <div className="fact"><span className="k">Started</span><span className="v">6 min ago · 02:47</span></div>
        <div className="fact"><span className="k">Error rate</span><span className="v mono warnv">4.7%</span></div>
        <div className="fact"><span className="k">Reporter</span><span className="v">PagerDuty P1</span></div>
        <div className="fact"><span className="k">Agent</span><span className="v" style={{ color: 'var(--accent)' }}>working · step 5/7</span></div>
      </div>
    </div>
  )
}
