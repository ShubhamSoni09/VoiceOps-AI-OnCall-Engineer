import { chromium, expect } from '@playwright/test'
import { spawn } from 'node:child_process'
import fs from 'node:fs/promises'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import process from 'node:process'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const projectRoot = path.resolve(__dirname, '../../..')
const frontendRoot = path.join(projectRoot, 'frontend')
const backendRoot = path.join(projectRoot, 'backend')
const args = new Set(process.argv.slice(2))
const asJson = args.has('--json')
const keepWorkspace = args.has('--keep-workspace')

class E2EFailure extends Error {
  constructor(step, message) {
    super(`${step}: ${message}`)
    this.step = step
  }
}

async function main() {
  const tempRoot = await fs.mkdtemp(path.join(os.tmpdir(), 'voiceops-agent-controls-e2e-'))
  const workspace = path.join(tempRoot, 'workspace')
  const logs = { backend: [], frontend: [] }
  const processes = []
  let browser

  try {
    await writeWorkspace(workspace)
    const apiPort = await freePort()
    const webPort = await freePort()
    const apiUrl = `http://127.0.0.1:${apiPort}`
    const webUrl = `http://127.0.0.1:${webPort}`

    processes.push(startBackend(apiPort, tempRoot, workspace, logs.backend))
    await waitForHttp(`${apiUrl}/health`, 'backend health')
    processes.push(startFrontend(webPort, apiUrl, logs.frontend))
    await waitForHttp(webUrl, 'frontend dev server')

    browser = await chromium.launch({ headless: !process.env.VOICEOPS_E2E_HEADED })
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
    await page.goto(`${webUrl}/react?agent_controls_e2e=${Date.now()}`)
    await page.getByRole('button', { name: /^sign in$/i }).click()
    await expect(page.getByRole('heading', { name: 'Team room' })).toBeVisible({ timeout: 30_000 })

    await configureExternalAgentFromUi(page)
    const assignmentReport = await createExternalAssignmentFromUi(page)

    const started = await page.evaluate(async () => {
      const token = localStorage.getItem('voiceops_token')
      const response = await fetch('/agents/rooms/main/runs', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          prompt: 'what did we decide?',
          source: 'agent-controls-e2e',
          background: true,
          timeout_seconds: 20,
          max_steps: 20,
          metadata: { debug_delay_seconds: 5 },
        }),
      })
      return response.json()
    })
    if (started.status !== 'running') {
      throw new E2EFailure('start agent run', `expected running, got ${started.status}`)
    }

    const agentPanel = page.getByTestId('agent-team')
    await expect(agentPanel).toContainText('running', { timeout: 10_000 })
    await expect(agentPanel).toContainText('budget 0/20')
    await expect(agentPanel).toContainText('timeout 20s')
    await agentPanel.getByRole('button', { name: /cancel/i }).click()
    await expect(agentPanel).toContainText('cancelled', { timeout: 15_000 })

    const report = await page.evaluate(async ({ runId, assignment }) => {
      const token = localStorage.getItem('voiceops_token')
      const headers = { Authorization: `Bearer ${token}` }
      const response = await fetch(`/agents/rooms/main/runs/${runId}`, { headers })
      const run = await response.json()
      const layout = {
        desktopOverflowX: document.documentElement.scrollWidth > document.documentElement.clientWidth,
      }
      return {
        runId: run.id,
        status: run.status,
        summary: run.summary,
        controlResult: run.metadata?.control_result || {},
        findingTitle: run.findings?.[run.findings.length - 1]?.title || '',
        assignment,
        layout,
      }
    }, { runId: started.id, assignment: assignmentReport })

    await page.setViewportSize({ width: 390, height: 844 })
    await page.waitForTimeout(250)
    Object.assign(report.layout, await page.evaluate(() => {
      const rightRail = document.querySelector('.rail--right')
      const rect = rightRail?.getBoundingClientRect()
      const visibleText = rightRail?.textContent || ''
      return {
        mobileOverflowX: document.documentElement.scrollWidth > document.documentElement.clientWidth,
        mobileRightRailVisible: Boolean(
          rightRail
          && rect
          && rect.width > 0
          && rect.height > 0
          && getComputedStyle(rightRail).display !== 'none'
        ),
        mobileRightRailWidth: Math.round(rect?.width || 0),
        mobileRightRailHeight: Math.round(rect?.height || 0),
        mobileHasWorkDashboard: visibleText.includes('Work dashboard'),
        mobileHasAgentActions: visibleText.includes('Agent actions'),
        mobileHasMeetingMemory: visibleText.includes('Meeting memory'),
        mobileWorkDashboardCoveredByStepper: (() => {
          const label = Array.from(document.querySelectorAll('.section-lab'))
            .find((node) => /work dashboard/i.test(node.textContent || ''))
          const labelRect = label?.getBoundingClientRect()
          if (!labelRect) return false
          const topElement = document.elementFromPoint(
            Math.min(labelRect.left + 12, document.documentElement.clientWidth - 2),
            Math.min(labelRect.top + labelRect.height + 18, document.documentElement.clientHeight - 2),
          )
          return Boolean(topElement?.closest?.('.stepper, .step, .node'))
        })(),
      }
    }))

    assertReport(report, started.id)
    printReport({ ...report, webUrl, workspace }, asJson)
  } catch (error) {
    await printFailure(error, logs)
    process.exitCode = 1
  } finally {
    if (browser) await browser.close().catch(() => {})
    await Promise.all(processes.map((child) => stopProcess(child)))
    if (!keepWorkspace) await fs.rm(tempRoot, { recursive: true, force: true })
  }
}

