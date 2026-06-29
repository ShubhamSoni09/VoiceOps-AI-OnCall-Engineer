import { useEffect, useState } from 'react'
import {
  Check,
  CheckCircle,
  Cloud,
  Code,
  Database,
  FloppyDisk,
  GithubLogo,
  PlugsConnected,
  Robot,
  SlackLogo,
  WarningCircle,
  Waveform,
} from '@phosphor-icons/react'
import { buildSpeakerCalibrationRows, pct } from '../speakerCalibration.js'
import { teammateRoleLabel } from '../roleLabels.js'
import { APP_INITIAL, APP_NAME } from '../branding.js'
import {
  sessionSetupDetail,
  speakerValidationSummary,
  teamRoomCompactText,
  teamRoomSummaryLabel,
  teamSpeakerSettingsMeta,
  workDashboardLeftRailStatus,
} from '../sessionRailModel.js'

const INTEG_ICON = {
  github: GithubLogo,
  mcp: Code,
  render: Cloud,
  clickhouse: Database,
  slack: SlackLogo,
}

export function integrationDisplayLabel(integration = {}) {
  const label = String(integration.label || '').trim()
  if (integration.id === 'slack' && /pagerduty/i.test(label)) return 'Slack'
  return label
}

function SpeakerBlock({ speakers, speakerValidation, participants, speakerError, speakerSaving, onMapSpeaker }) {
  const rows = buildSpeakerCalibrationRows(speakers, participants || [])
  const hasAssigned = rows.some((row) => row.mappedUserName)
  const hasUnresolved = rows.some((row) => !row.mappedUserName)
  const validationSummary = speakerValidationSummary(speakerValidation)
  const ValidationIcon = validationSummary?.tone === 'ready' ? CheckCircle : WarningCircle

  return (
    <div className="speaker-block">
      <div className="speaker-title">
        <span><Waveform size={14} /> Speakers</span>
        <small>{speakers?.provider || 'mock'}</small>
      </div>

      {validationSummary && (
        <div className={`speaker-validation ${validationSummary.tone}`}>
          <span><ValidationIcon size={13} /> {validationSummary.label}</span>
          <small>{validationSummary.detail}</small>
        </div>
      )}

      {hasAssigned && (
        <div className="speaker-map-list">
          {rows.filter((row) => row.mappedUserName).map((row) => (
            <div className="speaker-map" key={row.speakerLabel}>
              <span className="mono">{row.speakerLabel}</span>
              <b>{row.mappedUserName}</b>
              <small>{row.source || 'manual'}</small>
            </div>
          ))}
        </div>
      )}

      {hasUnresolved ? (
        <div className="unknown-list">
          {rows.filter((row) => !row.mappedUserName).map((row) => (
            <label className="unknown-speaker" key={row.speakerLabel}>
              <span className="unknown-main">
                <b>
                  <span className="mono">{row.speakerLabel}</span>
                  <span className={`speaker-status ${row.status.tone}`}>{row.status.label}</span>
                </b>
                <small>{pct(row.confidence)} confidence{row.preview ? ` - ${row.preview}` : ''}</small>
              </span>
              <select
                aria-label={`Assign ${row.speakerLabel} to teammate`}
                defaultValue=""
                disabled={speakerSaving === row.speakerLabel || row.assignableParticipants.length === 0}
                onChange={(e) => onMapSpeaker?.(row.speakerLabel, e.target.value)}
              >
                <option value="" disabled>
                  {speakerSaving === row.speakerLabel ? 'Saving' : 'Assign'}
                </option>
                {row.assignableParticipants.map((p) => (
                  <option value={p.id} key={p.id}>{p.name}</option>
                ))}
              </select>
            </label>
          ))}
        </div>
      ) : rows.length === 0 ? (
        <div className="speaker-empty">No speaker labels yet. Start a live meeting or ingest mock segments to map voices to teammates.</div>
      ) : null}

      {speakerValidation?.next_steps?.[0] && (
        <div className="speaker-next-step">{speakerValidation.next_steps[0]}</div>
      )}

      {speakerError && <div className="speaker-error" role="alert">{speakerError}</div>}
    </div>
  )
}

