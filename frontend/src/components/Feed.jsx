import { useEffect, useRef } from 'react'
import {
  User,
  Circuitry,
  Waveform,
  ListMagnifyingGlass,
  GitDiff,
  Target,
  GitCommit,
  Wrench,
  RocketLaunch,
  Pause,
} from '@phosphor-icons/react'

/* ---- a single dynamic message (user voice or agent reply) ---- */
function Message({ m }) {
  const isUser = m.role === 'user'
  return (
    <div className={`msg ${m.role}`}>
      <div className="gut">
        <span className="ava">{isUser ? <User size={15} /> : <Circuitry size={15} />}</span>
      </div>
      <div>
        <span className="name">
          {m.name}
          <span className="role">{m.role2}</span>
          <span className="when">{m.when}</span>
        </span>
        {isUser ? (
          <>
            <div className="text">{m.text}</div>
            <div className="voicequote"><Waveform size={13} /> transcribed from voice</div>
          </>
        ) : (
          <div className="text" dangerouslySetInnerHTML={{ __html: m.html }} />
        )}
      </div>
    </div>
  )
}

/* ---- the live "agent is waiting / working" row at the tail ---- */
function ThinkingRow({ status }) {
  if (status === 'idle') return null

  if (status === 'hold') {
    return (
      <div className="msg agent">
        <div className="gut"><span className="ava"><Circuitry size={15} /></span></div>
        <div style={{ paddingTop: 5 }}>
          <span className="thinking">
            <span className="orb" style={{ background: 'var(--ink-ghost)' }}><Pause size={11} /></span>
            Holding. Want me to try a config-only hotfix instead of a deploy?
          </span>
        </div>
      </div>
    )
  }

  const label =
    status === 'deploying' ? (
      <>Deploying <b>PR&nbsp;#482</b> to Render preview</>
    ) : (
      <>Ready to deploy a preview to Render. Waiting for your approval</>
    )

  return (
    <div className="msg agent">
      <div className="gut"><span className="ava"><Circuitry size={15} /></span></div>
      <div style={{ paddingTop: 5 }}>
        <span className="thinking">
          <span className="orb"><RocketLaunch size={11} /></span>
          {label}
          <span className="dots"><span /><span /><span /></span>
        </span>
      </div>
    </div>
  )
}

export default function Feed({ messages, agentStatus }) {
  const scrollRef = useRef(null)

  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages, agentStatus])

  return (
    <div className="scroll" ref={scrollRef} style={{ flex: 1 }}>
      <div className="feed">
        <div className="feed-day">Today · 02:47</div>

        {/* user voice query */}
        <div className="msg user">
          <div className="gut"><span className="ava"><User size={15} /></span></div>
          <div>
            <span className="name">Priya Nair<span className="role">on-call</span><span className="when">02:47</span></span>
            <div className="text">Why is the checkout API throwing 500s? Pull the logs and tell me what changed.</div>
            <div className="voicequote"><Waveform size={13} /> transcribed from voice · 3.2s</div>
          </div>
        </div>

        {/* agent plan */}
        <div className="msg agent">
          <div className="gut"><span className="ava"><Circuitry size={15} /></span></div>
          <div>
            <span className="name">VoiceOps<span className="role">agent</span><span className="when">02:47</span></span>
            <div className="text">
              On it. Planning a 4-step investigation: pull recent <b>5xx logs</b>, diff the last deploy,
              check DB pool and latency, then propose a fix.
            </div>
          </div>
        </div>

        {/* agent log block */}
        <div className="msg agent">
          <div className="gut"><span className="ava"><Circuitry size={15} /></span></div>
          <div>
            <span className="name">VoiceOps<span className="role">via Composio · ClickHouse</span><span className="when">02:48</span></span>
            <div className="text">Last 200 errors on <b>checkout-api</b> cluster on one cause:</div>
            <div className="block">
              <div className="block-h">
                <ListMagnifyingGlass size={13} /> logs.query · checkout-api · last 5m
                <span className="src mono">187 matched</span>
              </div>
              <pre className="mono">
<span className="lk">02:47:12</span> <span className="lc">ERROR</span> charge_handler: <span className="lc">TimeoutError</span> acquiring db connection (waited 5001ms){'\n'}
<span className="lk">02:47:12</span> <span className="lc">ERROR</span> pool: HikariPool-1 - Connection is not available, request timed out{'\n'}
<span className="lk">02:47:11</span> <span className="lw">WARN </span> pool: active=20 idle=0 waiting=64 max=20{'\n'}
<span className="lk">02:47:09</span> <span className="lk">INFO </span> deploy: build a3f9c2 promoted to prod 11m ago</pre>
            </div>
          </div>
        </div>

        {/* agent diagnosis */}
        <div className="msg agent">
          <div className="gut"><span className="ava"><Circuitry size={15} /></span></div>
          <div>
            <span className="name">VoiceOps<span className="role">diagnosis</span><span className="when">02:49</span></span>
            <div className="text">
              Root cause found. Deploy <b>a3f9c2</b> raised request concurrency but left the DB pool at <b>20</b>.
              The pool is exhausted, 64 requests are queued, every one times out at 5s.
            </div>
            <div className="findings">
              <div className="finding"><Target size={15} /><span><b>Cause:</b> connection pool too small for new concurrency limit</span></div>
              <div className="finding"><GitCommit size={15} /><span><b>Introduced by:</b> <span className="mono">a3f9c2</span> "raise worker concurrency to 64" by m.bell</span></div>
              <div className="finding"><Wrench size={15} /><span><b>Fix:</b> raise <span className="mono">db.pool.max</span> 20 → 80, add 2s acquire timeout</span></div>
            </div>
          </div>
        </div>

        {/* agent patch / PR */}
        <div className="msg agent">
          <div className="gut"><span className="ava"><Circuitry size={15} /></span></div>
          <div>
            <span className="name">VoiceOps<span className="role">patch · tests passed</span><span className="when">02:51</span></span>
            <div className="text">Patched in a sandbox, lint + unit + a load smoke test all green. Opened <b>PR #482</b>:</div>
            <div className="block">
              <div className="block-h">
                <GitDiff size={13} /> checkout-api/config/database.yml
                <span className="src mono">+3 -1</span>
              </div>
              <pre className="mono">
<span className="del">- pool:   {'{'} max: 20 {'}'}</span>{'\n'}
<span className="add">+ pool:   {'{'} max: 80, acquire_timeout_ms: 2000 {'}'}</span>{'\n'}
<span className="add">+ # autoscaled with worker concurrency (a3f9c2)</span></pre>
            </div>
          </div>
        </div>

        {/* dynamic messages */}
        {messages.map((m) => (
          <Message key={m.id} m={m} />
        ))}

        {/* live tail */}
        <ThinkingRow status={agentStatus} />
      </div>
    </div>
  )
}
