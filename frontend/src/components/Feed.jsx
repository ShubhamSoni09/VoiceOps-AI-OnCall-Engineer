import { useEffect, useRef, useState } from 'react'
import { User, Circuitry, Waveform, ChatsCircle, CaretDown, CaretRight, Terminal } from '@phosphor-icons/react'
import OpenUIBriefing from './OpenUIBriefing.jsx'

function DiffLine({ line }) {
  if (line.startsWith('+++') || line.startsWith('---')) {
    return <div className="diff-line diff-meta">{line}</div>
  }
  if (line.startsWith('@@')) {
    return <div className="diff-line diff-hunk">{line}</div>
  }
  if (line.startsWith('+')) {
    return <div className="diff-line diff-add">{line}</div>
  }
  if (line.startsWith('-')) {
    return <div className="diff-line diff-del">{line}</div>
  }
  return <div className="diff-line diff-ctx">{line}</div>
}

function ExpandableDetails({ diff, commandOutput }) {
  const [open, setOpen] = useState(false)
  const [tab, setTab] = useState(diff ? 'diff' : 'output')

  if (!diff && !commandOutput) return null

  return (
    <div className="msg-details">
      <button className="details-toggle" onClick={() => setOpen((v) => !v)}>
        {open ? <CaretDown size={12} /> : <CaretRight size={12} />}
        {open ? 'Hide changes' : 'Show changes'}
        {diff && <span className="details-badge">diff</span>}
        {commandOutput && <span className="details-badge">output</span>}
      </button>

      {open && (
        <div className="details-panel">
          {diff && commandOutput && (
            <div className="details-tabs">
              <button
                className={`details-tab${tab === 'diff' ? ' active' : ''}`}
                onClick={() => setTab('diff')}
              >
                Changes
              </button>
              <button
                className={`details-tab${tab === 'output' ? ' active' : ''}`}
                onClick={() => setTab('output')}
              >
                <Terminal size={11} /> Test output
              </button>
            </div>
          )}

          {(!commandOutput || tab === 'diff') && diff && (
            <div className="diff-view">
              {diff.split('\n').map((line, i) => (
                <DiffLine key={i} line={line} />
              ))}
            </div>
          )}

          {(!diff || tab === 'output') && commandOutput && (
            <pre className="output-view">{commandOutput}</pre>
          )}
        </div>
      )}
    </div>
  )
}

function LiveDetails({ step, output, diff }) {
  const outputRef = useRef(null)

  useEffect(() => {
    if (outputRef.current) outputRef.current.scrollTop = outputRef.current.scrollHeight
  }, [output])

  const hasContent = output || diff

  if (!hasContent) return null

  return (
    <div className="details-panel live-details">
      {diff && (
        <div className="diff-view">
          {diff.split('\n').map((line, i) => (
            <DiffLine key={i} line={line} />
          ))}
        </div>
      )}
      {output && (
        <pre className="output-view live-output" ref={outputRef}>{output}</pre>
      )}
    </div>
  )
}

function Message({ m }) {
  const isUser = m.role === 'user'

  return (
    <div className={`msg ${m.role}`}>
      <div className="gut">
        <span className="ava">{isUser ? <User size={15} /> : <Circuitry size={15} />}</span>
      </div>
      <div style={{ minWidth: 0, flex: 1 }}>
        <span className="name">
          {m.name}
          <span className="role">{m.role2}</span>
          <span className="when">{m.when}</span>
        </span>

        {isUser ? (
          <>
            <div className="text">{m.text}</div>
            {m.fromVoice && (
              <div className="voicequote">
                <Waveform size={13} /> transcribed from voice
              </div>
            )}
          </>
        ) : m.pending ? (
          <>
            <div className="text">
              <span className="thinking">
                <span className="orb"><Circuitry size={11} /></span>
                {m.liveStep || 'Working…'}
                <span className="dots"><span /><span /><span /></span>
              </span>
            </div>
            <LiveDetails step={m.liveStep} output={m.liveOutput} diff={m.diff} />
          </>
        ) : (
          <>
            <div className="text" dangerouslySetInnerHTML={{ __html: m.html }} />
            <ExpandableDetails diff={m.diff} commandOutput={m.commandOutput} />
          </>
        )}
      </div>
    </div>
  )
}

export default function Feed({ messages, workspace }) {
  const scrollRef = useRef(null)

  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages])

  const emptyText = workspace?.connected
    ? 'Hold space or tap the mic to start talking to VoiceOps.'
    : 'Connect a repository, then hold space or tap the mic to talk to VoiceOps.'

  return (
    <div className="scroll" ref={scrollRef} style={{ flex: 1 }}>
      <div className="feed">
        {/* Generative-UI showcase: the agent's incident briefing rendered from
            OpenUI Lang via @openuidev/react-lang, mapped onto the VoiceOps design
            system. Static sample for now; swap OpenUIBriefing's response for a
            streamed LLM output (system prompt = voiceopsSystemPrompt) to go live. */}
        <div className="msg agent">
          <div className="gut"><span className="ava"><Circuitry size={15} /></span></div>
          <div>
            <span className="name">
              VoiceOps
              <span className="role">live briefing</span>
              <span className="when">rendered by OpenUI</span>
            </span>
            <OpenUIBriefing />
          </div>
        </div>

        {messages.length === 0 && (
          <div className="feed-empty">
            <ChatsCircle size={36} color="var(--ink-ghost)" />
            <p>{emptyText}</p>
          </div>
        )}

        {messages.map((m) => (
          <Message key={m.id} m={m} />
        ))}
      </div>
    </div>
  )
}