function assertReport(report, expectedRunId) {
  if (report.runId !== expectedRunId) throw new E2EFailure('assert report', `unexpected run ${report.runId}`)
  if (report.status !== 'cancelled') throw new E2EFailure('assert report', `expected cancelled, got ${report.status}`)
  if (report.controlResult.cancelled_by_name !== 'Priya Nair') {
    throw new E2EFailure('assert report', `missing canceller metadata: ${JSON.stringify(report.controlResult)}`)
  }
  if (report.findingTitle !== 'Run cancelled') {
    throw new E2EFailure('assert report', `missing cancellation finding: ${report.findingTitle}`)
  }
  if (report.assignment?.agentId !== 'claude') {
    throw new E2EFailure('assert report', `missing external assignment: ${JSON.stringify(report.assignment)}`)
  }
  if (report.assignment?.model !== 'claude-opus-4-8') {
    throw new E2EFailure('assert report', `expected selected model, got ${report.assignment?.model || 'empty'}`)
  }
  if (!report.assignment?.providerCommandTemplate?.includes('{workspace}')) {
    throw new E2EFailure('assert report', `missing CLI template metadata: ${JSON.stringify(report.assignment)}`)
  }
  if (report.layout.desktopOverflowX || report.layout.mobileOverflowX) {
    throw new E2EFailure('assert layout', `horizontal overflow: ${JSON.stringify(report.layout)}`)
  }
  if (
    !report.layout.mobileRightRailVisible
    || !report.layout.mobileHasWorkDashboard
    || !report.layout.mobileHasAgentActions
    || !report.layout.mobileHasMeetingMemory
    || report.layout.mobileWorkDashboardCoveredByStepper
  ) {
    throw new E2EFailure('assert layout', `mobile work console is not reachable: ${JSON.stringify(report.layout)}`)
  }
}

function printReport(report, json) {
  if (json) {
    console.log(JSON.stringify(report, null, 2))
    return
  }
  console.log('Agent controls UI E2E passed')
  console.log(`URL: ${report.webUrl}/react?agent_controls_e2e=1`)
  console.log(`Workspace: ${report.workspace}`)
  console.log(`Run: ${report.runId}`)
  console.log(`Status: ${report.status}`)
  console.log(`Assignment: ${report.assignment?.agentId || 'none'} ${report.assignment?.model || ''}`)
}

