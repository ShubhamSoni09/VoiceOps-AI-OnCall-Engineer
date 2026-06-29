import {
  Check,
  ClipboardText,
  Database,
  GitBranch,
  ShieldCheck,
  Sparkle,
  TestTube,
  Waveform,
} from '@phosphor-icons/react'

const STEP_ICON = {
  capture: Waveform,
  memory: Database,
  proposal: Sparkle,
  approval: ShieldCheck,
  test: TestTube,
  branch: GitBranch,
  handoff: ClipboardText,
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