function AgentSettingsBlock({
  settings,
  saving,
  error,
  onSave,
}) {
  const canEdit = Boolean(onSave)
  const [displayName, setDisplayName] = useState(settings?.display_name || APP_NAME)
  const [initials, setInitials] = useState(settings?.initials || APP_INITIAL)
  const [wakeWords, setWakeWords] = useState((settings?.wake_words || []).join(', '))
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    setDisplayName(settings?.display_name || APP_NAME)
    setInitials(settings?.initials || APP_INITIAL)
    setWakeWords((settings?.wake_words || []).join(', '))
  }, [settings])

  async function submit(e) {
    e.preventDefault()
    if (!canEdit) return
    const name = displayName.trim()
    if (!name || saving) return
    const ok = await onSave({
      display_name: name,
      initials: initials.trim() || undefined,
      wake_words: wakeWords
        .split(',')
        .map((word) => word.trim())
        .filter(Boolean),
    })
    if (!ok) return
    setSaved(true)
    window.setTimeout(() => setSaved(false), 1400)
  }

  return (
    <form className="agent-settings" onSubmit={submit}>
      <div className="agent-settings-title">
        <span><Robot size={14} /> AI teammate</span>
        {saved && !error ? <small><Check size={12} /> saved</small> : <small>{canEdit ? 'admin' : 'read-only'}</small>}
      </div>
      <div className="agent-fields">
        <label>
          Name
          <input
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            maxLength={40}
            aria-label="AI teammate name"
            readOnly={!canEdit}
            required
          />
        </label>
        <label className="initials-field">
          Initials
          <input
            value={initials}
            onChange={(e) => setInitials(e.target.value.toUpperCase())}
            maxLength={4}
            aria-label="AI teammate initials"
            readOnly={!canEdit}
          />
        </label>
      </div>
      <label className="wake-field">
        Wake words
        <input
          value={wakeWords}
          onChange={(e) => setWakeWords(e.target.value)}
          placeholder="ada, assistant, agent"
          aria-label="AI teammate wake words"
          readOnly={!canEdit}
        />
      </label>
      {canEdit ? (
        <button
          className="agent-save"
          type="submit"
          aria-label={saving ? 'Saving AI teammate settings' : 'Save AI teammate settings'}
          disabled={saving || !displayName.trim()}
        >
          <FloppyDisk size={13} />
          <span>{saving ? 'Saving' : 'Save settings'}</span>
        </button>
      ) : (
        <small className="agent-settings-note">Admin only.</small>
      )}
      {error && <div className="agent-settings-error" role="alert">{error}</div>}
    </form>
  )
}

function LeftRailDetails({ title, meta, open = false, children }) {
  const [isOpen, setIsOpen] = useState(open)

  useEffect(() => {
    if (open) setIsOpen(true)
  }, [open])

  return (
    <details
      className="left-rail-details"
      open={isOpen}
      onToggle={(event) => setIsOpen(event.currentTarget.open)}
    >
      <summary>
        <span>{title}</span>
        {meta && <small>{meta}</small>}
      </summary>
      <div className="left-rail-details-body">
        {children}
      </div>
    </details>
  )
}

function queueOpenCount(workDashboard, agentAssignments = []) {
  const queueHealth = Array.isArray(workDashboard?.queue_health) ? workDashboard.queue_health : []
  const openHealth = queueHealth.find((item) => String(item.label || '').toLowerCase() === 'open')
  const healthValue = Number(openHealth?.value)
  if (Number.isFinite(healthValue) && healthValue > 0) return healthValue
  return (agentAssignments || []).filter((assignment) => (
    !['completed', 'failed', 'cancelled'].includes(String(assignment.status || ''))
  )).length
}

function sessionActivitySummary({ workDashboard, actions = [], agentAssignments = [] } = {}) {
  const approvals = Array.isArray(workDashboard?.approvals) ? workDashboard.approvals.length : 0
  const openQueue = queueOpenCount(workDashboard, agentAssignments)
  const openItems = Array.isArray(workDashboard?.open_items) ? workDashboard.open_items.length : 0
  const actionCount = Array.isArray(actions) ? actions.length : 0
  const total = approvals + openQueue + openItems + actionCount
  return {
    total,
    approvals,
    openQueue,
    openItems,
    actionCount,
    hasActivity: total > 0,
  }
}

function normalizedRoomState(roomStatus) {
  return String(roomStatus?.state || 'ready')
}

