import { Microphone, Stop, Waveform, Check } from '@phosphor-icons/react'

const WAVE_BARS = Array.from({ length: 36 }, (_, i) => ({
  delay: (i * 0.045).toFixed(2),
  dur: (0.7 + (i % 5) * 0.12).toFixed(2),
}))

function StateLine({ phase }) {
  if (phase === 'listening') {
    return (
      <>
        <span className="live">● Listening</span> · speak now, tap to stop
      </>
    )
  }
  if (phase === 'captured') {
    return (
      <>
        <Check size={14} color="var(--ok)" /> Captured · routing to agent
      </>
    )
  }
  return (
    <>
      <Waveform size={14} /> Tap to talk to production
    </>
  )
}

export default function PushToTalk({ listening, phase, transcript, onToggle }) {
  return (
    <footer className="ptt">
      <button className="mic" onClick={onToggle} aria-label="Push to talk">
        {listening ? <Stop size={23} /> : <Microphone size={23} />}
      </button>

      <div className="ptt-mid">
        <div className="ptt-state"><StateLine phase={phase} /></div>
        <div className="ptt-trans">
          {transcript ?? (
            <>
              Ask anything:{' '}
              <span style={{ color: 'var(--ink-faint)' }}>
                "deploy the fix", "what changed in the last hour", "roll back edge-gateway"
              </span>
            </>
          )}
        </div>
        <div className="wave">
          {WAVE_BARS.map((b, i) => (
            <i key={i} style={{ animationDelay: `${b.delay}s`, animationDuration: `${b.dur}s` }} />
          ))}
        </div>
      </div>

      <div className="ptt-hint">
        hold <kbd>space</kbd> to talk · <kbd>⌘K</kbd> commands
      </div>
    </footer>
  )
}