async function configureExternalAgentFromUi(page) {
  await openAgentSetup(page)
  const externalRow = page.locator('.external-agent-row').filter({ hasText: 'Claude Code' }).first()
  await expect(externalRow).toBeVisible({ timeout: 30_000 })
  const runSettings = externalRow.locator('details.external-agent-run-settings').first()
  await expect(runSettings).toBeVisible({ timeout: 30_000 })
  if (!(await runSettings.evaluate((node) => node.open))) {
    await runSettings.locator(':scope > summary').click()
  }
  await externalRow.getByRole('button', { name: /^CLI setup$/i }).click()
  await externalRow.getByRole('textbox', { name: 'Claude Code CLI command', exact: true }).fill('python agent_cli.py')
  await externalRow
    .getByRole('textbox', { name: 'Claude Code CLI command template', exact: true })
    .fill('python agent_cli.py --workspace {workspace} --prompt {prompt} --model {model}')
  await externalRow.getByRole('button', { name: /^Save CLI$/i }).click()
  await expect(externalRow).toContainText('local cli', { timeout: 15_000 })
  await expect(externalRow).toContainText('python agent_cli.py')
}

async function openAgentSetup(page) {
  const setup = page.locator('details.rail-section-details', { hasText: 'Agent setup' })
  if (!(await setup.evaluate((node) => node.open))) {
    await setup.locator(':scope > summary').click()
  }
  const codingAgents = setup.locator('details.agent-setup-subdetails', { hasText: 'Coding agents' })
  await expect(codingAgents).toBeVisible({ timeout: 30_000 })
  if (!(await codingAgents.evaluate((node) => node.open))) {
    await codingAgents.locator(':scope > summary').click()
  }
  await expect(setup.locator('.external-agent-panel')).toBeVisible({ timeout: 30_000 })
}

async function createExternalAssignmentFromUi(page) {
  await openCreateAssignment(page)
  await page.getByLabel('Assignment agent', { exact: true }).selectOption('claude')
  await page.getByLabel('Assignment mode', { exact: true }).selectOption('review')
  await page.getByLabel('Assignment model', { exact: true }).selectOption('claude-opus-4-8')
  await page.getByLabel('Assignment task', { exact: true }).fill('Review app.py and summarize risk.')
  await page.getByRole('button', { name: /^Assign$/i }).click()

  const assignment = await page.evaluate(async () => {
    const token = localStorage.getItem('voiceops_token')
    const headers = { Authorization: `Bearer ${token}` }
    const [assignmentsResponse, providersResponse] = await Promise.all([
      fetch('/agents/rooms/main/assignments', { headers }),
      fetch('/external-agents/providers', { headers }),
    ])
    const assignmentsBody = await assignmentsResponse.json()
    const providers = await providersResponse.json()
    const created = (assignmentsBody.assignments || []).find((item) => (
      item.agent_id === 'claude' && item.metadata?.model === 'claude-opus-4-8'
    ))
    const claude = providers.find((provider) => provider.provider === 'claude')
    return {
      id: created?.id || '',
      agentId: created?.agent_id || '',
      model: created?.metadata?.model || '',
      status: created?.status || '',
      providerCommand: claude?.local_cli_command || '',
      providerCommandTemplate: claude?.local_cli_command_template || '',
    }
  })
  if (!assignment.id) throw new E2EFailure('create external assignment', 'assignment was not persisted')
  return assignment
}

async function openCreateAssignment(page) {
  const assignment = page.locator('details.workdash-assignment-details')
  await expect(assignment).toBeVisible({ timeout: 30_000 })
  if (!(await assignment.evaluate((node) => node.open))) {
    await assignment.locator('summary').click()
  }
  await expect(page.getByLabel('Assignment agent', { exact: true })).toBeVisible({ timeout: 10_000 })
}