function WorkItemsEmptyState({ workspace, activity, roomStatus, inputStatus }) {
  const known = Boolean(workspace)
  const connected = Boolean(workspace?.connected)
  const roomState = connected ? normalizedRoomState(roomStatus) : 'setup'
  const roomSyncing = roomState === 'checking' || roomState === 'connecting' || roomState === 'syncing'
  const roomIssue = roomState === 'issue'
  const dashboardSyncing = Boolean(roomStatus?.dashboardSyncing)
  const hasActivity = connected && activity?.hasActivity
  const dashboardStatus = workDashboardLeftRailStatus(activity)
  const title = !known
    ? 'Preparing session'
    : !connected
      ? 'Connect local repo'
      : roomIssue
        ? 'Room sync issue'
        : roomSyncing
          ? 'Syncing room'
          : dashboardSyncing
            ? 'Room ready'
          : hasActivity
            ? 'Session active'
            : 'No tracked work yet'
  const detail = sessionSetupDetail({ known, connected, hasActivity, roomState, dashboardSyncing })
  const rows = [
    {
      label: 'Repository',
      detail: !known ? 'loading' : connected ? 'connected' : 'patches need repo',
      title: !known ? 'loading' : connected ? (workspace.name || 'connected') : 'patches need repo',
      ready: connected,
    },
    {
      label: 'Work tracking',
      detail: !known
        ? 'loading'
        : !connected
          ? 'text + voice ready'
          : roomIssue
            ? 'sync issue'
            : roomSyncing
              ? 'checking'
              : dashboardSyncing
                ? 'syncing'
              : dashboardStatus.detail,
      title: !known
        ? 'loading'
        : !connected
          ? 'text and live meeting work before repo setup'
          : roomIssue
            ? roomStatus?.message || 'room data unavailable'
            : roomSyncing
              ? roomStatus?.message || 'loading room state'
              : dashboardSyncing
                ? roomStatus?.message || 'work dashboard syncing'
              : dashboardStatus.title,
      ready: Boolean(!roomIssue && !roomSyncing && !dashboardSyncing && hasActivity && dashboardStatus.ready),
    },
    {
      label: 'Input',
      detail: inputStatus?.detail || 'text or voice',
      title: inputStatus?.title || inputStatus?.detail || 'text or voice',
      ready: inputStatus?.ready !== false,
    },
    {
      label: 'AI teammate',
      detail: 'name + wake words',
      ready: true,
    },
  ]

  return (
    <div className={`rail-empty-state ${connected && !roomIssue && !roomSyncing ? 'ready' : 'attention'}`}>
      <div className="rail-empty-head">
        <PlugsConnected size={22} />
        <div>
          <b>{title}</b>
          <p>{detail}</p>
        </div>
      </div>
      <div className="rail-empty-steps" aria-label="Workspace setup status">
        {rows.map((row) => {
          const Icon = row.ready ? CheckCircle : WarningCircle
          return (
            <div className={row.ready ? 'ready' : 'attention'} key={row.label}>
              <Icon size={13} />
              <span>{row.label}</span>
              <small title={row.title || row.detail}>{row.detail}</small>
            </div>
          )
        })}
      </div>
    </div>
  )
}

