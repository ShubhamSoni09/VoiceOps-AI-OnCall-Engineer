export const IDLE_STEPS = [
  { key: 'investigate', label: 'Investigate', state: '' },
  { key: 'diagnose', label: 'Diagnose', state: '' },
  { key: 'patch', label: 'Patch', state: '' },
  { key: 'test', label: 'Test', state: '' },
  { key: 'pr', label: 'PR', state: '' },
  { key: 'deploy', label: 'Deploy', state: '' },
  { key: 'verify', label: 'Verify', state: '' },
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
}
