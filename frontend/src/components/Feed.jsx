import { useEffect, useRef } from 'react'
import { User, Circuitry, Waveform, ChatsCircle } from '@phosphor-icons/react'
import { APP_NAME } from '../branding.js'
import { citationKey, citationMeta, citationTitle, ragTraceItems, uniqueCitations } from '../ragCitations.js'
import { feedEmptyStateCopy } from '../sessionRailModel.js'

const VISIBLE_MESSAGE_LIMIT = 24

function compactText(value) {
  return String(value || '').replace(/\s+/g, ' ').trim()
}

function escapeRegExp(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function messageBody(m) {
  return compactText(m?.html || m?.text)
}

function displayMessageName(m, agentName = APP_NAME) {
  if (m?.role !== 'agent') return m?.name
  const rawName = compactText(m.name)
  if (!rawName || rawName.toLowerCase() === APP_NAME.toLowerCase()) return agentName
  return m.name
}

function displayMessageBody(m, agentName = APP_NAME) {
  const raw = m?.html || m?.text || ''
  const renamed = m?.role !== 'agent' || !agentName || agentName === APP_NAME
    ? raw
    : String(raw).replace(new RegExp(`\\b${escapeRegExp(APP_NAME)}\\b(?!-)`, 'g'), agentName)
  return m?.role === 'agent' ? compactFeedDisplayText(renamed) : renamed
}

export function compactFeedDisplayText(value) {
  return String(value || '')
    .replace(
      /\b(I investigated|I checked|Checked|Investigated)\s+([A-Za-z0-9._/-]+)\s+in the\s+\2\s+workspace\b/gi,
      (_match, verb, name) => `${verb} the ${name} workspace`,
    )
    .replace(
      /\bI investigated the ([A-Za-z0-9._/-]+) workspace\. Key files:\s*(.+?)\.\s*All\s+\d+\s+tests?\s+pass(?:ed)?\./gi,
      'Checked $1 · files: $2 · tests pass.',
    )
    .replace(
      /\bI investigated the ([A-Za-z0-9._/-]+) workspace\. Key files:\s*(.+?)\.(?=\s*$)/gi,
      'Checked $1 · files: $2.',
    )
    .replace(
      /\bI investigated the ([A-Za-z0-9._/-]+) workspace\.\s*All\s+\d+\s+tests?\s+pass(?:ed)?\./gi,
      'Checked $1 · tests pass.',
    )
    .replace(/\basked\s+(.+?)\s+to status\b/gi, 'asked $1 for status')
}

function emptyAgentLabel(agentName = APP_NAME) {
  const name = compactText(agentName)
  return name && name.toLowerCase() !== APP_NAME.toLowerCase() ? name : 'your AI teammate'
}

function isCompactableMessage(m) {
  if (!m || m.draft) return false
  if (m.fromVoice || m.speakerLabel || m.identitySource) return false
  if (m.codeReferences?.length || m.citations?.length) return false
  if (ragTraceItems(m.ragRetrieval).length > 0) return false
  return Boolean(messageBody(m))
}

function compactKey(m) {
  return [
    m.role || '',
    m.name || '',
    m.role2 || '',
    m.html ? 'html' : 'text',
    messageBody(m),
  ].join('\u001f')
}

function queueActivityInfo(m) {
  if (!isCompactableMessage(m) || m.role !== 'agent' || m.duplicateCount) return null
  const text = messageBody(m)
  if (!/\b(assigned|cancelled|retried|cleared)\b/i.test(text)) return null
  const matched = text.match(/^(.+?)\s+(assigned|cancelled|retried)\s+.+?(?:\s+to| assignment:)\s+(.+)$/i)
    || text.match(/^(.+?)\s+cleared\s+\d+\s+completed assignments from the queue\.?$/i)
  if (!matched) return null
  return {
    actor: compactText(matched[1]),
    action: compactText(matched[2] || 'cleared'),
  }
}

function queueActivitySummary(messages) {
  const first = queueActivityInfo(messages[0])
  const sameActor = messages.every((message) => queueActivityInfo(message)?.actor === first?.actor)
  const actor = sameActor && first?.actor ? `${first.actor} ` : ''
  return `${actor}had ${messages.length} assignment queue updates.`
}

function compactQueueActivity(messages) {
  const compacted = []
  let index = 0
  while (index < messages.length) {
    const current = messages[index]
    if (!queueActivityInfo(current)) {
      compacted.push(current)
      index += 1
      continue
    }

    const run = []
    while (index < messages.length && queueActivityInfo(messages[index])) {
      run.push(messages[index])
      index += 1
    }

    if (run.length < 3) {
      compacted.push(...run)
      continue
    }

    compacted.push({
      ...run[0],
      text: queueActivitySummary(run),
      html: '',
      activityCount: run.length,
      activityLabel: 'Queue updates',
      activityMessages: run,
    })
  }
  return compacted
}

export function compactFeedMessages(messages = []) {
  const repeatGroups = new Map()
  messages.forEach((message) => {
    if (!isCompactableMessage(message)) return
    const key = compactKey(message)
    repeatGroups.set(key, [...(repeatGroups.get(key) || []), message])
  })
  const globalRepeatKeys = new Set(
    [...repeatGroups.entries()]
      .filter(([, entries]) => entries.length >= 3)
      .map(([key]) => key),
  )
  const globalSeen = new Set()
  const globallyCompacted = []

  messages.forEach((message) => {
    if (!isCompactableMessage(message)) {
      globallyCompacted.push(message)
      return
    }
    const key = compactKey(message)
    if (!globalRepeatKeys.has(key)) {
      globallyCompacted.push(message)
      return
    }
    if (globalSeen.has(key)) return
    const duplicateMessages = repeatGroups.get(key) || [message]
    globallyCompacted.push({
      ...message,
      duplicateMessages,
      duplicateCount: duplicateMessages.length,
    })
    globalSeen.add(key)
  })

  const compacted = []

  globallyCompacted.forEach((message) => {
    const previous = compacted[compacted.length - 1]
    if (
      previous
      && isCompactableMessage(previous)
      && isCompactableMessage(message)
      && compactKey(previous) === compactKey(message)
    ) {
      const duplicateMessages = previous.duplicateMessages || [previous]
      const incomingMessages = message.duplicateMessages || [message]
      const mergedMessages = [...duplicateMessages, ...incomingMessages]
      compacted[compacted.length - 1] = {
        ...previous,
        duplicateMessages: mergedMessages,
        duplicateCount: mergedMessages.length,
      }
      return
    }
    compacted.push(message)
  })

  return compactQueueActivity(compacted)
}

function RepeatDetails({ m, agentName }) {
  if (!m.duplicateCount || m.duplicateCount < 2) return null
  const entries = m.duplicateMessages || [m]
  const first = entries[0]
  const last = entries[entries.length - 1]
  const range = first?.when && last?.when && first.when !== last.when
    ? `${first.when} - ${last.when}`
    : first?.when || last?.when || 'timeline'
  const actor = displayMessageName(first, agentName) || 'Message'

  return (
    <details className="feed-repeat-details" title={`${actor} repeated ${m.duplicateCount} times, ${range}`}>
      <summary aria-label={`${actor} repeated ${m.duplicateCount} times, ${range}`}>
        <span>{m.duplicateCount} repeats · {actor}</span>
      </summary>
      <div>
        {entries.map((entry, index) => (
          <div className="feed-repeat-row" key={entry.id || `${messageBody(entry)}-${index}`} title={messageBody(entry)}>
            <b>{displayMessageName(entry, agentName)}</b>
            <span>{entry.when}</span>
            <em>{displayMessageBody(entry, agentName) || entry.id || `entry ${index + 1}`}</em>
          </div>
        ))}
      </div>
    </details>
  )
}

function ActivityDetails({ m, agentName }) {
  if (!m.activityCount || m.activityCount < 3) return null
  const entries = m.activityMessages || [m]
  const first = entries[0]
  const last = entries[entries.length - 1]
  const range = first?.when && last?.when && first.when !== last.when
    ? `${first.when} - ${last.when}`
    : first?.when || last?.when || 'timeline'

  return (
    <details className="feed-repeat-details feed-activity-details" title={`${m.activityLabel || 'Activity'}, ${m.activityCount} updates, ${range}`}>
      <summary aria-label={`${m.activityLabel || 'Activity'}, ${m.activityCount} updates, ${range}`}>
        <span>{m.activityCount} updates · {m.activityLabel || 'Activity'}</span>
      </summary>
      <div>
        {entries.map((entry, index) => (
          <div className="feed-repeat-row" key={entry.id || `${messageBody(entry)}-${index}`} title={messageBody(entry)}>
            <b>{displayMessageName(entry, agentName)}</b>
            <span>{entry.when}</span>
            <em>{displayMessageBody(entry, agentName)}</em>
          </div>
        ))}
      </div>
    </details>
  )
}

function voiceQuoteDisplay(m) {
  const state = m.draft || m.provisional
    ? 'provisional'
    : m.corrected
      ? 'speaker confirmed'
      : 'voice'
  const speaker = m.speakerLabel || 'voice'
  const confidence = typeof m.confidence === 'number' ? `${Math.round(m.confidence * 100)}%` : null
  const identityConfidence = typeof m.identityConfidence === 'number'
    ? `${Math.round(m.identityConfidence * 100)}% identity`
    : null
  const mapped = m.identitySource && m.identitySource !== 'mock'
    ? 'mapped'
    : m.speakerLabel
      ? 'unassigned'
      : null
  const visible = [state, speaker, confidence, mapped].filter(Boolean).join(' · ')
  const audit = [
    state,
    speaker,
    confidence,
    m.identitySource && m.identitySource !== 'mock' ? `source ${m.identitySource}` : null,
    identityConfidence,
  ].filter(Boolean).join(' · ')

  return {
    visible,
    audit,
  }
}

function messageToneClass(m) {
  if (m.role === 'agent') return 'agent ai-teammate'
  if (m.role === 'system') return 'system audit'
  if (m.draft || m.provisional) return 'user voice provisional-speaker'
  if (m.fromVoice || m.speakerLabel) {
    if (m.identitySource && m.identitySource !== 'mock') return 'user voice mapped-speaker'
    if (/identified speaker/i.test(String(m.role2 || ''))) return 'user voice mapped-speaker'
    return 'user voice unknown-speaker'
  }
  return m.role || 'user'
}

export function speakerIdentityLine(m) {
  if (!(m?.role === 'user' && (m.fromVoice || m.speakerLabel))) return null
  const confidence = typeof m.confidence === 'number' ? `${Math.round(m.confidence * 100)}%` : ''
  const rawLabel = m.speakerLabel || ''
  const detail = [rawLabel, confidence].filter(Boolean).join(' · ')
  if (m.draft || m.provisional) {
    return {
      tone: 'provisional',
      label: 'Live caption',
      detail: detail || 'speaker calibrating',
    }
  }
  if (m.identitySource && m.identitySource !== 'mock') {
    return {
      tone: 'mapped',
      label: 'Mapped voice',
      detail: detail || m.identitySource,
    }
  }
  if (/identified speaker/i.test(String(m.role2 || ''))) {
    return {
      tone: 'mapped',
      label: 'Mapped voice',
      detail: detail || 'identified speaker',
    }
  }
  return {
    tone: 'unknown',
    label: 'Needs speaker mapping',
    detail: detail || 'unknown speaker',
  }
}

function Message({ m, agentName }) {
  const isUser = m.role === 'user'
  const isSystem = m.role === 'system'
  const voiceQuote = voiceQuoteDisplay(m)
  const speakerLine = speakerIdentityLine(m)
  return (
    <div className={`msg ${messageToneClass(m)}${m.draft ? ' draft' : ''}`}>
      <div className="gut">
        <span className="ava">
          {isUser ? <User size={15} /> : isSystem ? <ChatsCircle size={15} /> : <Circuitry size={15} />}
        </span>
      </div>
      <div>
        <div className="msg-head">
          <b className="name">{displayMessageName(m, agentName)}</b>
          {m.role2 && <span className="role">{m.role2}</span>}
          {m.when && <span className="when">{m.when}</span>}
        </div>
        {speakerLine && (
          <div className={`speaker-identity-line ${speakerLine.tone}`} aria-label={`Speaker identity: ${speakerLine.label}. ${speakerLine.detail}`}>
            <Waveform size={12} />
            <span>{speakerLine.label}</span>
            <small>{speakerLine.detail}</small>
          </div>
        )}
        {isUser || isSystem ? (
          <>
            <div className="text">{displayMessageBody(m, agentName)}</div>
            {isUser && (m.fromVoice || m.speakerLabel) && (
              <div
                className={`voicequote${m.draft || m.provisional ? ' pending' : ''}${m.corrected ? ' corrected' : ''}`}
                title={voiceQuote.audit}
                aria-label={`Voice attribution: ${voiceQuote.audit}`}
              >
                <Waveform size={13} />
                <span>{voiceQuote.visible}</span>
              </div>
            )}
            <RepeatDetails m={m} agentName={agentName} />
            <ActivityDetails m={m} agentName={agentName} />
          </>
        ) : (
          <>
            {m.html
              ? <div className="text" dangerouslySetInnerHTML={{ __html: displayMessageBody(m, agentName) }} />
              : <div className="text">{displayMessageBody(m, agentName)}</div>}
            <RepeatDetails m={m} agentName={agentName} />
            <ActivityDetails m={m} agentName={agentName} />
            {m.codeReferences?.length > 0 && (
              <div className="code-ref-chips">
                {m.codeReferences.slice(0, 4).map((ref) => (
                  <span className="code-ref-chip" key={`${ref.path}:${ref.line}`}>
                    {ref.path}:{ref.line}
                  </span>
                ))}
              </div>
            )}
            {uniqueCitations(m.citations).length > 0 && (
              <div className="rag-citation-chips" aria-label="RAG sources">
                {uniqueCitations(m.citations).slice(0, 3).map((citation, index) => (
                  <span className="rag-citation-chip" key={citationKey(citation, index)}>
                    <b>{citationTitle(citation)}</b>
                    <small>{citationMeta(citation)}</small>
                    {citation.excerpt && <em>{citation.excerpt}</em>}
                  </span>
                ))}
              </div>
            )}
            {ragTraceItems(m.ragRetrieval).length > 0 && (
              <div className="rag-trace" aria-label="RAG retrieval trace">
                {ragTraceItems(m.ragRetrieval).map((item) => <span key={item}>{item}</span>)}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

export default function Feed({ messages, thinking, workspace, roomStatus, agentName = APP_NAME }) {
  const scrollRef = useRef(null)
  const agentLabel = emptyAgentLabel(agentName)

  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages, thinking])

  const emptyCopy = feedEmptyStateCopy({ workspace, roomStatus, agentLabel })
  const olderMessages = messages.length > VISIBLE_MESSAGE_LIMIT
    ? messages.slice(0, -VISIBLE_MESSAGE_LIMIT)
    : []
  const visibleMessagesRaw = olderMessages.length
    ? messages.slice(-VISIBLE_MESSAGE_LIMIT)
    : messages
  const olderDisplayMessages = compactFeedMessages(olderMessages)
  const visibleMessages = compactFeedMessages(visibleMessagesRaw)

  return (
    <div className="scroll" ref={scrollRef} style={{ flex: 1 }}>
      <div className="feed">
        {messages.length === 0 && !thinking && (
          <div className="feed-empty">
            <ChatsCircle size={36} color="var(--ink-ghost)" />
            <b>{emptyCopy.title}</b>
            <p>{emptyCopy.text}</p>
            <div className="feed-empty-steps" aria-label="Suggested first actions">
              {emptyCopy.steps.map((step) => <span key={step}>{step}</span>)}
            </div>
          </div>
        )}

        {olderMessages.length > 0 && (
          <details className="feed-history">
            <summary aria-label={`Earlier activity, ${olderMessages.length} message${olderMessages.length === 1 ? '' : 's'} folded`}>
              <span>Earlier activity</span>
              <small> · {olderMessages.length} message{olderMessages.length === 1 ? '' : 's'} folded</small>
            </summary>
            <div className="feed-history-body">
              {olderDisplayMessages.map((m) => (
                <Message key={m.id} m={m} agentName={agentName} />
              ))}
            </div>
          </details>
        )}

        {olderMessages.length > 0 && (
          <div className="feed-recent-marker" aria-label="Recent activity window">
            <span>Recent activity</span>
            <small>last {visibleMessagesRaw.length} shown</small>
          </div>
        )}

        {visibleMessages.map((m) => (
          <Message key={m.id} m={m} agentName={agentName} />
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
