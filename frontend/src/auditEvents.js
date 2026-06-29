const ACTION_TITLES = {
  patch: 'Patch proposal',
  test: 'Test run',
  verify: 'Verification gate',
  rollback: 'Rollback request',
  deploy: 'Deploy request',
  create_pr: 'PR request',
}

export function buildAuditEvents({ messages = [], actions = [] } = {}, limit = 8) {
  const events = [
    ...actionEvents(actions),
    ...messageEvents(messages),
  ]
    .filter(Boolean)
    .sort((a, b) => timestamp(b.at) - timestamp(a.at))
  return events.slice(0, limit)
}

function actionEvents(actions) {
  if (!Array.isArray(actions)) return []
  return actions
    .filter((action) => action && shouldShowAction(action))
    .map((action) => {
      const approval = action.approval || {}
      const git = approval.git || {}
      const status = action.status || (action.pending_approval ? 'pending_approval' : 'completed')
      const files = action.files_changed || git.files_changed || []
      const branch = git.branch_name || git.branch_created || ''
      const commit = git.commit_sha || ''
      return {
        id: `action:${action.id}:${status}:${commit || branch}`,
        kind: 'action',
        tone: toneForStatus(status),
        title: ACTION_TITLES[action.action] || `${action.action || 'Agent'} action`,
        status: labelStatus(status),
        detail: action.summary || 'Agent action recorded',
        actor: action.requested_by_name || '',
        at: action.updated_at || action.created_at || '',
        chips: [
          branch ? `branch ${branch}` : '',
          commit ? `commit ${commit}` : '',
          files.length ? `${files.length} file${files.length === 1 ? '' : 's'}` : '',
          approval.test_command ? `test ${approval.test_command}` : '',
        ].filter(Boolean),
      }
    })
}

function messageEvents(messages) {
  if (!Array.isArray(messages)) return []
  return messages
    .map((message) => {
      const metadata = message?.metadata || {}
      if (metadata.source === 'demo_gate_result') return demoGateResultEvent(message, metadata)
      if (metadata.source === 'demo_gate_run') return demoGateRunEvent(message, metadata)
      if (metadata.source === 'speaker_mapping' || String(metadata.event || '').startsWith('speaker_mapping_')) {
        return {
          id: `message:${message.id}`,
          kind: 'speaker',
          tone: 'info',
          title: 'Speaker mapping',
          status: mappingStatus(metadata.event),
          detail: speakerMappingDetail(metadata),
          actor: message.name || '',
          at: message.createdAt || '',
          chips: [
            metadata.speaker_label,
            metadata.mapped_user_name || metadata.identified_user_name || metadata.user_name,
            metadata.mapping_source,
          ].filter(Boolean),
        }
      }
      if (metadata.source === 'action_approval' || metadata.source === 'action_commit') {
        return {
          id: `message:${message.id}`,
          kind: 'action',
          tone: toneForStatus(metadata.status || metadata.source),
          title: metadata.source === 'action_commit' ? 'Git commit' : 'Approval decision',
          status: labelStatus(metadata.status || metadata.source),
          detail: message.text || 'Action audit recorded',
          actor: message.name || '',
          at: message.createdAt || '',
          chips: [metadata.action_id, metadata.commit_sha].filter(Boolean),
        }
      }
      return null
    })
    .filter(Boolean)
}

function shouldShowAction(action) {
  const status = action.status || ''
  return action.pending_approval || ['completed', 'failed', 'rejected'].includes(status) || action.approval?.git
}

function demoGateRunEvent(message, metadata) {
  return {
    id: `message:${message.id}`,
    kind: 'gate',
    tone: metadata.error ? 'warn' : 'info',
    title: 'Demo gate started',
    status: labelStatus(metadata.gate_status || 'running'),
    detail: message.text || metadata.gate_label || 'Demo gate started',
    actor: message.name || '',
    at: message.createdAt || '',
    chips: [metadata.gate_label, metadata.gate_job_id].filter(Boolean),
  }
}

function demoGateResultEvent(message, metadata) {
  const status = metadata.gate_status || metadata.evidence_status || 'finished'
  const passed = status === 'succeeded' || metadata.evidence_status === 'passed'
  const failed = status === 'failed' || metadata.evidence_status === 'failed'
  const labels = Array.isArray(metadata.speaker_labels) ? metadata.speaker_labels.filter(Boolean) : []
  return {
    id: `message:${message.id}`,
    kind: 'gate',
    tone: passed ? 'ok' : failed ? 'warn' : 'info',
    title: 'Demo gate result',
    status: passed ? 'passed' : failed ? 'failed' : labelStatus(status),
    detail: metadata.error || metadata.detail || message.text || 'Demo gate finished',
    actor: message.name || '',
    at: message.createdAt || '',
    chips: [
      metadata.gate_label,
      metadata.gate_job_id,
      labels.length ? `${labels.length} labels` : '',
      chunkChip(metadata),
    ].filter(Boolean),
  }
}

function chunkChip(metadata) {
  const completed = Number(metadata.completed_chunks || 0)
  const requested = Number(metadata.requested_chunks || 0)
  if (completed > 0 && requested > 0) return `${completed}/${requested} chunks`
  if (completed > 0) return `${completed} chunks`
  return ''
}

function mappingStatus(event) {
  if (event === 'speaker_mapping_corrected') return 'corrected'
  if (event === 'speaker_mapping_confirmed') return 'confirmed'
  return 'mapped'
}

function speakerMappingDetail(metadata) {
  const label = metadata.speaker_label || 'speaker'
  const user = metadata.mapped_user_name || metadata.identified_user_name || metadata.user_name || 'teammate'
  const previous = metadata.previous_user_name
  if (metadata.event === 'speaker_mapping_corrected' && previous) {
    return `${label} reassigned from ${previous} to ${user}`
  }
  if (metadata.event === 'speaker_mapping_confirmed') {
    return `${label} confirmed as ${user}`
  }
  return `${label} mapped to ${user}`
}

function toneForStatus(status) {
  if (['completed', 'succeeded', 'approved', 'passed', 'action_commit'].includes(status)) return 'ok'
  if (['failed', 'rejected'].includes(status)) return 'warn'
  if (status === 'pending_approval') return 'accent'
  return 'info'
}

function labelStatus(status) {
  return String(status || 'recorded').replace(/_/g, ' ')
}

function timestamp(value) {
  const ms = Date.parse(value || '')
  return Number.isFinite(ms) ? ms : 0
}
