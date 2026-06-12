// Mock data for the VoiceOps incident console prototype.
// Realistic, messy values on purpose (no John Doe / Acme / round numbers).

export const SAMPLE_UTTERANCE = 'Deploy the preview for PR 482 and watch the error rate.'

export const INCIDENTS = [
  { id: 'checkout', sev: 'act',  title: '5xx spike on checkout API',        svc: 'checkout-api',    tag: 'SEV1',     age: '6m',  live: true },
  { id: 'edge',     sev: 'crit', title: 'p99 latency breach, edge gateway', svc: 'edge-gateway',    tag: 'SEV2',     age: '23m' },
  { id: 'payments', sev: 'ok',   title: 'Retry storm, payments worker',     svc: 'payments-worker', tag: 'resolved', age: '2h' },
]

export const INTEGRATIONS = [
  { id: 'github',     label: 'GitHub',            on: true },
  { id: 'render',     label: 'Render',            on: true },
  { id: 'clickhouse', label: 'ClickHouse',        on: true },
  { id: 'slack',      label: 'Slack / PagerDuty', on: true },
]

// pr / deploy / verify carry their own glyph; done steps render a check.
export const INITIAL_STEPS = [
  { key: 'investigate', label: 'Investigate', state: 'done' },
  { key: 'diagnose',    label: 'Diagnose',    state: 'done' },
  { key: 'patch',       label: 'Patch',       state: 'done' },
  { key: 'test',        label: 'Test',        state: 'done' },
  { key: 'pr',          label: 'PR',          state: 'active' },
  { key: 'deploy',      label: 'Deploy',      state: '' },
  { key: 'verify',      label: 'Verify',      state: '' },
]

export const METRICS = [
  { k: 'Error rate',    v: '4.7',   unit: '%',      tone: 'bad',  delta: '▲ from 0.08%', dtone: 'up' },
  { k: 'p99 latency',   v: '1,840', unit: 'ms',     tone: 'warn', delta: '▲ 6.1×',  dtone: 'up' },
  { k: 'Throughput',    v: '312',   unit: 'req/s',  tone: '',     delta: 'stable',            dtone: '' },
  { k: 'Error budget',  v: '87.3',  unit: '%',      tone: '',     delta: '▼ 9.4% today', dtone: 'down' },
]

export const ARTIFACTS = [
  { id: 'pr',      icon: 'pr',   title: 'PR #482 · pool exhaustion fix', sub: '+3 -1 · checks passed' },
  { id: 'logs',    icon: 'logs', title: 'Error logs · 187 lines',        sub: 'ClickHouse · last 5 min' },
  { id: 'runbook', icon: 'book', title: 'Runbook · DB pool exhaustion',  sub: 'matched 91% · last used Apr 3' },
]

// Canned agent replies for spoken queries (regex matched on the utterance).
export function agentReplyFor(q) {
  if (/deploy|preview|ship|482/i.test(q)) {
    return 'Understood. The deploy is queued and waiting on the approval card to your right. Approve it and I will run the health checks and report back.'
  }
  if (/roll ?back|revert/i.test(q)) {
    return 'I can roll back <b>edge-gateway</b> to the previous green build. That needs an approval too, prepping the action now.'
  }
  return 'Looking into that now, pulling the relevant logs and metrics through Composio.'
}
