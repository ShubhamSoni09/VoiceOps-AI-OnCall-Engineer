import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
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
import { APP_CONSOLE_TITLE, APP_NAME } from './branding.js'
import {
  browserLiveSupport,
  browserSupportDiagnostic,
  clearedLiveCaptureError,
  getUserMediaWithTimeout,
  initialLiveDiagnostics,
  liveStatusDisplayMessage,
  liveRecorderTimesliceMs,
  microphoneFailureDiagnosticsUpdate,
  microphonePermissionDiagnostic,
  speechLanguage,
  speechUnavailableFallback,
  userMessageRoleLabel,
  voiceCaptureState,
} from './appVoiceSupport.js'
import {
  applySpeakerAttribution,
  buildLiveAudioPayload,
  chooseSupportedAudioMimeType,
  removeInstantCaption as removeInstantCaptionMessages,
  upsertInstantCaption as upsertInstantCaptionMessage,
  upsertLivePartial as upsertLivePartialMessage,
} from './liveTranscript.js'
import { canReconnect, reconnectDelay, reconnectMessage } from './liveRecovery.js'
import {
  mergeLiveCorrectionStats,
  mergeLiveLatencyStats,
  mergeLiveServerStats,
  resetLiveLatencyDiagnostics,
  resetLiveQueueDiagnostics,
} from './liveDiagnostics.js'
import { CAPTION_STATE, compactCaptionText } from './liveCaptions.js'
import { messagesFromRoom, mergeLocalDrafts } from './timelineMessages.js'
import { microphoneDiagnostics, microphoneErrorMessage, microphoneRecoveryHint } from './microphoneErrors.js'
import {
  approveAction,
  cancelAgentAssignment,
  cancelAgentRun,
  clearCompletedAgentAssignments,
  cloneRoomWorkspace,
  connectRoomWorkspace,
  connectLLMProviderApiKey,
  connectExternalAgentLocalCli,
  createAgentAssignment,
  commitAction,
  createPullRequestPlan,
  dispatchAgentAssignment,
  fetchAgentAssignments,
  fetchAgentLLMRouting,
  fetchAgentRuns,
  fetchBootstrap,
  fetchAgentSettings,
  fetchExternalAgentProviders,
  fetchGithubOAuthStatus,
  fetchLLMProviders,
  fetchRoom,
  fetchRoomAudit,
  fetchRoomHandoff,
  fetchWorkDashboard,
  fetchSpeakerRoom,
  fetchSpeakerValidation,
  getToken,
  ingestSpeakerSegments,
  incidentContextFromBootstrap,
  joinRoom,
  leaveRoom,
  mapSpeaker,
  preflightAgentLLMRoute,
  preflightLLMProvider,
  processText,
  recommendExternalAgent,
  rejectAction,
  retryAgentAssignment,
  roomEventsUrl,
  runExternalAgent,
  speakerLiveUrl,
  startExternalAgentOAuth,
  startGithubOAuth,
  disconnectLLMProvider,
  updateAgentSettings,
  updateAgentLLMRoute,
} from './api.js'

const DEFAULT_ROOM_ID = 'main'
const ROOM_ID = resolveRoomId()

export function resolveRoomId(locationLike = typeof window === 'undefined' ? null : window.location) {
  const search = String(locationLike?.search || '')
  const params = new URLSearchParams(search)
  const value = String(params.get('room_id') || params.get('room') || '').trim()
  return /^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/.test(value) ? value : DEFAULT_ROOM_ID
}

export function buildRoomStatus({
  loadError = '',
  collab = null,
  workDashboard = null,
  roomSyncStatus = 'connecting',
} = {}) {
  if (loadError) return { state: 'issue', message: loadError }
  if (!collab) return { state: 'checking', message: 'timeline syncing' }

  const dashboardSyncing = !workDashboard
  const updateSyncing = roomSyncStatus && roomSyncStatus !== 'live'
  return {
    state: 'ready',
    message: dashboardSyncing
      ? 'work dashboard syncing'
      : updateSyncing
        ? 'room updates syncing'
        : null,
    dashboardSyncing,
  }
}

export function activeWorkspaceInfo(workspace, room) {
  const roomPath = String(room?.workspace_path || '').trim()
  if (!roomPath) return workspace
  const githubMatch = roomPath.match(/github\.com[:/]([^/\s]+)\/([^/\s]+?)(?:\.git)?$/i)
  const workspacePath = String(workspace?.path || workspace?.configured_workspace || '').trim()
  const sameWorkspace = Boolean(workspacePath && workspacePath === roomPath)
  const name = githubMatch
    ? githubMatch[2]
    : roomPath.split(/[\\/]/).filter(Boolean).pop() || 'room repo'
  return {
    ...(sameWorkspace ? workspace : {}),
    connected: true,
    path: githubMatch ? null : roomPath,
    name,
    source: 'room',
    configured_workspace: roomPath,
    remote_url: githubMatch ? `https://github.com/${githubMatch[1]}/${githubMatch[2]}` : sameWorkspace ? workspace?.remote_url : null,
    remote_kind: githubMatch ? 'github' : sameWorkspace ? workspace?.remote_kind : 'none',
    remote_web_url: githubMatch ? `https://github.com/${githubMatch[1]}/${githubMatch[2]}` : sameWorkspace ? workspace?.remote_web_url : null,
    is_git_repo: githubMatch ? true : sameWorkspace ? workspace?.is_git_repo : false,
    persistence_note: githubMatch
      ? 'Room-scoped GitHub repository. Code actions use GitHub directly.'
      : 'Room-scoped repository. Code actions in this room use this local clone.',
  }
}

function clockNow() {
  return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

function arrayBufferToBase64(buffer) {
  const bytes = new Uint8Array(buffer)
  let binary = ''
  const chunkSize = 0x8000
  for (let index = 0; index < bytes.length; index += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(index, index + chunkSize))
  }
  return btoa(binary)
}

function meetingClosureSmokeMode() {
  if (typeof window === 'undefined') return ''
  const params = new URLSearchParams(window.location.search)
  if (!params.has('meeting_closure_smoke')) return ''
  const mode = params.get('meeting_closure_smoke') || 'preview'
  return mode === 'approve' ? 'approve' : 'preview'
}

function updateMeetingClosureSmokeStatus(update) {
  if (typeof window === 'undefined') return
  const previous = window.__voiceopsMeetingClosureSmoke || { steps: [] }
  const next = {
    ...previous,
    ...update,
    steps: update.step ? [...(previous.steps || []), update.step] : (previous.steps || []),
    updatedAt: new Date().toISOString(),
  }
  window.__voiceopsMeetingClosureSmoke = next
  return next
}

function appendStageEvent(previous, data, stage) {
  if (!stage) return previous.stageEvents || []
  const sequence = data.sequence ?? previous.stageSequence ?? null
  const currentEvents = stage === 'received'
    ? []
    : (previous.stageEvents || []).filter((event) => event.sequence === sequence)
  const elapsedMs = typeof data.elapsed_ms === 'number' ? data.elapsed_ms : null
  const previousElapsed = currentEvents.length ? currentEvents[currentEvents.length - 1].elapsedMs : 0
  const durationMs = elapsedMs == null ? null : Math.max(0, elapsedMs - (previousElapsed || 0))
  const nextEvent = {
    stage,
    sequence,
    elapsedMs,
    durationMs,
    message: data.message || '',
    state: data.state || '',
  }
  const last = currentEvents[currentEvents.length - 1]
  const nextEvents = last?.stage === stage
    ? [...currentEvents.slice(0, -1), nextEvent]
    : [...currentEvents, nextEvent]
  return nextEvents.slice(-8)
}

