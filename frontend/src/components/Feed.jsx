import { useEffect, useRef } from 'react'
import { User, Circuitry, Waveform, ChatsCircle } from '@phosphor-icons/react'
import OpenUIBriefing from './OpenUIBriefing.jsx'

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
            {m.fromVoice && <div className="voicequote"><Waveform size={13} /> transcribed from voice</div>}
          </>
        ) : (
          <div className="text" dangerouslySetInnerHTML={{ __html: m.html }} />
        )}
      </div>
    </div>
  )
}

export default function Feed({ messages, thinking, workspace }) {
  const scrollRef = useRef(null)

  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages, thinking])

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

        {messages.length === 0 && !thinking && (
          <div className="feed-empty">
            <ChatsCircle size={36} color="var(--ink-ghost)" />
            <p>{emptyText}</p>
          </div>
        )}

        {messages.map((m) => (
          <Message key={m.id} m={m} />
        ))}

        {thinking && (
          <div className="msg agent">
            <div className="gut"><span className="ava"><Circuitry size={15} /></span></div>
            <div style={{ paddingTop: 5 }}>
              <span className="thinking">
                <span className="orb"><Circuitry size={11} /></span>
                {thinking}
                <span className="dots"><span /><span /><span /></span>
              </span>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
