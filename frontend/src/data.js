export const IDLE_STEPS = [
  { key: 'capture', label: 'Capture', state: '' },
  { key: 'memory', label: 'Memory', state: '' },
  { key: 'proposal', label: 'Proposal', state: '' },
  { key: 'approval', label: 'Approval', state: '' },
  { key: 'test', label: 'Tests', state: '' },
  { key: 'branch', label: 'Branch', state: '' },
  { key: 'handoff', label: 'Handoff', state: '' },
]

export const ACTION_INDEX = {
  investigate: 0,
  diagnose: 1,
  patch: 2,
  test: 3,
  create_pr: 4,
  deploy: 5,
  verify: 6,
  rollback: 5,
  status: 0,
  unknown: 0,
  explain_code: 1,
  find_bug: 1,
  summarize_changes: 6,
  git_status: 5,
  run_tests: 4,
}
