import { compactDisplayText } from './displayText.js'

export function actionStatusLabel(action = {}) {
  if (action.status === 'pending_approval') return 'awaiting approval'
  if (action.status === 'completed') return 'completed'
  if (action.status === 'failed') return 'failed'
  if (action.status === 'rejected') return 'rejected'
  return action.status || 'logged'
}

function actionRequesterLabel(action = {}) {
  return action.requested_by_name || action.requested_by || 'Unknown requester'
}

export function actionRequesterDisplay(action = {}) {
  const requester = actionRequesterLabel(action)
  return requester === 'Unknown requester' ? requester : `Requested by ${requester}`
}

export function actionSummaryRaw(action = {}) {
  return action.summary || action.result || 'No action summary provided.'
}

function actionFileCountLabel(count, verb = 'changed') {
  if (!count) return ''
  return `${count} file${count === 1 ? '' : 's'} ${verb}`
}

export function actionSummaryLabel(action = {}) {
  const approval = action.approval || {}
  const pending = action.status === 'pending_approval' || action.pending_approval
  const files = Array.isArray(action.files_changed) && action.files_changed.length
    ? action.files_changed
    : Array.isArray(approval.proposed_files)
      ? approval.proposed_files
      : []
  const facts = []

  if (files.length) facts.push(actionFileCountLabel(files.length, pending ? 'proposed' : 'changed'))
  else if (action.status === 'completed' && ['status', 'git_status'].includes(action.action)) {
    facts.push(action.action === 'git_status' ? 'Git checked' : 'Status checked')
  }
  if (pending) facts.push('waiting for approval')
  else if (action.status === 'completed' && (approval.test_command || /tests?\s+pass/i.test(actionSummaryRaw(action)))) {
    facts.push('tests passed')
  } else if (action.status === 'failed' || approval.status === 'failed') {
    facts.push('tests failed')
  } else if (action.status === 'rejected' || approval.status === 'rejected') {
    facts.push('rejected')
  }

  if (facts.length) return facts.join(' · ')
  return compactDisplayText(actionSummaryRaw(action))
}

export function actionTypeLabel(action = {}) {
  const value = String(action?.action || '').trim()
  const labels = {
    status: 'Status check',
    patch: 'Patch proposal',
    test: 'Test run',
    run_tests: 'Test run',
    explain_code: 'Code explanation',
    find_bug: 'Bug review',
    summarize_changes: 'Change summary',
    git_status: 'Git status',
  }
  if (labels[value]) return labels[value]
  return value ? value.replace(/_/g, ' ') : 'Agent action'
}

export function gitStatusAfterLabel(git = {}) {
  if (!Array.isArray(git.status_after) || git.status_after.length === 0) return ''
  const count = git.status_after.length
  return `${count} changed after approval`
}

export function actionLifecycleFacts(action = {}) {
  const approval = action.approval || {}
  const pending = action.status === 'pending_approval' || action.pending_approval
  const files = Array.isArray(action.files_changed) && action.files_changed.length
    ? action.files_changed
    : Array.isArray(approval.proposed_files)
      ? approval.proposed_files
      : []
  const fileLabel = files.length
    ? `${files.length} file${files.length === 1 ? '' : 's'} ${pending ? 'proposed' : 'changed'}`
    : ''
  const decider = approval.decided_by_name || approval.approved_by_name || approval.rejected_by_name || ''
  const facts = []

  if (pending) {
    facts.push({ key: 'pending', tone: 'pending', label: 'Waiting for approval' })
    if (fileLabel) facts.push({ key: 'files', tone: 'neutral', label: fileLabel })
    if (approval.test_command) facts.push({ key: 'tests', tone: 'neutral', label: `Will run ${approval.test_command}` })
    return facts
  }

  if (action.status === 'rejected' || approval.status === 'rejected') {
    facts.push({ key: 'rejected', tone: 'rejected', label: decider ? `Rejected by ${decider}` : 'Rejected' })
    if (fileLabel) facts.push({ key: 'files', tone: 'neutral', label: fileLabel })
    return facts
  }

  if (action.status === 'failed' || approval.status === 'failed') {
    facts.push({ key: 'approved', tone: 'neutral', label: decider ? `Approved by ${decider}` : 'Approved' })
    facts.push({ key: 'tests', tone: 'failed', label: approval.test_command ? `Tests failed · ${approval.test_command}` : 'Tests failed' })
    if (fileLabel) facts.push({ key: 'files', tone: 'neutral', label: fileLabel })
    return facts
  }

  if (action.status === 'completed' && (approval.status === 'approved' || approval.test_command || action.action === 'patch')) {
    facts.push({ key: 'approved', tone: 'approved', label: decider ? `Approved by ${decider}` : 'Approved' })
    if (approval.test_command || action.action === 'patch') {
      facts.push({ key: 'tests', tone: 'passed', label: approval.test_command ? `Tests passed · ${approval.test_command}` : 'Tests passed' })
    }
    if (fileLabel) facts.push({ key: 'files', tone: 'neutral', label: fileLabel })
  }

  return facts
}

export function pendingApprovalBrief(action = {}) {
  const approval = action.approval || {}
  const pending = action.status === 'pending_approval' || action.pending_approval
  if (!pending || action.action !== 'patch') return ''
  const testText = approval.test_command
    ? `then runs ${approval.test_command}`
    : 'then runs configured checks'
  return `Approve creates a local branch, applies this diff, ${testText}. Reject leaves files unchanged.`
}

function actionCompactKey(action = {}) {
  const trace = action.approval?.route_trace || {}
  return [
    action.status || '',
    action.action || '',
    action.requested_by_name || '',
    action.summary || '',
    trace.route || '',
    trace.reason || '',
    (action.files_changed || []).join(','),
  ].join('|')
}

export function compactRecentActions(actions, limit = 5, historyLimit = 2) {
  const compacted = []
  ;(actions || []).slice().reverse().forEach((action) => {
    const keepSeparate = action.status === 'pending_approval' || action.pending_approval
    const compactKey = actionCompactKey(action)
    const existing = keepSeparate ? null : compacted.find((item) => item.compactKey === compactKey)
    if (existing) {
      existing.duplicateCount += 1
      existing.duplicateIds.push(action.id)
      return
    }
    compacted.push({
      ...action,
      compactKey,
      duplicateCount: 1,
      duplicateIds: [action.id],
    })
  })
  const pending = compacted.filter((action) => action.status === 'pending_approval' || action.pending_approval)
  const history = compacted.filter((action) => !(action.status === 'pending_approval' || action.pending_approval))
  if (pending.length >= limit) return pending
  return [
    ...pending,
    ...history.slice(0, Math.min(historyLimit, limit - pending.length)),
  ]
}
