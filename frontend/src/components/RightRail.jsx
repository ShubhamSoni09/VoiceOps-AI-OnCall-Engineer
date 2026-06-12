import {
  CheckCircle,
  X,
  XCircle,
  Warning,
  RocketLaunch,
  ShieldCheck,
  GitPullRequest,
  ListMagnifyingGlass,
  BookOpenText,
  ArrowUpRight,
} from '@phosphor-icons/react'
import { METRICS, ARTIFACTS } from '../data.js'

const DONE_ICON = {
  rocket: RocketLaunch,
  shield: ShieldCheck,
  check: CheckCircle,
  x: XCircle,
}

const ARTIFACT_ICON = {
  pr: GitPullRequest,
  logs: ListMagnifyingGlass,
  book: BookOpenText,
}

export default function RightRail({ action, onApprove, onReject }) {
  const resolved = action.status === 'approved' || action.status === 'rejected'
  const DoneIcon = action.done ? DONE_ICON[action.done.icon] : null

  return (
    <aside className="rail rail--right">
      <div className="scroll">
        {/* pending action */}
        <div>
          <p className="section-lab">Awaiting your call</p>
          <div
            className={
              'action' +
              (action.status === 'approved' ? ' resolved' : '') +
              (action.status === 'rejected' ? ' resolved rejected' : '')
            }
          >
            <div className="ah">
              <span className="ribbon"><span className="lvdot" />AGENT PROPOSES</span>
              <h3>Deploy preview of PR #482</h3>
            </div>
            <p className="sub">
              Spin up a Render preview env from the fix branch and run health checks before promoting to prod.
            </p>
            <div className="preview mono">
              <div className="row"><span className="k">branch</span><span className="v">fix/pool-exhaustion-482</span></div>
              <div className="row"><span className="k">target</span><span className="v">render · checkout-api-preview</span></div>
              <div className="row"><span className="k">checks</span><span className="v">/healthz · p99 · error-rate</span></div>
            </div>
            <div className="risk">
              <Warning size={14} /> Preview only. Prod promote needs a second approval.
            </div>
            <div className="acts">
              <button className="btn approve" onClick={onApprove}><CheckCircle size={16} /> Approve</button>
              <button className="btn reject" onClick={onReject}><X size={16} /> Reject</button>
            </div>
            {action.done && (
              <div className="doneline">
                {DoneIcon && <DoneIcon size={15} color={action.done.green ? 'var(--ok)' : undefined} />}
                {action.done.text}
              </div>
            )}
          </div>
        </div>

        {/* live context metrics */}
        <div>
          <p className="section-lab">checkout-api · live</p>
          <div className="metrics">
            {METRICS.map((m) => (
              <div className="metric" key={m.k}>
                <div className="k">{m.k}</div>
                <div className={`v${m.tone ? ' ' + m.tone : ''}`}>
                  {m.v}<small>{m.unit}</small>
                </div>
                <div className={`delta${m.dtone ? ' ' + m.dtone : ''}`}>{m.delta}</div>
              </div>
            ))}
          </div>
        </div>

        {/* artifacts */}
        <div>
          <p className="section-lab">Artifacts</p>
          {ARTIFACTS.map((a) => {
            const Icon = ARTIFACT_ICON[a.icon]
            return (
              <button className="artifact" key={a.id}>
                <Icon className="lead" size={18} />
                <span className="af"><b>{a.title}</b><small>{a.sub}</small></span>
                <ArrowUpRight className="go" size={14} />
              </button>
            )
          })}
        </div>
      </div>
    </aside>
  )
}
