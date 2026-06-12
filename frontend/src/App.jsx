import { useEffect, useRef, useState } from 'react'
import { useTheme } from './useTheme.js'
import TopBar from './components/TopBar.jsx'
import IncidentList from './components/IncidentList.jsx'
import IncidentHeader from './components/IncidentHeader.jsx'
import Stepper from './components/Stepper.jsx'
import Feed from './components/Feed.jsx'
import RightRail from './components/RightRail.jsx'
import PushToTalk from './components/PushToTalk.jsx'
import { INITIAL_STEPS, SAMPLE_UTTERANCE, agentReplyFor } from './data.js'

export default function App() {
  const { theme, toggle } = useTheme()

  const [activeId, setActiveId] = useState('checkout')
  const [steps, setSteps] = useState(INITIAL_STEPS)
  const [messages, setMessages] = useState([])
  const [agentStatus, setAgentStatus] = useState('await') // await | deploying | hold | idle
  const [action, setAction] = useState({ status: 'pending', done: null }) // pending | approved | rejected
  const [listening, setListening] = useState(false)
  const [pttPhase, setPttPhase] = useState('idle') // idle | listening | captured
  const [transcript, setTranscript] = useState(null)

  const listeningRef = useRef(false)
  const timers = useRef([])
  const capturedTimer = useRef(null)
  const whenRef = useRef(51)
  const idRef = useRef(0)

  // clear every pending timeout on unmount
  useEffect(
    () => () => {
      timers.current.forEach(clearTimeout)
      clearTimeout(capturedTimer.current)
    },
    [],
  )

  function nextWhen() {
    whenRef.current = whenRef.current >= 59 ? 50 : whenRef.current + 1
    return '02:' + whenRef.current
  }

  function pushMsg(msg) {
    idRef.current += 1
    const id = idRef.current
    setMessages((prev) => [...prev, { id, ...msg }])
  }

  function addUserMessage(text) {
    pushMsg({ role: 'user', name: 'Priya Nair', role2: 'on-call', when: nextWhen(), text })
    const t = setTimeout(() => {
      pushMsg({ role: 'agent', name: 'VoiceOps', role2: 'agent', when: nextWhen(), html: agentReplyFor(text) })
    }, 700)
    timers.current.push(t)
  }

  function setStep(key, state) {
    setSteps((prev) => prev.map((s) => (s.key === key ? { ...s, state } : s)))
  }

  /* ---------- push to talk ---------- */
  function startListening() {
    if (listeningRef.current) return
    listeningRef.current = true
    clearTimeout(capturedTimer.current)
    setListening(true)
    setPttPhase('listening')
  }

  function stopListening() {
    if (!listeningRef.current) return
    listeningRef.current = false
    setListening(false)
    setPttPhase('captured')
    setTranscript(SAMPLE_UTTERANCE)
    addUserMessage(SAMPLE_UTTERANCE)
    capturedTimer.current = setTimeout(() => setPttPhase('idle'), 2600)
  }

  function toggleMic() {
    if (listeningRef.current) stopListening()
    else startListening()
  }

  /* ---------- approve / reject ---------- */
  function onApprove() {
    if (action.status !== 'pending') return
    setAction({ status: 'approved', done: { icon: 'rocket', text: 'Deploying preview to Render…' } })
    setAgentStatus('deploying')
    setSteps((prev) =>
      prev.map((s) =>
        s.key === 'pr' ? { ...s, state: 'done' } : s.key === 'deploy' ? { ...s, state: 'active' } : s,
      ),
    )

    const t1 = setTimeout(() => {
      setSteps((prev) =>
        prev.map((s) =>
          s.key === 'deploy' ? { ...s, state: 'done' } : s.key === 'verify' ? { ...s, state: 'active' } : s,
        ),
      )
      setAction({ status: 'approved', done: { icon: 'shield', text: 'Preview live · verifying health checks…' } })
      pushMsg({
        role: 'agent',
        name: 'VoiceOps',
        role2: 'agent',
        when: nextWhen(),
        html: 'Preview deployed to <b>checkout-api-preview.onrender.com</b>. Running health checks against /healthz, p99 and error-rate.',
      })
    }, 2200)

    const t2 = setTimeout(() => {
      setStep('verify', 'done')
      setAction({
        status: 'approved',
        done: { icon: 'check', green: true, text: 'Verified · error rate 4.7% → 0.04%, p99 1,840ms → 210ms' },
      })
      // prototype parity: the tail row keeps showing "Deploying PR #482…" after verify
      pushMsg({
        role: 'agent',
        name: 'VoiceOps',
        role2: 'verified',
        when: nextWhen(),
        html: 'Fix verified on the preview. <b>Error rate 4.7% → 0.04%</b>, p99 <b>1,840ms → 210ms</b>. Ready to promote to prod whenever you give the word.',
      })
    }, 4600)

    timers.current.push(t1, t2)
  }

  function onReject() {
    if (action.status !== 'pending') return
    setAction({ status: 'rejected', done: { icon: 'x', text: 'Rejected · agent will hold and suggest an alternative' } })
    setAgentStatus('hold')
  }

  /* ---------- hold-space to talk (refs keep handlers fresh) ---------- */
  const hRef = useRef({})
  hRef.current.start = startListening
  hRef.current.stop = stopListening
  const spaceRef = useRef(false)

  useEffect(() => {
    const down = (e) => {
      if (e.code === 'Space' && !spaceRef.current && e.target === document.body) {
        e.preventDefault()
        spaceRef.current = true
        hRef.current.start()
      }
    }
    const up = (e) => {
      if (e.code === 'Space' && spaceRef.current) {
        spaceRef.current = false
        hRef.current.stop()
      }
    }
    window.addEventListener('keydown', down)
    window.addEventListener('keyup', up)
    return () => {
      window.removeEventListener('keydown', down)
      window.removeEventListener('keyup', up)
    }
  }, [])

  return (
    <div className={'app' + (listening ? ' listening' : '')}>
      <TopBar theme={theme} onToggleTheme={toggle} />

      <div className="body">
        <IncidentList activeId={activeId} onSelect={setActiveId} />

        <section className="center">
          <IncidentHeader />
          <Stepper steps={steps} />
          <Feed messages={messages} agentStatus={agentStatus} />
        </section>

        <RightRail action={action} onApprove={onApprove} onReject={onReject} />
      </div>

      <PushToTalk listening={listening} phase={pttPhase} transcript={transcript} onToggle={toggleMic} />
    </div>
  )
}
