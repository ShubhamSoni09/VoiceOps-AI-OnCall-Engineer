import { useCallback, useEffect, useRef, useState } from 'react'
import { useTheme } from './useTheme.js'
import TopBar from './components/TopBar.jsx'
import IncidentList from './components/IncidentList.jsx'
import IncidentHeader from './components/IncidentHeader.jsx'
import Stepper from './components/Stepper.jsx'
import Feed from './components/Feed.jsx'
import RightRail from './components/RightRail.jsx'
import PushToTalk from './components/PushToTalk.jsx'
import Login from './Login.jsx'
import { ACTION_INDEX, IDLE_STEPS } from './data.js'
import {
  fetchBootstrap,
  getToken,
  incidentContextFromBootstrap,
  processText,
} from './api.js'

function clockNow() {
  return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

export default function App() {
  const { theme, toggle } = useTheme()
  const [authed, setAuthed] = useState(!!getToken())
  const [bootstrap, setBootstrap] = useState(null)
  const [loadError, setLoadError] = useState('')
  const [steps, setSteps] = useState(IDLE_STEPS)
  const [messages, setMessages] = useState([])
  const [artifacts, setArtifacts] = useState([])
  const [actionDone, setActionDone] = useState('')
  const [listening, setListening] = useState(false)
  const [pttPhase, setPttPhase] = useState('idle')
  const [transcript, setTranscript] = useState(null)
  const [thinking, setThinking] = useState('')
  const [processing, setProcessing] = useState(false)

  const idRef = useRef(0)
  const sessionId = bootstrap?.user?.id ? `voiceops-${bootstrap.user.id}` : 'voiceops-session'
  const recognitionRef = useRef(null)

  const loadBootstrap = useCallback(async () => {
    try {
      const data = await fetchBootstrap()
      setBootstrap(data)
      setArtifacts(data.artifacts || [])
      setLoadError('')
    } catch (err) {
      setLoadError(err.message || 'Failed to load console')
    }
  }, [])

  useEffect(() => {
    if (authed) loadBootstrap()
  }, [authed, loadBootstrap])

  function pushMsg(msg) {
    idRef.current += 1
    setMessages((prev) => [...prev, { id: idRef.current, ...msg }])
  }

  function highlightStepper(action) {
    const idx = ACTION_INDEX[action] ?? 0
    setSteps((prev) =>
      prev.map((s, i) => {
        if (i < idx) return { ...s, state: 'done' }
        if (i === idx) return { ...s, state: 'active' }
        return { ...s, state: '' }
      }),
    )
  }

  async function sendUtterance(text, fromVoice = true) {
    if (!text?.trim() || processing) return
    setProcessing(true)
    setThinking('Working on your request')
    setPttPhase('captured')
    setTranscript(text)

    pushMsg({
      role: 'user',
      name: bootstrap?.user?.name || 'You',
      role2: bootstrap?.user?.role_label || 'on-call',
      when: clockNow(),
      text,
      fromVoice,
    })

    try {
      const data = await processText(text, sessionId, incidentContextFromBootstrap(bootstrap))
      let html = `<p>${escapeHtml(data.response_text || '')}</p>`
      const orch = data.orchestrator_result
      if (orch?.files_changed?.length) {
        html += orch.files_changed.map((f) => `<div class="finding">Updated <b>${escapeHtml(f)}</b> in sandbox</div>`).join('')
      }
      pushMsg({
        role: 'agent',
        name: 'VoiceOps',
        role2: `${data.intent?.action || 'agent'}`,
        when: clockNow(),
        html,
      })
      if (orch?.artifacts?.length) {
        setArtifacts((prev) => [...orch.artifacts, ...prev])
      }
      if (orch?.executed) setActionDone(data.response_text || 'Workspace action completed')
      highlightStepper(data.command?.action)
    } catch (err) {
      pushMsg({
        role: 'agent',
        name: 'VoiceOps',
        role2: 'error',
        when: clockNow(),
        html: `<p>Sorry, I hit an error: <b>${escapeHtml(err.message)}</b></p>`,
      })
    } finally {
      setThinking('')
      setProcessing(false)
      setTimeout(() => {
        setPttPhase('idle')
        setTranscript(null)
      }, 2000)
    }
  }

  function startListening() {
    if (processing) return
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition
    if (SR) {
      const rec = new SR()
      rec.continuous = false
      rec.interimResults = true
      rec.lang = 'en-US'
      rec.onresult = (e) => {
        const t = Array.from(e.results).map((r) => r[0].transcript).join('').trim()
        setTranscript(t)
        if (e.results[0].isFinal && t) sendUtterance(t, true)
      }
      rec.onerror = () => {
        setListening(false)
        setPttPhase('idle')
      }
      rec.onend = () => setListening(false)
      recognitionRef.current = rec
      rec.start()
      setListening(true)
      setPttPhase('listening')
      return
    }
    const typed = window.prompt('Say your command (speech not supported in this browser):')
    if (typed) sendUtterance(typed, false)
  }

  function stopListening() {
    recognitionRef.current?.stop()
    setListening(false)
    if (!processing) setPttPhase('idle')
  }

  function toggleMic() {
    if (listening) stopListening()
    else startListening()
  }

  useEffect(() => {
    const down = (e) => {
      if (e.code === 'Space' && e.target === document.body && !processing) {
        e.preventDefault()
        startListening()
      }
    }
    const up = (e) => {
      if (e.code === 'Space') {
        e.preventDefault()
        stopListening()
      }
    }
    window.addEventListener('keydown', down)
    window.addEventListener('keyup', up)
    return () => {
      window.removeEventListener('keydown', down)
      window.removeEventListener('keyup', up)
    }
  }, [processing, listening])

  if (!authed) {
    return <Login onSuccess={() => setAuthed(true)} />
  }

  if (loadError && !bootstrap) {
    return (
      <div className="login-wrap">
        <div className="login-card">
          <h1>Console unavailable</h1>
          <p className="sub">{loadError}</p>
          <p className="sub">Restart the backend on port 8001 and refresh.</p>
        </div>
      </div>
    )
  }

  return (
    <div className={'app' + (listening ? ' listening' : '')}>
      <TopBar
        theme={theme}
        onToggleTheme={toggle}
        user={bootstrap?.user}
        statusCounts={bootstrap?.status_counts}
        workspace={bootstrap?.workspace}
      />

      <div className="body">
        <IncidentList
          incidents={bootstrap?.incidents}
          integrations={bootstrap?.integrations}
          workspace={bootstrap?.workspace}
        />

        <section className="center">
          <IncidentHeader workspace={bootstrap?.workspace} user={bootstrap?.user} />
          <Stepper steps={steps} />
          <Feed messages={messages} thinking={thinking} workspace={bootstrap?.workspace} />
        </section>

        <RightRail
          workspace={bootstrap?.workspace}
          actionDone={actionDone}
          artifacts={artifacts}
          metrics={bootstrap?.metrics}
        />
      </div>

      <PushToTalk listening={listening} phase={pttPhase} transcript={transcript} onToggle={toggleMic} />
    </div>
  )
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}