export default function App() {
  const { theme, toggle } = useTheme()
  const [authed, setAuthed] = useState(!!getToken())
  const [bootstrap, setBootstrap] = useState(null)
  const [loadError, setLoadError] = useState('')
  const [steps, setSteps] = useState(IDLE_STEPS)
  const [messages, setMessages] = useState([])
  const [artifacts, setArtifacts] = useState([])
  const [collab, setCollab] = useState(null)
  const [auditEvents, setAuditEvents] = useState([])
  const [handoff, setHandoff] = useState(null)
  const [workDashboard, setWorkDashboard] = useState(null)
  const [speakers, setSpeakers] = useState(null)
  const [speakerValidation, setSpeakerValidation] = useState(null)
  const [agentSettings, setAgentSettings] = useState(null)
  const [agentRuns, setAgentRuns] = useState([])
  const [agentAssignments, setAgentAssignments] = useState([])
  const [agentLLMRouting, setAgentLLMRouting] = useState(null)
  const [llmProviders, setLlmProviders] = useState([])
  const [externalAgentProviders, setExternalAgentProviders] = useState(null)
  const [githubOAuth, setGithubOAuth] = useState(null)
  const [agentSettingsSaving, setAgentSettingsSaving] = useState(false)
  const [agentSettingsError, setAgentSettingsError] = useState('')
  const [speakerError, setSpeakerError] = useState('')
  const [speakerSaving, setSpeakerSaving] = useState('')
  const [actionDone, setActionDone] = useState('')
  const [listening, setListening] = useState(false)
  const [liveMeeting, setLiveMeeting] = useState(false)
  const [liveStatus, setLiveStatus] = useState('')
  const [livePhase, setLivePhase] = useState('idle')
  const [liveTranscript, setLiveTranscript] = useState('')
  const [liveDiagnostics, setLiveDiagnostics] = useState(initialLiveDiagnostics)
  const [meetingClosureSmoke, setMeetingClosureSmoke] = useState(null)
  const [roomSyncStatus, setRoomSyncStatus] = useState('connecting')
  const [pttPhase, setPttPhase] = useState('idle')
  const [transcript, setTranscript] = useState(null)
  const [commandText, setCommandText] = useState('')
  const [thinking, setThinking] = useState('')
  const [processing, setProcessing] = useState(false)

  const idRef = useRef(0)
  const sessionId = bootstrap?.user?.id ? `voiceops-${bootstrap.user.id}` : 'voiceops-session'
  const activeWorkspace = useMemo(
    () => activeWorkspaceInfo(bootstrap?.workspace, collab?.room),
    [bootstrap?.workspace, collab?.room],
  )
  const roomReady = Boolean(bootstrap && collab)
  const recognitionRef = useRef(null)
  const liveSocketRef = useRef(null)
  const mediaRecorderRef = useRef(null)
  const roomSocketRef = useRef(null)
  const roomRefreshTimerRef = useRef(null)
  const liveStreamRef = useRef(null)
  const liveRecognitionRef = useRef(null)
  const liveActiveRef = useRef(false)
  const liveClosingRef = useRef(false)
  const liveBackpressureRef = useRef(false)
  const livePendingChunkRef = useRef(null)
  const liveReconnectTimerRef = useRef(null)
  const liveReconnectAttemptRef = useRef(0)
  const liveSessionIdRef = useRef('')
  const liveMimeTypeRef = useRef('audio/webm')
  const liveSequenceRef = useRef(1)
  const liveProviderRef = useRef('')
  const liveCaptionTextRef = useRef('')
  const liveCaptionLastSentRef = useRef('')
  const liveSmokeTimersRef = useRef([])
  const meetingClosureSmokeRanRef = useRef(false)
  const commandInputRef = useRef(null)
  const bootstrapRequestRef = useRef(null)

  useEffect(() => {
    let mounted = true
    setLiveDiagnostics((previous) => {
      const diagnostic = browserSupportDiagnostic()
      if (diagnostic.lastErrorKind) {
        return {
          ...previous,
          ...diagnostic,
          active: false,
          phase: 'idle',
          stage: 'unsupported',
        }
      }
      return {
        ...previous,
        ...diagnostic,
        lastErrorKind: '',
        lastErrorMessage: '',
        recoveryHint: '',
      }
    })
    microphonePermissionDiagnostic().then((diagnostic) => {
      if (!mounted) return
      setLiveDiagnostics((previous) => {
        if (previous.active) return { ...previous, microphonePermission: diagnostic.microphonePermission }
        if (diagnostic.lastErrorKind) {
          return {
            ...previous,
            ...diagnostic,
            active: false,
            phase: 'idle',
            stage: diagnostic.lastErrorKind === 'NotAllowedError' ? 'permission_denied' : 'unsupported',
          }
        }
        return {
          ...previous,
          microphonePermission: diagnostic.microphonePermission,
          permissionsApi: diagnostic.permissionsApi,
        }
      })
    })
    return () => {
      mounted = false
    }
  }, [])

  const agentParticipant = collab?.participants?.find((p) => p.kind === 'agent')
  const agentName = agentSettings?.display_name || agentParticipant?.name || APP_NAME
  const latestSmokeAction = meetingClosureSmokeMode()
    ? [...(collab?.actions || [])].reverse().find((action) => action.action === 'patch')
    : null
  const observedMeetingClosureSmoke = latestSmokeAction
    ? {
        status: latestSmokeAction.status === 'completed'
          ? 'completed'
          : latestSmokeAction.status === 'pending_approval'
            ? 'ready'
            : latestSmokeAction.status,
        steps: ['room_action_observed'],
      }
    : null
  const visibleMeetingClosureSmoke = observedMeetingClosureSmoke || meetingClosureSmoke || (meetingClosureSmokeMode()
    ? {
        status: 'running',
        steps: ['waiting_for_room_action'],
      }
    : null)

  const applyRoomSnapshot = useCallback((snapshot) => {
    const roomMessages = messagesFromRoom(snapshot)
    setCollab(snapshot)
    setMessages((prev) => mergeLocalDrafts(roomMessages, prev))
    setHandoff(snapshot?.handoff || null)
    const latestAction = snapshot?.actions?.[snapshot.actions.length - 1]
    if (latestAction?.summary) setActionDone(latestAction.summary)
  }, [])

  const refreshSpeakers = useCallback(async () => {
    const [state, validation] = await Promise.all([
      fetchSpeakerRoom(ROOM_ID),
      fetchSpeakerValidation(ROOM_ID),
    ])
    setSpeakers(state)
    setSpeakerValidation(validation)
    setSpeakerError('')
    return state
  }, [])

  const refreshRoom = useCallback(async () => {
    const [snapshot, audit, handoffResult, dashboard] = await Promise.all([
      fetchRoom(ROOM_ID),
      fetchRoomAudit(ROOM_ID, { limit: 8 }),
      fetchRoomHandoff(ROOM_ID),
      fetchWorkDashboard(ROOM_ID),
    ])
    snapshot.handoff = handoffResult
    applyRoomSnapshot(snapshot)
    setAuditEvents(audit)
    setWorkDashboard(dashboard)
    try {
      const [runs, assignments] = await Promise.all([
        fetchAgentRuns(ROOM_ID, 8),
        fetchAgentAssignments(ROOM_ID, 20),
      ])
      setAgentRuns(runs)
      setAgentAssignments(assignments)
    } catch (_err) {
      setAgentRuns([])
      setAgentAssignments([])
    }
    try {
      await refreshSpeakers()
    } catch (err) {
      setSpeakerError(err.message || 'Speaker state unavailable')
    }
    return snapshot
  }, [applyRoomSnapshot, refreshSpeakers])

  const refreshAgentSettings = useCallback(async () => {
    const settings = await fetchAgentSettings()
    setAgentSettings(settings)
    return settings
  }, [])

  const refreshExternalAgents = useCallback(async () => {
    try {
      const providers = await fetchExternalAgentProviders()
      setExternalAgentProviders(providers)
      return providers
    } catch (_err) {
      setExternalAgentProviders([])
      return []
    }
  }, [])

  const refreshAgentLLMRouting = useCallback(async () => {
    try {
      const routing = await fetchAgentLLMRouting(ROOM_ID)
      setAgentLLMRouting(routing)
      return routing
    } catch (_err) {
      setAgentLLMRouting(null)
      return null
    }
  }, [])

  const refreshLLMProviders = useCallback(async () => {
    try {
      const providers = await fetchLLMProviders()
      setLlmProviders(providers)
      return providers
    } catch (_err) {
      setLlmProviders([])
      return []
    }
  }, [])

  const refreshGithubOAuth = useCallback(async () => {
    try {
      const status = await fetchGithubOAuthStatus()
      setGithubOAuth(status)
      return status
    } catch (_err) {
      setGithubOAuth(null)
      return null
    }
  }, [])

  const scheduleRoomRefresh = useCallback((eventName) => {
    window.clearTimeout(roomRefreshTimerRef.current)
    roomRefreshTimerRef.current = window.setTimeout(async () => {
      try {
        await refreshRoom()
        await refreshExternalAgents()
        await refreshLLMProviders()
        await refreshAgentLLMRouting()
        if (eventName === 'agent_settings_updated') await refreshAgentSettings()
        setRoomSyncStatus('live')
      } catch (err) {
        setRoomSyncStatus('reconnecting')
      }
    }, 160)
  }, [refreshAgentLLMRouting, refreshAgentSettings, refreshExternalAgents, refreshLLMProviders, refreshRoom])

  const loadBootstrap = useCallback(async () => {
    if (bootstrapRequestRef.current) return bootstrapRequestRef.current

    bootstrapRequestRef.current = (async () => {
      try {
        const data = await fetchBootstrap()
        setBootstrap(data)
        setArtifacts(data.artifacts || [])
        const room = await joinRoom(ROOM_ID, {
          room_name: `${APP_NAME} team room`,
          project: data.workspace?.name || null,
        })
        applyRoomSnapshot(room)
        const [audit, dashboard] = await Promise.all([
          fetchRoomAudit(ROOM_ID, { limit: 8 }),
          fetchWorkDashboard(ROOM_ID),
        ])
        setAuditEvents(audit)
        setWorkDashboard(dashboard)
        Promise.allSettled([refreshAgentSettings(), refreshExternalAgents(), refreshLLMProviders(), refreshAgentLLMRouting(), refreshGithubOAuth()])
        try {
          const [runs, assignments] = await Promise.all([
            fetchAgentRuns(ROOM_ID, 8),
            fetchAgentAssignments(ROOM_ID, 20),
          ])
          setAgentRuns(runs)
          setAgentAssignments(assignments)
        } catch (_err) {
          setAgentRuns([])
          setAgentAssignments([])
        }
        try {
          await refreshSpeakers()
        } catch (err) {
          setSpeakerError(err.message || 'Speaker state unavailable')
        }
        setLoadError('')
      } catch (err) {
        setLoadError(err.message || 'Failed to load console')
        return null
      } finally {
        bootstrapRequestRef.current = null
      }
      return true
    })()

    return bootstrapRequestRef.current
  }, [applyRoomSnapshot, refreshAgentLLMRouting, refreshAgentSettings, refreshExternalAgents, refreshGithubOAuth, refreshLLMProviders, refreshSpeakers])

  const handleConnectWorkspace = useCallback(async (path) => {
    const room = await connectRoomWorkspace(ROOM_ID, path)
    applyRoomSnapshot(room)
    try {
      const dashboard = await fetchWorkDashboard(ROOM_ID)
      setWorkDashboard(dashboard)
    } catch {
      setWorkDashboard(null)
    }
    return room
  }, [applyRoomSnapshot])

  const handleCloneWorkspace = useCallback(async (remoteUrl, targetPath) => {
    const room = await cloneRoomWorkspace(ROOM_ID, remoteUrl, targetPath)
    applyRoomSnapshot(room)
    try {
      const dashboard = await fetchWorkDashboard(ROOM_ID)
      setWorkDashboard(dashboard)
    } catch {
      setWorkDashboard(null)
    }
    return room
  }, [applyRoomSnapshot])

  const handleConnectGithubOAuth = useCallback(async () => {
    const result = await startGithubOAuth()
    window.open(result.authorize_url, 'voiceops-github-oauth', 'width=720,height=760')
    window.setTimeout(refreshGithubOAuth, 1500)
    window.setTimeout(refreshGithubOAuth, 5000)
    return result
  }, [refreshGithubOAuth])

  useEffect(() => {
    document.title = APP_CONSOLE_TITLE
  }, [])

  useEffect(() => {
    function handleSessionExpired() {
      setAuthed(false)
      setBootstrap(null)
      setLoadError('')
      setRoomSyncStatus('connecting')
    }
    window.addEventListener('voiceops:session-expired', handleSessionExpired)
    return () => window.removeEventListener('voiceops:session-expired', handleSessionExpired)
  }, [])

  useEffect(() => {
    if (authed) loadBootstrap()
  }, [authed, loadBootstrap])

  useEffect(() => {
    if (!authed || !roomReady) return undefined

    const socket = new WebSocket(roomEventsUrl(ROOM_ID))
    roomSocketRef.current = socket
    setRoomSyncStatus('connecting')

    socket.onopen = () => setRoomSyncStatus('live')
    socket.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        if (data.event && data.event !== 'connected') scheduleRoomRefresh(data.event)
      } catch {
        setRoomSyncStatus('reconnecting')
      }
    }
    socket.onerror = () => setRoomSyncStatus('reconnecting')
    socket.onclose = () => {
      if (roomSocketRef.current === socket) setRoomSyncStatus('reconnecting')
    }

    return () => {
      window.clearTimeout(roomRefreshTimerRef.current)
      roomSocketRef.current = null
      socket.close()
    }
  }, [authed, roomReady, scheduleRoomRefresh])

  useEffect(() => {
    if (!authed) return undefined
    return () => {
      leaveRoom(ROOM_ID).catch(() => {})
    }
  }, [authed])

  useEffect(() => {
    const mode = meetingClosureSmokeMode()
    if (!mode || !authed || !bootstrap || !collab || meetingClosureSmokeRanRef.current) return
    if (latestSmokeAction?.status === 'completed') {
      recordMeetingClosureSmoke({
        status: 'completed',
        step: 'room_action_observed',
        actionId: latestSmokeAction.id,
        filesChanged: latestSmokeAction.files_changed || [],
      })
      meetingClosureSmokeRanRef.current = true
      return
    }
    meetingClosureSmokeRanRef.current = true
    runMeetingClosureSmoke(mode)
  }, [authed, bootstrap, collab, latestSmokeAction])

  function pushMsg(msg) {
    idRef.current += 1
    setMessages((prev) => [...prev, { id: idRef.current, ...msg }])
  }

  function recordMeetingClosureSmoke(update) {
    const next = updateMeetingClosureSmokeStatus(update)
    setMeetingClosureSmoke(next)
    return next
  }

  function upsertLivePartial(evt) {
    setMessages((prev) => upsertLivePartialMessage(prev, evt, clockNow()))
  }

  function upsertInstantCaption(text) {
    setMessages((prev) => upsertInstantCaptionMessage(prev, text, clockNow()))
  }

  function removeInstantCaption() {
    setMessages((prev) => removeInstantCaptionMessages(prev))
  }

  function applySpeakerUpdate(evt) {
    setMessages((prev) => applySpeakerAttribution(prev, evt, clockNow()))
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
      role2: userMessageRoleLabel(bootstrap?.user),
      when: clockNow(),
      text,
      fromVoice,
    })

    try {
      const data = await processText(text, sessionId, ROOM_ID, incidentContextFromBootstrap(bootstrap))
      let html = `<p>${escapeHtml(data.response_text || '')}</p>`
      const orch = data.orchestrator_result
      if (orch?.files_changed?.length) {
        const verb = orch.pending_approval ? 'Proposed' : 'Updated'
        html += orch.files_changed.map((f) => `<div class="finding">${verb} <b>${escapeHtml(f)}</b> in sandbox</div>`).join('')
      }
      pushMsg({
        role: 'agent',
        name: agentName,
        role2: `${data.intent?.action || 'agent'}`,
        when: clockNow(),
        html,
      })
      if (orch?.artifacts?.length) {
        setArtifacts((prev) => [...orch.artifacts, ...prev])
      }
      if (orch?.executed) setActionDone(data.response_text || 'Workspace action completed')
      highlightStepper(data.command?.action)
      await refreshRoom()
    } catch (err) {
      pushMsg({
        role: 'agent',
        name: agentName,
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

  async function joinSmokeBob() {
    const loginResponse = await fetch('/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: 'admin@voiceops.dev', password: 'admin123' }),
    })
    const loginData = await loginResponse.json()
    if (!loginResponse.ok) throw new Error(loginData.detail || 'Bob smoke login failed')
    const joinResponse = await fetch(`/collab/rooms/${encodeURIComponent(ROOM_ID)}/join`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${loginData.access_token}`,
      },
      body: JSON.stringify({ room_name: `${APP_NAME} team room`, project: bootstrap?.workspace?.name || null }),
    })
    const joinData = await joinResponse.json()
    if (!joinResponse.ok) throw new Error(joinData.detail || 'Bob smoke join failed')
    return loginData.access_token
  }

  async function approveSmokePatch(actionId, bobToken) {
    const response = await fetch(
      `/collab/rooms/${encodeURIComponent(ROOM_ID)}/actions/${encodeURIComponent(actionId)}/approve`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${bobToken}`,
        },
        body: JSON.stringify({ note: 'approved by UI smoke harness' }),
      },
    )
    const data = await response.json()
    if (!response.ok) throw new Error(data.detail || 'Bob smoke approval failed')
    return data
  }

  async function runMeetingClosureSmoke(mode) {
    recordMeetingClosureSmoke({ status: 'running', mode, step: 'started' })
    try {
      const bobToken = await joinSmokeBob()
      recordMeetingClosureSmoke({ step: 'bob_joined' })

      await mapSpeaker(ROOM_ID, 'SPEAKER_00', 'user-priya')
      await mapSpeaker(ROOM_ID, 'SPEAKER_01', 'user-admin')
      recordMeetingClosureSmoke({ step: 'speakers_mapped' })

      await ingestSpeakerSegments(ROOM_ID, {
        session_id: 'ui-meeting-closure-smoke',
        source: 'meeting_audio',
        segments: [
          {
            speaker_label: 'SPEAKER_00',
            start_ms: 0,
            end_ms: 1900,
            confidence: 0.9,
            text: 'We decided to keep the UI closure smoke deterministic and need to fix the failing health check in app.py',
          },
          {
            speaker_label: 'SPEAKER_01',
            start_ms: 2000,
            end_ms: 3200,
            confidence: 0.88,
            text: 'What is still open before Bob approves the patch?',
          },
        ],
      })
      recordMeetingClosureSmoke({ step: 'segments_ingested' })

      await sendUtterance('fix that', false)
      const proposedRoom = await refreshRoom()
      const pendingAction = [...(proposedRoom?.actions || [])].reverse()
        .find((action) => action.status === 'pending_approval' && action.action === 'patch')
      if (!pendingAction) throw new Error('UI smoke did not create a pending patch action')
      recordMeetingClosureSmoke({
        status: mode === 'approve' ? 'pending_approval' : 'ready',
        step: 'pending_patch_created',
        actionId: pendingAction.id,
        filesChanged: pendingAction.files_changed || [],
      })

      if (mode === 'approve') {
        const approved = await approveSmokePatch(pendingAction.id, bobToken)
        await refreshRoom()
        recordMeetingClosureSmoke({
          status: approved.status === 'completed' ? 'completed' : 'failed',
          step: 'patch_approved',
          actionId: approved.id,
          branch: approved.approval?.git?.branch_name || '',
          filesChanged: approved.approval?.git?.files_changed || approved.files_changed || [],
          testsPassed: /passed/i.test(approved.command_output || ''),
        })
      }
    } catch (err) {
      recordMeetingClosureSmoke({
        status: 'failed',
        step: 'failed',
        error: err.message || 'Meeting closure smoke failed',
      })
      setSpeakerError(err.message || 'Meeting closure smoke failed')
    }
  }

  function focusCommandInput(message) {
    if (message) {
      setTranscript(message)
      window.setTimeout(() => setTranscript(null), 3000)
    }
    window.setTimeout(() => commandInputRef.current?.focus(), 0)
  }

  function submitTypedCommand(event) {
    event.preventDefault()
    const text = commandText.trim()
    if (!text || processing) return
    setCommandText('')
    sendUtterance(text, false)
  }

  function startListening() {
    if (processing) return
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition
    if (SR) {
      const rec = new SR()
      rec.continuous = false
      rec.interimResults = true
      rec.lang = speechLanguage()
      rec.onresult = (e) => {
        const t = Array.from(e.results).map((r) => r[0].transcript).join('').trim()
        setTranscript(t)
        const latest = e.results[e.results.length - 1]
        if (latest?.isFinal && t) sendUtterance(t, true)
      }
      rec.onerror = (event) => {
        const diagnostic = microphoneDiagnostics(event, 'speech')
        const message = diagnostic.message
        setListening(false)
        setPttPhase('idle')
        patchLiveDiagnostics({
          ...microphoneFailureDiagnosticsUpdate(diagnostic, 'speech_error'),
          lastEvent: `speech_${diagnostic.kind}`,
        })
        focusCommandInput(message)
      }
      rec.onend = () => setListening(false)
      recognitionRef.current = rec
      try {
        rec.start()
        setListening(true)
        setPttPhase('listening')
    } catch (err) {
      const message = microphoneErrorMessage(err, 'speech')
      const recoveryHint = microphoneRecoveryHint(err, 'speech')
      recognitionRef.current = null
      setListening(false)
      setPttPhase('idle')
      patchLiveDiagnostics({
        active: false,
        phase: 'idle',
        stage: 'speech_start_error',
        lastEvent: 'speech_start_error',
        lastMessage: message,
        lastErrorKind: err?.name || 'SpeechStartError',
        lastErrorMessage: message,
        recoveryHint,
      })
      focusCommandInput(message)
    }
      return
    }
    const fallback = speechUnavailableFallback()
    if (fallback.startLiveMeeting) {
      setTranscript(fallback.message)
      setLiveStatus(fallback.message)
      void startLiveMeeting()
      return
    }
    patchLiveDiagnostics({
      active: false,
      phase: 'idle',
      stage: 'speech_unavailable',
      lastEvent: fallback.code,
      lastMessage: fallback.message,
      lastErrorKind: fallback.code,
      lastErrorMessage: fallback.message,
      recoveryHint: fallback.recoveryHint,
    })
    focusCommandInput(fallback.message)
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

  function startLiveCaptions() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition
    if (!SR) {
      patchLiveDiagnostics({
        captionState: CAPTION_STATE.UNAVAILABLE,
        captionPreview: '',
        lastEvent: 'captions_unavailable',
      })
      setLiveStatus('Streaming audio; instant captions unavailable in this browser')
      return
    }

    let finalText = ''
    const rec = new SR()
    rec.continuous = true
    rec.interimResults = true
    rec.lang = speechLanguage()
    patchLiveDiagnostics({
      captionState: CAPTION_STATE.STARTING,
      captionPreview: '',
      lastEvent: 'captions_starting',
    })
    rec.onresult = (event) => {
      let interimText = ''
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const part = event.results[i][0]?.transcript || ''
        if (event.results[i].isFinal) finalText = `${finalText} ${part}`.trim()
        else interimText = `${interimText} ${part}`.trim()
      }
      const visible = `${finalText} ${interimText}`.trim()
      if (!visible) return
      const compact = compactCaptionText(visible)
      liveCaptionTextRef.current = compact
      setLiveTranscript(compact)
      upsertInstantCaption(compact)
      patchLiveDiagnostics({
        captionState: CAPTION_STATE.ACTIVE,
        captionPreview: compactCaptionText(compact, 96),
        lastEvent: 'captions_active',
      })
    }
    rec.onerror = () => {
      patchLiveDiagnostics({
        captionState: CAPTION_STATE.PAUSED,
        lastEvent: 'captions_paused',
      })
      if (liveActiveRef.current) setLiveStatus('Streaming audio; instant captions paused')
    }
    rec.onend = () => {
      if (!liveActiveRef.current) return
      window.setTimeout(() => {
        if (!liveActiveRef.current) return
        try {
          patchLiveDiagnostics({
            captionState: CAPTION_STATE.STARTING,
            lastEvent: 'captions_restarting',
          })
          rec.start()
        } catch {
          patchLiveDiagnostics({
            captionState: CAPTION_STATE.PAUSED,
            lastEvent: 'captions_paused',
          })
          setLiveStatus('Streaming audio; instant captions paused')
        }
      }, 250)
    }
    liveRecognitionRef.current = rec
    try {
      rec.start()
    } catch {
      patchLiveDiagnostics({
        captionState: CAPTION_STATE.UNAVAILABLE,
        captionPreview: '',
        lastEvent: 'captions_unavailable',
      })
      setLiveStatus('Streaming audio; instant captions unavailable')
    }
  }

  function stopLiveCaptions() {
    liveActiveRef.current = false
    const rec = liveRecognitionRef.current
    liveRecognitionRef.current = null
    liveCaptionTextRef.current = ''
    liveCaptionLastSentRef.current = ''
    patchLiveDiagnostics({
      captionState: CAPTION_STATE.STOPPED,
      captionPreview: '',
      lastEvent: 'captions_stopped',
    })
    if (rec) {
      rec.onresult = null
      rec.onerror = null
      rec.onend = null
      try {
        if (typeof rec.abort === 'function') rec.abort()
        else rec.stop()
      } catch {
        // SpeechRecognition can already be inactive when the socket closes.
      }
    }
    setLiveTranscript('')
  }

  function liveSmokeModeEnabled() {
    return new URLSearchParams(window.location.search).has('live_smoke')
  }

  function patchLiveDiagnostics(update) {
    setLiveDiagnostics((prev) => {
      const base = { ...prev, ...browserLiveSupport() }
      return typeof update === 'function' ? update(base) : { ...base, ...update }
    })
  }

  function resetLiveAudioQueue() {
    liveBackpressureRef.current = false
    livePendingChunkRef.current = null
  }

  async function sendLiveAudioBuffer(ws, buffer, size, { queued = false } = {}) {
    if (!buffer || ws?.readyState !== WebSocket.OPEN) return false
    liveBackpressureRef.current = true
    const sequence = liveSequenceRef.current
    liveSequenceRef.current += 1
    const { payload, nextCaptionSent } = buildLiveAudioPayload({
      sequence,
      mimeType: liveMimeTypeRef.current,
      audioBase64: arrayBufferToBase64(buffer),
      provider: liveProviderRef.current,
      captionText: liveCaptionTextRef.current,
      lastCaptionSent: liveCaptionLastSentRef.current,
    })
    liveCaptionLastSentRef.current = nextCaptionSent
    ws.send(JSON.stringify(payload))
    setLiveDiagnostics((prev) => ({
      ...prev,
      ...browserLiveSupport(),
      active: true,
      phase: 'live',
      backendBusy: true,
      heldChunk: false,
      heldChunkKb: 0,
      stage: 'uploading',
      chunksSent: prev.chunksSent + 1,
      liveSequence: sequence,
      latencySequence: sequence,
      latencyStage: 'uploading',
      latencyElapsedMs: null,
      stageEvents: queued ? prev.stageEvents : [],
      stageTotalMs: queued ? prev.stageTotalMs : null,
      lastEvent: queued ? 'held_chunk_sent' : 'audio_chunk_sent',
      lastMessage: queued
        ? `Sent held ${Math.max(1, Math.round(size / 1024))} KB audio chunk`
        : `${Math.max(1, Math.round(size / 1024))} KB audio chunk`,
    }))
    return true
  }

  function holdLatestLiveChunk(buffer, size) {
    const replaced = Boolean(livePendingChunkRef.current)
    livePendingChunkRef.current = { buffer, size }
    setLiveDiagnostics((prev) => ({
      ...prev,
      ...browserLiveSupport(),
      active: true,
      phase: 'degraded',
      backendBusy: true,
      heldChunk: true,
      heldChunkKb: Math.max(1, Math.round(size / 1024)),
      replacedChunks: (prev.replacedChunks || 0) + (replaced ? 1 : 0),
      stage: prev.stage === 'idle' ? 'waiting_backend' : prev.stage,
      lastEvent: replaced ? 'held_chunk_replaced' : 'chunk_held',
      lastMessage: replaced
        ? 'Backend busy; replaced held audio with the latest chunk'
        : 'Backend busy; holding the latest audio chunk',
    }))
  }

  async function flushHeldLiveChunk() {
    if (liveBackpressureRef.current || !liveActiveRef.current) return false
    const pending = livePendingChunkRef.current
    const ws = liveSocketRef.current
    if (!pending || ws?.readyState !== WebSocket.OPEN) return false
    livePendingChunkRef.current = null
    return sendLiveAudioBuffer(ws, pending.buffer, pending.size, { queued: true })
  }

  async function handleLiveServerEvent(data) {
    if (!liveActiveRef.current && data.state !== 'stopped') return
    const nextStage = data.stage || (data.type === 'error' ? data.code || 'error' : data.state === 'listening' ? 'completed' : null)
    const backendReady = data.type === 'error'
      || data.stage === 'completed'
      || data.stage === 'failed'
      || data.stage === 'duplicate_chunk'
      || data.stage === 'out_of_order_chunk'
      || data.stage === 'empty_chunk'
      || data.stage === 'oversized_chunk'
      || data.state === 'listening'
      || data.state === 'stopped'
    const backendBusy = data.stage === 'busy'
      || data.stage === 'processing_slow'
      || data.state === 'processing'
      || (data.state === 'degraded' && data.stage !== 'failed')
    if (backendReady) liveBackpressureRef.current = false
    else if (backendBusy) liveBackpressureRef.current = true
    patchLiveDiagnostics((prev) => {
      const provider = data.provider || prev.provider
      if (data.provider) liveProviderRef.current = data.provider
      const device = data.device || prev.device
      const computeType = data.compute_type || prev.computeType
      const serverStats = mergeLiveServerStats(prev, data)
      const correctionStats = mergeLiveCorrectionStats(prev, data)
      const latencyStats = mergeLiveLatencyStats(prev, data, nextStage)
      return {
        ...prev,
        ...(data.state ? { active: data.state !== 'stopped' } : {}),
        phase: data.state || livePhase,
        provider,
        model: data.model || prev.model,
        device,
        computeType,
        cpuMode: provider === 'whisperx' && device === 'cpu',
        warmupRecommended: typeof data.warmup_recommended === 'boolean'
          ? data.warmup_recommended
          : prev.warmupRecommended,
        warmupHint: data.warmup_hint || prev.warmupHint,
        speakerVerificationStatus: data.speaker_verification_status || prev.speakerVerificationStatus,
        speakerVerificationReady: typeof data.speaker_verification_ready === 'boolean'
          ? data.speaker_verification_ready
          : prev.speakerVerificationReady,
        speakerVerificationCount: typeof data.speaker_verification_count === 'number'
          ? data.speaker_verification_count
          : prev.speakerVerificationCount,
        speakerVerificationLabels: Array.isArray(data.speaker_verification_labels)
          ? data.speaker_verification_labels
          : prev.speakerVerificationLabels,
        speakerVerificationQuality: data.speaker_verification_quality || prev.speakerVerificationQuality,
        speakerVerificationDetail: data.speaker_verification_detail || prev.speakerVerificationDetail,
        backendBusy: backendReady ? false : backendBusy ? true : prev.backendBusy,
        ...serverStats,
        ...correctionStats,
        ...latencyStats,
        heldChunk: backendReady && !livePendingChunkRef.current ? false : prev.heldChunk,
        heldChunkKb: backendReady && !livePendingChunkRef.current ? 0 : prev.heldChunkKb,
        stageSequence: data.sequence ?? prev.stageSequence,
        stageEvents: nextStage ? appendStageEvent(prev, data, nextStage) : prev.stageEvents,
        stageTotalMs: typeof data.elapsed_ms === 'number' ? data.elapsed_ms : prev.stageTotalMs,
        ...(nextStage ? { stage: nextStage } : {}),
        lastEvent: data.type || 'message',
        lastMessage: data.message || data.text || data.segment?.text || data.segments?.[0]?.text || '',
      }
    })
    if (data.type === 'session_status') {
      setLiveStatus(liveStatusDisplayMessage(data))
      setLivePhase(
        data.state === 'processing'
          ? 'calibrating'
          : data.state === 'listening'
            ? 'live'
            : data.state === 'degraded'
              ? 'degraded'
              : 'idle',
      )
    } else if (data.type === 'partial_transcript') {
      upsertLivePartial(data)
    } else if (data.type === 'speaker_segments' || data.type === 'speaker_correction') {
      applySpeakerUpdate(data)
      await refreshRoom()
    } else if (data.type === 'agent_response') {
      removeInstantCaption()
      setLiveStatus(`${agentName} joined the meeting`)
      await refreshRoom()
    } else if (data.type === 'error') {
      setLiveStatus(data.recoverable ? data.message : 'Live meeting stopped')
      if (!data.recoverable) {
        patchLiveDiagnostics({
          active: false,
          phase: 'idle',
          stage: data.code || 'error',
          lastEvent: 'error',
          lastMessage: data.message,
        })
      }
    }
    if (backendReady && data.state !== 'stopped') await flushHeldLiveChunk()
  }

  function clearLiveSmokeTimers() {
    liveSmokeTimersRef.current.forEach((timer) => window.clearTimeout(timer))
    liveSmokeTimersRef.current = []
  }

  function queueLiveSmokeChunks(ws) {
    const chunks = [
      'Alice mentioned app.py in the live meeting smoke path',
      'Bob is reviewing the speaker label correction',
      `${agentName} what files were mentioned?`,
    ]
    liveSmokeTimersRef.current = chunks.map((text, index) => window.setTimeout(() => {
      if (ws.readyState !== WebSocket.OPEN) return
      ws.send(JSON.stringify({
        type: 'audio_chunk',
        sequence: index + 1,
        mime_type: 'audio/webm',
        audio_base64: btoa(`voiceops-live-smoke-${index + 1}`),
        debug_text: text,
      }))
      patchLiveDiagnostics({
        active: true,
        phase: 'live',
        stage: 'uploading',
        stageEvents: [],
        stageTotalMs: null,
        latencySequence: index + 1,
        latencyStage: 'uploading',
        latencyElapsedMs: null,
        chunksSent: index + 1,
        lastEvent: 'audio_chunk_sent',
        lastMessage: text,
      })
    }, 160 + index * 620))
  }

  async function startLiveSmokeMeeting() {
    const ws = new WebSocket(speakerLiveUrl(ROOM_ID))
    liveSocketRef.current = ws
    liveActiveRef.current = true
    liveClosingRef.current = false
    resetLiveAudioQueue()
    liveProviderRef.current = ''
    liveCaptionTextRef.current = ''
    liveCaptionLastSentRef.current = ''
    setLiveStatus('Connecting live smoke')
    setLivePhase('calibrating')
    patchLiveDiagnostics({
      active: true,
      phase: 'calibrating',
      stage: 'connecting',
      chunksSent: 0,
      ...resetLiveQueueDiagnostics(),
      ...resetLiveLatencyDiagnostics(),
      backendBusy: false,
      stageEvents: [],
      stageTotalMs: null,
      lastEvent: 'connecting',
      lastMessage: 'Connecting live smoke',
    })

    ws.onopen = () => {
      ws.send(JSON.stringify({
        type: 'start',
        session_id: `${sessionId}-live-smoke`,
        mime_type: 'audio/webm',
      }))
      setLiveMeeting(true)
      setLivePhase('live')
      setLiveStatus('Running local live smoke')
      patchLiveDiagnostics({
        active: true,
        phase: 'live',
        stage: 'connected',
        backendBusy: false,
        lastEvent: 'socket_open',
        lastMessage: 'Running local live smoke',
      })
      queueLiveSmokeChunks(ws)
    }

    ws.onmessage = async (event) => {
      await handleLiveServerEvent(JSON.parse(event.data))
    }

    ws.onerror = () => {
      stopLiveMeeting()
      const message = 'Live smoke connection failed. Type a command below.'
      setLiveStatus(message)
      patchLiveDiagnostics({ active: false, phase: 'idle', lastEvent: 'error', lastMessage: message })
      focusCommandInput(message)
    }
    ws.onclose = () => {
      clearLiveSmokeTimers()
      stopLiveCaptions()
      resetLiveAudioQueue()
      setLiveMeeting(false)
      setLivePhase('idle')
      if (!liveClosingRef.current) {
        const message = 'Live smoke disconnected. Type a command below.'
        setLiveStatus(message)
        patchLiveDiagnostics({
          active: false,
          phase: 'idle',
          stage: 'disconnected',
          lastEvent: 'socket_closed',
          lastMessage: message,
        })
      }
    }
  }

  async function startLiveMeeting() {
    if (liveMeeting || processing) return
    setLiveStatus('')
    if (liveSmokeModeEnabled()) {
      startLiveSmokeMeeting()
      return
    }
    const support = browserLiveSupport()
    if (!support.mediaRecorder || !support.getUserMedia) {
      const code = !support.getUserMedia ? 'get-user-media-unavailable' : 'media-recorder-unavailable'
      const message = !support.getUserMedia
        ? 'This browser cannot use the microphone. Type a command below.'
        : 'This browser cannot record live audio. Type a command below.'
      const recoveryHint = microphoneRecoveryHint({ code })
      setLiveStatus(message)
      patchLiveDiagnostics({
        active: false,
        phase: 'idle',
        stage: 'unsupported',
        lastEvent: code,
        lastMessage: message,
        lastErrorKind: code,
        lastErrorMessage: message,
        recoveryHint,
      })
      focusCommandInput(message)
      return
    }

    liveActiveRef.current = true
    liveClosingRef.current = false
    window.clearTimeout(liveReconnectTimerRef.current)
    resetLiveAudioQueue()
    setLiveMeeting(true)
    setLiveStatus('Requesting microphone')
    setLivePhase('calibrating')
    patchLiveDiagnostics({
      ...clearedLiveCaptureError(),
      active: true,
      phase: 'calibrating',
      stage: 'requesting_microphone',
      chunksSent: 0,
      ...resetLiveQueueDiagnostics(),
      ...resetLiveLatencyDiagnostics(),
      backendBusy: false,
      stageEvents: [],
      stageTotalMs: null,
      lastEvent: 'requesting_microphone',
      lastMessage: 'Requesting microphone',
    })

    try {
      const stream = await getUserMediaWithTimeout(navigator.mediaDevices, { audio: true })
      if (!liveActiveRef.current) {
        stream.getTracks().forEach((track) => track.stop())
        return
      }
      patchLiveDiagnostics({
        ...clearedLiveCaptureError(),
        active: true,
        phase: 'calibrating',
        stage: 'microphone_ready',
        lastEvent: 'microphone_ready',
        lastMessage: 'Microphone ready',
      })
      const recorderMimeType = chooseSupportedAudioMimeType(MediaRecorder)
      const transportMimeType = recorderMimeType || 'audio/webm'
      liveSessionIdRef.current = `${sessionId}-live-${Date.now()}`
      liveMimeTypeRef.current = transportMimeType
      liveSequenceRef.current = 1
      liveProviderRef.current = ''
      liveCaptionTextRef.current = ''
      liveCaptionLastSentRef.current = ''
      liveReconnectAttemptRef.current = 0
      liveStreamRef.current = stream
      setLiveStatus('Connecting live meeting')
      patchLiveDiagnostics({
        active: true,
        phase: 'calibrating',
        stage: 'connecting',
        chunksSent: 0,
        ...resetLiveQueueDiagnostics(),
        ...resetLiveLatencyDiagnostics(),
        backendBusy: false,
        stageEvents: [],
        stageTotalMs: null,
        lastEvent: 'connecting',
        lastMessage: 'Connecting live meeting',
      })

      const openSocket = (reconnect = false) => {
        const ws = new WebSocket(speakerLiveUrl(ROOM_ID))
        liveSocketRef.current = ws

        ws.onopen = async () => {
          if (!liveActiveRef.current) {
            ws.close()
            return
          }
          const lastSequence = Math.max(0, liveSequenceRef.current - 1)
          liveBackpressureRef.current = false
          ws.send(JSON.stringify({
            type: 'start',
            session_id: liveSessionIdRef.current,
            resume_session_id: reconnect ? liveSessionIdRef.current : undefined,
            last_sequence: reconnect ? lastSequence : 0,
            mime_type: transportMimeType,
          }))
          liveReconnectAttemptRef.current = 0
          setLiveMeeting(true)
          setLivePhase('live')
          setLiveStatus(reconnect ? 'Live meeting reconnected' : 'Streaming room audio')
          patchLiveDiagnostics({
            ...clearedLiveCaptureError(),
            active: true,
            phase: 'live',
            stage: reconnect ? 'reconnected' : 'connected',
            backendBusy: false,
            reconnectAttempts: 0,
            lastEvent: reconnect ? 'socket_reconnected' : 'socket_open',
            lastMessage: reconnect ? 'Live meeting reconnected' : 'Streaming room audio',
          })

          if (!mediaRecorderRef.current) {
            const recorder = recorderMimeType ? new MediaRecorder(stream, { mimeType: recorderMimeType }) : new MediaRecorder(stream)
            mediaRecorderRef.current = recorder
            recorder.ondataavailable = async (event) => {
              if (!liveActiveRef.current) return
              if (!event.data?.size) return
              const buffer = await event.data.arrayBuffer()
              if (!liveActiveRef.current) return
              const currentWs = liveSocketRef.current
              if (currentWs?.readyState !== WebSocket.OPEN || liveBackpressureRef.current) {
                holdLatestLiveChunk(buffer, event.data.size)
                return
              }
              await sendLiveAudioBuffer(currentWs, buffer, event.data.size)
            }
            recorder.start(liveRecorderTimesliceMs())
            startLiveCaptions()
          }
          await flushHeldLiveChunk()
        }

        ws.onmessage = async (event) => {
          if (!liveActiveRef.current) return
          await handleLiveServerEvent(JSON.parse(event.data))
        }

        ws.onerror = () => {
          if (!liveActiveRef.current) return
          const message = 'Live meeting connection interrupted'
          setLiveStatus(message)
          patchLiveDiagnostics({
            active: true,
            phase: 'reconnecting',
            stage: 'socket_error',
            backendBusy: false,
            lastEvent: 'socket_error',
            lastMessage: message,
          })
          try {
            ws.close()
          } catch {
            // Browser already closed the socket.
          }
        }
        ws.onclose = () => {
          if (liveSocketRef.current === ws) liveSocketRef.current = null
          if (liveClosingRef.current || !liveActiveRef.current) return
          const attempt = liveReconnectAttemptRef.current + 1
          liveReconnectAttemptRef.current = attempt
          if (canReconnect(attempt)) {
            const message = reconnectMessage(attempt)
            setLiveMeeting(true)
            setLivePhase('reconnecting')
            setLiveStatus(message)
            patchLiveDiagnostics({
              active: true,
              phase: 'reconnecting',
              reconnectAttempts: attempt,
              backendBusy: false,
              stage: 'reconnecting',
              lastEvent: 'socket_closed',
              lastMessage: message,
            })
            window.clearTimeout(liveReconnectTimerRef.current)
            liveReconnectTimerRef.current = window.setTimeout(() => openSocket(true), reconnectDelay(attempt))
            return
          }
          stopLiveCaptions()
          resetLiveAudioQueue()
          setLiveMeeting(false)
          setLivePhase('idle')
          const message = reconnectMessage(attempt)
          setLiveStatus(message)
          patchLiveDiagnostics({
            active: false,
            phase: 'idle',
            stage: 'disconnected',
            reconnectAttempts: attempt - 1,
            lastEvent: 'socket_closed',
            lastMessage: message,
          })
          focusCommandInput(message)
        }
      }
      openSocket(false)
    } catch (err) {
      const diagnostic = microphoneDiagnostics(err, 'live')
      const message = diagnostic.message
      stopLiveMeeting()
      setLiveStatus(message)
      patchLiveDiagnostics(microphoneFailureDiagnosticsUpdate(diagnostic, 'microphone_error'))
      focusCommandInput(message)
    }
  }

  function stopLiveMeeting() {
    clearLiveSmokeTimers()
    window.clearTimeout(liveReconnectTimerRef.current)
    liveClosingRef.current = true
    liveActiveRef.current = false
    resetLiveAudioQueue()
    liveProviderRef.current = ''
    liveCaptionTextRef.current = ''
    liveCaptionLastSentRef.current = ''
    setLiveMeeting(false)
    setLivePhase('idle')
    setLiveStatus('')
    patchLiveDiagnostics({
      active: false,
      phase: 'idle',
      backendBusy: false,
      heldChunk: false,
      heldChunkKb: 0,
      stage: 'stopped',
      lastEvent: 'stopped',
      lastMessage: 'Live meeting stopped',
    })
    stopLiveCaptions()
    const recorder = mediaRecorderRef.current
    if (recorder) {
      recorder.ondataavailable = null
      recorder.onerror = null
      recorder.onstop = null
      try {
        if (recorder.state !== 'inactive') recorder.stop()
      } catch {
        // Recorder may already be inactive after browser-level mic shutdown.
      }
    }
    mediaRecorderRef.current = null
    liveStreamRef.current?.getTracks().forEach((track) => {
      try {
        track.stop()
      } catch {
        // Track can already be ended when the user stops permission or device capture.
      }
    })
    liveStreamRef.current = null
    const ws = liveSocketRef.current
    if (ws?.readyState === WebSocket.OPEN) {
      try {
        ws.send(JSON.stringify({ type: 'stop', reason: 'user' }))
      } catch {
        // Socket may close between the readyState check and send.
      }
      ws.onmessage = null
      ws.onerror = null
      ws.onclose = null
      window.setTimeout(() => {
        try {
          if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) ws.close()
        } catch {
          // Browser already closed the socket.
        }
      }, 250)
    } else if (ws) {
      ws.onmessage = null
      ws.onerror = null
      ws.onclose = null
    }
    liveSocketRef.current = null
    liveReconnectAttemptRef.current = 0
  }

  function toggleLiveMeeting() {
    if (liveMeeting) stopLiveMeeting()
    else startLiveMeeting()
  }

  async function handleMapSpeaker(speakerLabel, userId) {
    if (!speakerLabel || !userId || speakerSaving) return
    setSpeakerSaving(speakerLabel)
    try {
      await mapSpeaker(ROOM_ID, speakerLabel, userId)
      await refreshRoom()
      setSpeakerError('')
    } catch (err) {
      setSpeakerError(err.message || 'Speaker mapping failed')
    } finally {
      setSpeakerSaving('')
    }
  }

  async function handleSaveAgentSettings(nextSettings) {
    if (agentSettingsSaving) return false
    setAgentSettingsSaving(true)
    setAgentSettingsError('')
    try {
      const saved = await updateAgentSettings(ROOM_ID, nextSettings)
      setAgentSettings(saved)
      await refreshRoom()
      return true
    } catch (err) {
      setAgentSettingsError(err.message || 'Agent settings failed')
      return false
    } finally {
      setAgentSettingsSaving(false)
    }
  }

  async function handleApproveAction(actionId) {
    const action = await approveAction(ROOM_ID, actionId)
    setActionDone(action.summary)
    await refreshRoom()
    return action
  }

  async function handleRejectAction(actionId) {
    const action = await rejectAction(ROOM_ID, actionId)
    setActionDone(action.summary)
    await refreshRoom()
    return action
  }

  async function handleCommitAction(actionId, message = '') {
    const action = await commitAction(ROOM_ID, actionId, message)
    setActionDone(action.summary)
    await refreshRoom()
    return action
  }

  async function handleCreatePullRequestPlan(actionId, options = {}) {
    const result = await createPullRequestPlan(ROOM_ID, actionId, { dry_run: true, ...options })
    if (options.dry_run === false) await refreshRoom()
    return result
  }

  async function handleCancelAgentRun(runId) {
    const run = await cancelAgentRun(ROOM_ID, runId, 'cancelled from console')
    await refreshRoom()
    return run
  }

  async function handleStartExternalAgentOAuth(provider) {
    const result = await startExternalAgentOAuth(provider)
    if (result?.authorization_url) {
      window.open(result.authorization_url, `voiceops-${provider}-oauth`, 'width=720,height=760')
      window.setTimeout(refreshExternalAgents, 1500)
      window.setTimeout(refreshExternalAgents, 5000)
    }
    await refreshExternalAgents()
    return result
  }

  async function handleConnectExternalAgentLocalCli(provider, options = {}) {
    const result = await connectExternalAgentLocalCli(provider, options)
    await refreshExternalAgents()
    return result
  }

  async function handleRunExternalAgent(provider, prompt, mode = 'patch', model = '') {
    const result = await runExternalAgent(ROOM_ID, provider, prompt, mode, model)
    await refreshRoom()
    await refreshExternalAgents()
    return result
  }

  async function handleRecommendExternalAgent(task, options = {}) {
    return recommendExternalAgent(task, options)
  }

  async function handleUpdateAgentLLMRoute(route) {
    const result = await updateAgentLLMRoute(ROOM_ID, route)
    await refreshAgentLLMRouting()
    return result
  }

  async function handlePreflightAgentLLMRoute(route) {
    const result = await preflightAgentLLMRoute(ROOM_ID, route)
    await refreshAgentLLMRouting()
    return result
  }

  async function handleConnectLLMProvider(provider, body) {
    const result = await connectLLMProviderApiKey(provider, body)
    await refreshLLMProviders()
    await refreshAgentLLMRouting()
    return result
  }

  async function handlePreflightLLMProvider(provider, body) {
    return preflightLLMProvider(provider, body)
  }

  async function handleDisconnectLLMProvider(provider) {
    const result = await disconnectLLMProvider(provider)
    await refreshLLMProviders()
    await refreshAgentLLMRouting()
    return result
  }

  async function handleCreateAgentAssignment(assignment) {
    const result = await createAgentAssignment(ROOM_ID, assignment)
    setAgentAssignments((prev) => [result, ...prev.filter((item) => item.id !== result.id)].slice(0, 8))
    refreshRoom().catch(() => {})
    return result
  }

  async function handleDispatchAgentAssignment(assignmentId) {
    const result = await dispatchAgentAssignment(ROOM_ID, assignmentId)
    setAgentAssignments((prev) => [result, ...prev.filter((item) => item.id !== result.id)].slice(0, 8))
    await refreshRoom()
    return result
  }

  async function handleCancelAgentAssignment(assignmentId) {
    const result = await cancelAgentAssignment(ROOM_ID, assignmentId, 'cancelled from dashboard queue')
    setAgentAssignments((prev) => [result, ...prev.filter((item) => item.id !== result.id)].slice(0, 8))
    await refreshRoom()
    return result
  }

  async function handleRetryAgentAssignment(assignmentId) {
    const result = await retryAgentAssignment(ROOM_ID, assignmentId)
    setAgentAssignments((prev) => [result, ...prev.filter((item) => item.id !== result.id)].slice(0, 8))
    await refreshRoom()
    return result
  }

  async function handleClearCompletedAgentAssignments() {
    const result = await clearCompletedAgentAssignments(ROOM_ID)
    setAgentAssignments(result.assignments || [])
    await refreshRoom()
    return result
  }

  useEffect(() => {
    const down = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        focusCommandInput()
        return
      }
      if (e.code === 'Space' && e.target === document.body && !processing && !liveMeeting) {
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
  }, [processing, listening, liveMeeting])

  useEffect(() => () => stopLiveMeeting(), [])

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

  const voiceCapture = voiceCaptureState(liveDiagnostics)
  const inputStatus = voiceCapture.unavailable
    ? {
      detail: 'text only',
      title: voiceCapture.message || 'Voice capture is unavailable. Typed commands still work.',
      ready: false,
    }
    : {
      detail: 'text or voice',
      title: 'Typed commands and voice capture are available.',
      ready: true,
    }
  const roomStatus = buildRoomStatus({ loadError, collab, workDashboard, roomSyncStatus })

  return (
    <div className={'app' + (listening ? ' listening' : '') + (liveMeeting ? ' live' : '')}>
      <TopBar
        theme={theme}
        onToggleTheme={toggle}
        user={bootstrap?.user}
        statusCounts={bootstrap?.status_counts}
        workspace={activeWorkspace}
        roomSyncStatus={roomSyncStatus}
        meetingClosureSmoke={visibleMeetingClosureSmoke}
      />

      <div className="body">
        <IncidentList
          incidents={bootstrap?.incidents}
          integrations={bootstrap?.integrations}
          workspace={activeWorkspace}
          participants={collab?.participants}
          speakers={speakers}
          speakerValidation={speakerValidation}
          speakerError={speakerError}
          speakerSaving={speakerSaving}
          agentSettings={agentSettings}
          agentSettingsSaving={agentSettingsSaving}
          agentSettingsError={agentSettingsError}
          workDashboard={workDashboard}
          actions={collab?.actions}
          agentAssignments={agentAssignments}
          roomStatus={roomStatus}
          inputStatus={inputStatus}
          onSaveAgentSettings={bootstrap?.user?.permissions?.includes('admin:manage') ? handleSaveAgentSettings : undefined}
          onMapSpeaker={handleMapSpeaker}
        />

        <section className="center">
          <IncidentHeader workspace={activeWorkspace} user={bootstrap?.user} roomStatus={roomStatus} agentName={agentName} />
          <Stepper steps={steps} />
          <Feed
            messages={messages}
            thinking={thinking}
            workspace={activeWorkspace}
            roomStatus={roomStatus}
            agentName={agentName}
          />
        </section>

        {roomReady ? (
          <RightRail
            roomId={ROOM_ID}
            user={bootstrap.user}
            workspace={activeWorkspace}
            actionDone={actionDone}
            artifacts={artifacts}
            handoff={handoff}
            workDashboard={workDashboard}
            actions={collab?.actions}
            agentRuns={agentRuns}
            agentAssignments={agentAssignments}
            agentLLMRouting={agentLLMRouting}
            llmProviders={llmProviders}
            externalAgentProviders={externalAgentProviders}
            messages={messages}
            auditEvents={auditEvents}
            memory={collab?.memory}
            agentName={agentName}
            liveDiagnostics={liveDiagnostics}
            metrics={bootstrap.metrics}
            onApproveAction={handleApproveAction}
            onRejectAction={handleRejectAction}
            onCommitAction={handleCommitAction}
            onCreatePullRequest={handleCreatePullRequestPlan}
            onCancelAgentRun={handleCancelAgentRun}
            onStartExternalAgentOAuth={handleStartExternalAgentOAuth}
            onConnectExternalAgentLocalCli={handleConnectExternalAgentLocalCli}
            onRunExternalAgent={handleRunExternalAgent}
            onRecommendExternalAgent={handleRecommendExternalAgent}
            onUpdateAgentLLMRoute={handleUpdateAgentLLMRoute}
            onPreflightAgentLLMRoute={handlePreflightAgentLLMRoute}
            onConnectLLMProvider={handleConnectLLMProvider}
            onPreflightLLMProvider={handlePreflightLLMProvider}
            onDisconnectLLMProvider={handleDisconnectLLMProvider}
            onConnectWorkspace={handleConnectWorkspace}
            onCloneWorkspace={handleCloneWorkspace}
            githubOAuth={githubOAuth}
            onConnectGithubOAuth={bootstrap?.user?.permissions?.includes('admin:manage') ? handleConnectGithubOAuth : undefined}
            onCreateAgentAssignment={handleCreateAgentAssignment}
            onDispatchAgentAssignment={handleDispatchAgentAssignment}
            onCancelAgentAssignment={handleCancelAgentAssignment}
            onRetryAgentAssignment={handleRetryAgentAssignment}
            onClearCompletedAgentAssignments={handleClearCompletedAgentAssignments}
          />
        ) : null}
      </div>

      <PushToTalk
        listening={listening}
        phase={liveMeeting ? livePhase : pttPhase}
        transcript={liveMeeting ? (liveTranscript || null) : transcript}
        onToggle={toggleMic}
        commandText={commandText}
        commandInputRef={commandInputRef}
        onCommandTextChange={setCommandText}
        onCommandSubmit={submitTypedCommand}
        processing={processing}
        liveMeeting={liveMeeting}
        liveStatus={liveStatus}
        agentName={agentName}
        onToggleLive={toggleLiveMeeting}
        voiceUnavailable={voiceCapture.unavailable}
        voiceUnavailableMessage={voiceCapture.message}
        voiceRecoveryHint={voiceCapture.recoveryHint}
        voiceRetryable={voiceCapture.retryable}
      />
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
