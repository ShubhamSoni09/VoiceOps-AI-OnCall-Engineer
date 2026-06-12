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
  processTextStream,
} from './api.js'

function clockNow() {
  return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

// Preferred voice names in order — first match wins.
// Browser loads voices async; we pick on first call and cache.
const PREFERRED_VOICES = [
  'Google UK English Female',
  'Microsoft Sonia Online (Natural) - English (United Kingdom)',
  'Microsoft Aria Online (Natural) - English (United States)',
  'Karen',           // macOS
  'Samantha',        // macOS fallback
]
let _pinnedVoice = null

function pickVoice() {
  if (_pinnedVoice) return _pinnedVoice
  const voices = window.speechSynthesis?.getVoices() || []
  for (const name of PREFERRED_VOICES) {
    const v = voices.find((v) => v.name === name)
    if (v) { _pinnedVoice = v; return v }
  }
  // last resort: first English voice
  return voices.find((v) => v.lang.startsWith('en')) || null
}

function speak(text) {
  if (!text || !window.speechSynthesis) return
  window.speechSynthesis.cancel()
  const utt = new SpeechSynthesisUtterance(text)
  const voice = pickVoice()
  if (voice) utt.voice = voice
  utt.rate = 1.05
  utt.pitch = 1.0
  window.speechSynthesis.speak(utt)
}

// Voices load async on page load — cache them when ready
if (window.speechSynthesis) {
  window.speechSynthesis.onvoiceschanged = () => { _pinnedVoice = null; pickVoice() }
}

export default function App() {
  const { theme, toggle } = useTheme()
  const [authed, setAuthed] = useState(!!getToken())
  const [bootstrap, setBootstrap] = useState(null)
  const [loadError, setLoadError] = useState('')
  const [steps, setSteps] = useState(IDLE_STEPS)
  const [messages, setMessages] = useState([])
  const [artifacts, setArtifacts] = useState([])
  const [actionState, setActionState] = useState({ message: '', allTestsPass: false })
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

    // Reserve a slot for the live agent message
    idRef.current += 1
    const agentMsgId = idRef.current
    setMessages((prev) => [
      ...prev,
      {
        id: agentMsgId,
        role: 'agent',
        name: 'VoiceOps',
        role2: 'agent',
        when: clockNow(),
        html: '',
        liveStep: 'Understanding command…',
        liveOutput: '',
        diff: null,
        commandOutput: null,
        pending: true,
      },
    ])

    function patchMsg(patch) {
      setMessages((prev) =>
        prev.map((m) => (m.id === agentMsgId ? { ...m, ...patch } : m)),
      )
    }

    let outputBuf = ''

    try {
      const data = await processTextStream(
        text,
        sessionId,
        incidentContextFromBootstrap(bootstrap),
        (event) => {
          if (event.type === 'step') {
            setThinking(event.label)
            patchMsg({ liveStep: event.label })
          } else if (event.type === 'output') {
            outputBuf += event.chunk
            patchMsg({ liveOutput: outputBuf })
          } else if (event.type === 'diff') {
            patchMsg({ diff: event.text })
          }
        },
      )

      const orch = data?.orchestrator_result
      let html = `<p>${escapeHtml(data?.response_text || '')}</p>`
      if (orch?.files_changed?.length) {
        html += orch.files_changed
          .map((f) => `<div class="finding">Updated <b>${escapeHtml(f)}</b> in sandbox</div>`)
          .join('')
      }

      patchMsg({
        role2: data?.intent?.action || 'agent',
        html,
        liveStep: null,
        liveOutput: null,
        pending: false,
        diff: orch?.diff || null,
        commandOutput: orch?.command_output || null,
      })

      speak(data?.response_text || '')

      if (orch?.artifacts?.length) {
        setArtifacts((prev) => [...orch.artifacts, ...prev])
      }
      if (orch?.executed) {
        setActionState({
          message: data.response_text || (orch.all_tests_pass
            ? 'All tests passing — incidents resolved'
            : 'Patch applied — incidents stay open until all tests pass'),
          allTestsPass: !!orch.all_tests_pass,
        })
      }
      highlightStepper(data?.command?.action)
      if (orch?.executed) {
        const refreshed = await fetchBootstrap()
        setBootstrap(refreshed)
        setArtifacts(refreshed.artifacts || [])
      }
    } catch (err) {
      patchMsg({
        html: `<p>Sorry, I hit an error: <b>${escapeHtml(err.message)}</b></p>`,
        liveStep: null,
        liveOutput: null,
        pending: false,
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
          <IncidentHeader
            workspace={bootstrap?.workspace}
            user={bootstrap?.user}
          />
          <Stepper steps={steps} />
          <Feed messages={messages} workspace={bootstrap?.workspace} />
        </section>

        <RightRail
          workspace={bootstrap?.workspace}
          actionState={actionState}
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
