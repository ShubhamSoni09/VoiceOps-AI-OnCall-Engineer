export function workspaceTitle(workspace) {
  const workspaceName = workspace?.name || 'workspace'
  const rawTitle = workspace?.readme_line?.replace(/^#+\s*/, '').trim()
  if (!rawTitle) return `${workspaceName} workspace`
  return rawTitle
    .replace(/\bAI\s+On-Call\s+Engineer\b/gi, 'AI Teammate Console')
    .replace(/\bOn-Call\s+Console\b/gi, 'Team Console')
    .replace(/\bIncident\s+Console\b/gi, 'Team Console')
}