async function printFailure(error, logs) {
  console.error(`Agent controls UI E2E failed: ${error.message || error}`)
  if (error.step) console.error(`Failed step: ${error.step}`)
  console.error('Backend log tail:')
  console.error(logs.backend.slice(-30).join('').trim() || '(empty)')
  console.error('Frontend log tail:')
  console.error(logs.frontend.slice(-30).join('').trim() || '(empty)')
}

async function writeWorkspace(workspace) {
  await fs.mkdir(workspace, { recursive: true })
  await fs.writeFile(path.join(workspace, 'app.py'), 'def health():\n    return "ok"\n', 'utf8')
  await fs.writeFile(
    path.join(workspace, 'agent_cli.py'),
    [
      'import argparse',
      'parser = argparse.ArgumentParser()',
      'parser.add_argument("--workspace")',
      'parser.add_argument("--prompt")',
      'parser.add_argument("--model")',
      'args = parser.parse_args()',
      'print(f"model={args.model}")',
      'print(f"prompt={args.prompt}")',
      '',
    ].join('\n'),
    'utf8',
  )
}

function startBackend(port, tempRoot, workspace, logBuffer) {
  const env = {
    ...process.env,
    USERS_STORE_PATH: path.join(tempRoot, 'users.json'),
    COLLAB_STORE_BACKEND: 'json',
    COLLAB_STORE_PATH: path.join(tempRoot, 'collab.json'),
    SPEAKER_STORE_BACKEND: 'json',
    SPEAKER_STORE_PATH: path.join(tempRoot, 'speakers.json'),
    AGENT_RUNS_PATH: path.join(tempRoot, 'agent-runs.json'),
    MEMORY_STORE_PATH: path.join(tempRoot, 'memory.json'),
    RAG_INDEX_PATH: path.join(tempRoot, 'rag-index.json'),
    VOICEOPS_CACHE_PATH: path.join(tempRoot, 'cache.json'),
    JWT_SECRET: 'ui-agent-controls-e2e-secret',
    VOICEOPS_WORKSPACE: workspace,
    LLM_PROVIDER: 'mock',
    TTS_PROVIDER: 'mock',
    STT_PROVIDER: 'mock',
    SPEAKER_PROVIDER: 'mock',
  }
  return spawn(
    process.env.PYTHON || 'python',
    ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(port)],
    { cwd: backendRoot, env, stdio: ['ignore', 'pipe', 'pipe'] },
  ).on('error', (error) => logBuffer.push(String(error)))
    .on('exit', (code) => logBuffer.push(`backend exited ${code}\n`))
    .on('spawn', function () {
      this.stdout.on('data', (chunk) => logBuffer.push(chunk.toString()))
      this.stderr.on('data', (chunk) => logBuffer.push(chunk.toString()))
    })
}

function startFrontend(port, apiUrl, logBuffer) {
  const child = spawn(
    'npm',
    ['run', 'dev', '--', '--host', '127.0.0.1', '--port', String(port)],
    {
      cwd: frontendRoot,
      env: { ...process.env, VITE_DEV_PORT: String(port), VITE_API_TARGET: apiUrl },
      stdio: ['ignore', 'pipe', 'pipe'],
    },
  )
  child.stdout.on('data', (chunk) => logBuffer.push(chunk.toString()))
  child.stderr.on('data', (chunk) => logBuffer.push(chunk.toString()))
  return child
}

async function waitForHttp(url, label) {
  const deadline = Date.now() + 30_000
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url)
      if (response.ok) return
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 250))
  }
  throw new E2EFailure(label, `timed out waiting for ${url}`)
}

function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer()
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      server.close(() => resolve(address.port))
    })
    server.on('error', reject)
  })
}

async function stopProcess(child) {
  if (!child || child.killed) return
  child.kill('SIGTERM')
  await new Promise((resolve) => setTimeout(resolve, 250))
  if (!child.killed) child.kill('SIGKILL')
}

main()
