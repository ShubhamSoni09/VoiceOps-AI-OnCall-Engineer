export function speakerValidationSummary(validation) {
  if (!validation) return null
  if (validation.ready) {
    return {
      tone: 'ready',
      label: 'Ready',
      detail: validation.verification_count
        ? `${validation.verification_count} verified labels`
        : `${validation.mapped_speaker_count || 0} mapped labels`,
    }
  }
  if (validation.unknown_speaker_count) {
    return {
      tone: 'attention',
      label: 'Map speakers',
      detail: `${validation.unknown_speaker_count} unknown label${validation.unknown_speaker_count === 1 ? '' : 's'}`,
    }
  }
  return {
    tone: 'attention',
    label: 'Verify',
    detail: validation.verification_status?.replaceAll('_', ' ') || 'verification needed',
  }
}

export function teamSpeakerSettingsMeta(people = [], validation) {
  const teammateText = people.length ? `${people.length} teammate${people.length === 1 ? '' : 's'}` : null
  let speakerText = null
  if (validation?.unknown_speaker_count) {
    speakerText = 'map speakers'
  } else if (Number(validation?.verification_count || 0) > 0) {
    const count = Number(validation.verification_count)
    speakerText = `${count} verified`
  } else if (Number(validation?.mapped_speaker_count || 0) > 0) {
    const count = Number(validation.mapped_speaker_count)
    speakerText = `${count} mapped`
  } else if (validation?.ready) {
    speakerText = 'speakers ready'
  } else {
    speakerText = speakerValidationSummary(validation)?.label?.toLowerCase() || null
  }
  return [teammateText, speakerText].filter(Boolean).join(' · ') || 'agent and speakers'
}

export function teamRoomCompactText(people = []) {
  return `${people.length} teammate${people.length === 1 ? '' : 's'}`
}

export function teamRoomSummaryLabel(people = []) {
  const onlineCount = people.filter((person) => person.online).length
  return `Team room summary: ${teamRoomCompactText(people)}, ${onlineCount} online`
}

function workDashboardCountDetail(activity) {
  if (!activity?.hasActivity) return 'no tracked work yet'
  const parts = [
    activity.openQueue ? `${activity.openQueue} queued` : null,
    activity.approvals ? `${activity.approvals} approval${activity.approvals === 1 ? '' : 's'}` : null,
    activity.openItems ? `${activity.openItems} open item${activity.openItems === 1 ? '' : 's'}` : null,
  ].filter(Boolean)
  return parts.join(' · ') || `${activity.actionCount} action${activity.actionCount === 1 ? '' : 's'}`
}

export function workDashboardLeftRailStatus(activity) {
  const title = workDashboardCountDetail(activity)
  if (!activity?.hasActivity) {
    return { detail: 'no tracked work yet', title, ready: false }
  }
  if (activity.approvals) {
    return { detail: 'approval needed', title, ready: false }
  }
  if (activity.openItems) {
    return { detail: 'open items', title, ready: false }
  }
  if (activity.openQueue) {
    return { detail: 'activity active', title, ready: true }
  }
  return { detail: 'activity logged', title, ready: true }
}

export function sessionSetupDetail({ known, connected, hasActivity, roomState, dashboardSyncing } = {}) {
  if (!known) return 'Checking signed-in session, local workspace, and shared room.'
  if (!connected) return 'Text and live meeting work now. Connect a local repo for patch proposals.'
  if (roomState === 'issue') return 'Room data is unavailable. Check backend sync before approving work.'
  if (roomState === 'checking' || roomState === 'connecting' || roomState === 'syncing') {
    return 'Syncing timeline, approvals, memory, and handoff.'
  }
  if (dashboardSyncing) return 'Room timeline is ready. Work status is still syncing.'
  if (hasActivity) return 'Activity, approvals, and handoff are being tracked.'
  return 'Ask about the repo, start a live meeting, or assign focused work.'
}

export function feedEmptyStateCopy({ workspace, roomStatus, agentLabel = 'AI teammate' } = {}) {
  const workspaceKnown = typeof workspace?.connected === 'boolean'
  const roomState = String(roomStatus?.state || 'ready')
  if (!workspaceKnown) {
    return {
      title: 'Preparing session',
      text: 'Checking signed-in session, local workspace, and shared room.',
      steps: ['Verify session', 'Load workspace', 'Join room'],
    }
  }
  if (!workspace.connected) {
    return {
      title: 'Start with text or live meeting',
      text: `You can talk to ${agentLabel} now. Connect a local repo when patches need approval.`,
      steps: ['Type a command', 'Start live meeting', 'Connect local repo'],
    }
  }
  if (roomState === 'issue') {
    return {
      title: 'Room sync issue',
      text: roomStatus?.message || 'Timeline, memory, or approvals are unavailable.',
      steps: ['Check backend', 'Retry refresh', 'Keep approvals paused'],
    }
  }
  if (roomState === 'checking' || roomState === 'connecting' || roomState === 'syncing') {
    return {
      title: 'Joining team room',
      text: roomStatus?.message || 'Syncing timeline, approvals, memory, and handoff.',
      steps: ['Load timeline', 'Sync approvals', 'Prepare handoff'],
    }
  }
  if (roomStatus?.dashboardSyncing) {
    return {
      title: `Start the room with ${agentLabel}`,
      text: 'Timeline is ready. Work queue and dashboard status are still syncing.',
      steps: ['Ask what changed', 'Start live meeting', 'Queue work after sync'],
    }
  }
  return {
    title: `Start the room with ${agentLabel}`,
    text: 'Ask about the repo, start a live meeting, or request an approval-first patch.',
    steps: ['Ask what changed', 'Start live meeting', 'Request approval-first patch'],
  }
}