export default function IncidentList({
  incidents,
  integrations,
  workspace,
  participants,
  speakers,
  speakerValidation,
  speakerError,
  speakerSaving,
  agentSettings,
  agentSettingsSaving,
  agentSettingsError,
  workDashboard,
  actions,
  agentAssignments,
  roomStatus,
  inputStatus,
  onSaveAgentSettings,
  onMapSpeaker,
}) {
  const open = (incidents || []).filter((i) => String(i.status || '').toLowerCase() !== 'resolved')
  const activity = sessionActivitySummary({ workDashboard, actions, agentAssignments })
  const people = participants || []
  const onlinePeople = people.filter((person) => person.online)
  const agentName = agentSettings?.display_name || APP_NAME
  const agentInitials = agentSettings?.initials || APP_INITIAL
  const wakeWord = (agentSettings?.wake_words || [])[0]
  const speakerSummary = speakerValidationSummary(speakerValidation)
  const SpeakerSummaryIcon = speakerSummary?.tone === 'ready' ? CheckCircle : WarningCircle
  const showTeamPresence = people.length > 0 || Boolean(agentSettings) || Boolean(speakerSummary)
  const shouldOpenTeamSettings = Boolean(agentSettingsError || speakerError || speakerValidation?.unknown_speaker_count)
  const settingsMeta = teamSpeakerSettingsMeta(people, speakerValidation)
  const hasOpenItems = open.length > 0
  const roomState = workspace?.connected ? normalizedRoomState(roomStatus) : 'setup'
  const roomSyncing = roomState === 'checking' || roomState === 'connecting' || roomState === 'syncing'
  const roomIssue = roomState === 'issue'
  const dashboardSyncing = Boolean(roomStatus?.dashboardSyncing)
  const railTitle = hasOpenItems ? 'Work items' : workspace?.connected ? 'Team room' : 'Session setup'
  const railMeta = hasOpenItems
    ? `${open.length} open`
    : workspace
      ? workspace.connected
        ? roomIssue
          ? 'sync issue'
          : roomSyncing
            ? 'checking'
            : dashboardSyncing
              ? 'syncing'
              : `${onlinePeople.length} online`
        : 'repo for patches'
      : 'checking'
  const showWorkItemsStatus = !workspace?.connected
    || !activity.hasActivity
    || roomIssue
    || roomSyncing
    || dashboardSyncing
    || inputStatus?.ready === false
  const showTeamBlockTitle = railTitle !== 'Team room'
  const hasLeftRailBody = open.length > 0 || showWorkItemsStatus

  return (
    <aside className={`rail rail--left${hasLeftRailBody ? '' : ' rail--left-no-body'}`}>
      <div className="rail-head">
        <h2>{railTitle}</h2>
        <span className="count">{railMeta}</span>
      </div>

      {hasLeftRailBody && (
        <div className="scroll" style={{ flex: 1 }}>
          {open.length > 0 ? (
          <div className="inc-list" role="list" aria-label="Tracked work items">
            {open.map((inc, idx) => (
              <div key={inc.id || idx} className={`inc${idx === 0 ? ' live' : ''}`} role="listitem">
                <span className="sev act" />
                <span className="inc-main">
                  <span className="inc-title">{inc.title}</span>
                  <span className="inc-meta">
                    <span className="mono svc">{inc.service}</span>
                    <span className="inc-sev-tag">{inc.severity || inc.status}</span>
                  </span>
                </span>
              </div>
            ))}
          </div>
          ) : (
          <WorkItemsEmptyState
            workspace={workspace}
            activity={activity}
            roomStatus={roomStatus}
            inputStatus={inputStatus}
          />
          )}
        </div>
      )}

      <div className="rail-foot">
        {showTeamPresence && (
          <div className="team-block">
            {showTeamBlockTitle && (
              <div className="team-title">
                <span>Team room</span>
                <small>{onlinePeople.length} online</small>
              </div>
            )}
            <div className="team-compact" aria-label={teamRoomSummaryLabel(people)}>
              <span className="avatar-stack">
                {people.slice(0, 2).map((p) => (
                  <span className="mini-avatar" key={p.id}>{p.initials}</span>
                ))}
                <span className="mini-avatar mini-avatar--agent">{agentInitials}</span>
              </span>
              <small>{teamRoomCompactText(people)}</small>
              <small className="team-compact-status">{agentName} · {speakerSummary?.label || 'speakers'}</small>
            </div>
            {people.map((p) => (
              <div className="person" key={p.id}>
                <span className={`presence${p.online ? ' on' : ''}`} />
                <span className="mini-avatar">{p.initials}</span>
                <span className="person-main">
                  <b>{p.name}</b>
                  <small>{teammateRoleLabel(p.role_label || p.kind)}</small>
                </span>
              </div>
            ))}
            <div className="person ai-coworker-person">
              <span className="presence on" />
              <span className="mini-avatar mini-avatar--agent">{agentInitials}</span>
              <span className="person-main">
                <b>{agentName}</b>
                <small>{wakeWord ? `AI coworker · wake: ${wakeWord}` : 'AI coworker · always online'}</small>
              </span>
            </div>
            {speakerSummary && (
              <div
                className={`team-speaker-summary ${speakerSummary.tone}`}
                aria-label={`Speaker identity: ${speakerSummary.label}, ${speakerSummary.detail}`}
              >
                <span><SpeakerSummaryIcon size={13} /> {speakerSummary.label}</span>
                <small>{speakerSummary.detail}</small>
              </div>
            )}
          </div>
        )}
        <LeftRailDetails title="Team settings" meta={settingsMeta} open={shouldOpenTeamSettings}>
          <AgentSettingsBlock
            settings={agentSettings}
            saving={agentSettingsSaving}
            error={agentSettingsError}
            onSave={onSaveAgentSettings}
          />
          <SpeakerBlock
            speakers={speakers}
            speakerValidation={speakerValidation}
            participants={people}
            speakerError={speakerError}
            speakerSaving={speakerSaving}
            onMapSpeaker={onMapSpeaker}
          />
          {(integrations || []).length > 0 && (
            <div className="integration-list">
              {(integrations || []).map((it) => {
                const Icon = INTEG_ICON[it.id] || PlugsConnected
                return (
                  <div className={`integ${it.connected ? '' : ' off'}`} key={it.id} title={it.detail || ''}>
                    <Icon size={15} color="var(--ink-faint)" />
                    {integrationDisplayLabel(it)}
                    <span className="ind" />
                  </div>
                )
              })}
            </div>
          )}
        </LeftRailDetails>
      </div>
    </aside>
  )
}
