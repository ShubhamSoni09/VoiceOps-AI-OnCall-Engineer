import { Check, GitPullRequest, RocketLaunch, ShieldCheck } from '@phosphor-icons/react'

const STEP_ICON = {
  pr: GitPullRequest,
  deploy: RocketLaunch,
  verify: ShieldCheck,
}

export default function Stepper({ steps }) {
  return (
    <div className="stepper">
      {steps.map((s) => {
        const Icon = s.state === 'done' ? Check : STEP_ICON[s.key] || Check
        return (
          <div key={s.key} className={`step ${s.state}`.trimEnd()}>
            <span className="node">
              <Icon size={s.state === 'done' ? 14 : 13} weight={s.state === 'done' ? 'bold' : 'regular'} />
            </span>
            <span className="lab">{s.label}</span>
          </div>
        )
      })}
    </div>
  )
}
