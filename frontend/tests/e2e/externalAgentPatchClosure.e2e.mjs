import { chromium, expect } from '@playwright/test'
import { execFileSync, spawn } from 'node:child_process'
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
  const tempRoot = await fs.mkdtemp(path.join(os.tmpdir(), 'voiceops-external-patch-e2e-'))
  const workspace = path.join(tempRoot, 'workspace')
  const logs = { backend: [], frontend: [] }
  const processes = []
  let browser

  try {
    await writeWorkspace(workspace)
    initGit(workspace)
    const originalApp = await fs.readFile(path.join(workspace, 'app.py'), 'utf8')

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
    await page.goto(`${webUrl}/react?external_patch_closure_e2e=${Date.now()}`)
    await page.getByRole('button', { name: /^sign in$/i }).click()
    await expect(page.getByRole('heading', { name: 'Team room' })).toBeVisible({ timeout: 30_000 })

    await configureExternalAgentFromUi(page)
    const assignment = await createPatchAssignmentFromUi(page)
    await dispatchAssignmentFromUi(page, assignment.id)

    const pendingAction = page.getByTestId('agent-action-pending_approval').filter({ hasText: 'patch' }).first()
    await expect(pendingAction).toContainText('awaiting approval', { timeout: 45_000 })
    await expect(pendingAction).toContainText('Claude Code')
    await expect(pendingAction).toContainText('Diff preview')
    await expect(pendingAction).toContainText('/health')

    const appAfterDispatch = await fs.readFile(path.join(workspace, 'app.py'), 'utf8')
    if (appAfterDispatch !== originalApp) {
      throw new E2EFailure('preview first', 'workspace app.py changed before approval')
    }

    await pendingAction.getByRole('button', { name: /^Approve$/i }).click()
    const completedAction = page.getByTestId('agent-action-completed').filter({ hasText: 'patch' }).first()
    await expect(completedAction).toContainText('completed', { timeout: 60_000 })
    await expect(completedAction).toContainText(/voiceops\/act-/)
    await expect(completedAction).toContainText('Verification python -m pytest -q')

    const appAfterApproval = await fs.readFile(path.join(workspace, 'app.py'), 'utf8')
    const report = await page.evaluate(async () => {
      const token = localStorage.getItem('voiceops_token')
      const headers = token ? { Authorization: `Bearer ${token}` } : {}
      const [roomResponse, gitResponse, handoffResponse, auditResponse] = await Promise.all([
        fetch('/collab/rooms/main', { headers }),
        fetch('/workspace/git/status', { headers }),
        fetch('/collab/rooms/main/handoff', { headers }),
        fetch('/collab/rooms/main/audit?kind=action&limit=10', { headers }),
      ])
      const room = await roomResponse.json()
      const git = await gitResponse.json()
      const handoff = await handoffResponse.json()
      const audit = await auditResponse.json()
      const completed = [...(room.actions || [])]
        .reverse()
        .find((action) => action.action === 'patch' && action.status === 'completed')
      const actionAudit = (audit || []).find((event) => event.source_id === completed?.id)
      return {
        actionId: completed?.id || '',
        actionStatus: completed?.status || '',
        requester: completed?.requested_by_name || '',
        summary: completed?.summary || '',
        branch: completed?.approval?.git?.branch_name || '',
        previousBranch: completed?.approval?.git?.previous_branch || '',
        filesChanged: completed?.approval?.git?.files_changed || completed?.files_changed || [],
        commandOutput: completed?.command_output || '',
        externalAgent: completed?.approval?.external_agent || {},
        runtime: completed?.approval?.runtime || {},
        gitDirty: git.dirty,
        gitFiles: git.files || [],
        handoffLines: handoff.lines || [],
        reviewItems: handoff.open_review_items || [],
        auditTitle: actionAudit?.title || '',
        auditStatus: actionAudit?.status || '',
        auditChips: actionAudit?.chips || [],
        layout: {
          desktopOverflowX: document.documentElement.scrollWidth > document.documentElement.clientWidth,
        },
      }
    })

    await page.setViewportSize({ width: 390, height: 844 })
    await page.waitForTimeout(250)
    report.layout.mobileOverflowX = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
    )

    assertReport(report, {
      originalApp,
      appAfterApproval,
      workspace,
    })
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
  const setup = page.locator('details.rail-section-details', { hasText: 'Agent setup' }).first()
  await expect(setup).toBeVisible({ timeout: 30_000 })
  if (!(await setup.evaluate((node) => node.open))) {
    await setup.locator(':scope > summary').click()
  }
  const codingAgents = setup.locator('details.agent-setup-subdetails', { hasText: 'Coding agents' }).first()
  await expect(codingAgents).toBeVisible({ timeout: 30_000 })
  if (!(await codingAgents.evaluate((node) => node.open))) {
    await codingAgents.locator(':scope > summary').click()
  }
  await expect(setup.locator('.external-agent-panel')).toBeVisible({ timeout: 30_000 })
}

async function createPatchAssignmentFromUi(page) {
  await openCreateAssignment(page)
  await page.getByLabel('Assignment agent', { exact: true }).selectOption('claude')
  await page.getByLabel('Assignment mode', { exact: true }).selectOption('patch')
  await page.getByLabel('Assignment model', { exact: true }).selectOption('claude-opus-4-8')
  await page.getByLabel('Assignment task', { exact: true }).fill('Add a health endpoint to app.py.')
  await page.getByRole('button', { name: /^Assign$/i }).click()

  const assignment = await page.evaluate(async () => {
    const token = localStorage.getItem('voiceops_token')
    const response = await fetch('/agents/rooms/main/assignments', {
      headers: { Authorization: `Bearer ${token}` },
    })
    const body = await response.json()
    const item = (body.assignments || []).find((candidate) => (
      candidate.agent_id === 'claude'
      && candidate.mode === 'patch'
      && candidate.metadata?.model === 'claude-opus-4-8'
    ))
    return {
      id: item?.id || '',
      status: item?.status || '',
      model: item?.metadata?.model || '',
    }
  })
  if (!assignment.id) throw new E2EFailure('create patch assignment', 'assignment was not persisted')
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

async function dispatchAssignmentFromUi(page, assignmentId) {
  const queueDetails = page.locator('details.workdash-queue-details').first()
  await expect(queueDetails).toBeVisible({ timeout: 30_000 })
  if (!(await queueDetails.evaluate((node) => node.open))) {
    await queueDetails.locator(':scope > summary').click()
  }
  const assignmentRow = page.locator('.workdash-item').filter({ hasText: assignmentId }).first()
  const fallbackRow = page.locator('.workdash-item').filter({ hasText: 'Add a health endpoint to app.py.' }).first()
  const row = await assignmentRow.count() ? assignmentRow : fallbackRow
  await expect(row).toContainText('queued', { timeout: 15_000 })
  await row.getByRole('button', { name: /^Run$/i }).click()
  await expect(row).toContainText('completed', { timeout: 45_000 })
}

function assertReport(report, { originalApp, appAfterApproval, workspace }) {
  if (!report.actionId) throw new E2EFailure('assert report', 'missing completed patch action')
  if (report.actionStatus !== 'completed') {
    throw new E2EFailure('assert report', `expected completed action, got ${report.actionStatus || 'empty'}`)
  }
  if (!report.branch.startsWith(`voiceops/${report.actionId}-`)) {
    throw new E2EFailure('assert report', `unexpected branch ${report.branch}`)
  }
  if (report.externalAgent.provider !== 'claude') {
    throw new E2EFailure('assert report', `missing external agent provider: ${JSON.stringify(report.externalAgent)}`)
  }
  if (report.externalAgent.model !== 'claude-opus-4-8') {
    throw new E2EFailure('assert report', `missing selected model: ${JSON.stringify(report.externalAgent)}`)
  }
  if (report.runtime.execution_mode !== 'local_cli_patch') {
    throw new E2EFailure('assert report', `expected local CLI patch runtime, got ${report.runtime.execution_mode || 'empty'}`)
  }
  if (!report.filesChanged.includes('app.py')) {
    throw new E2EFailure('assert report', `app.py missing from changed files: ${report.filesChanged.join(', ')}`)
  }
  if (appAfterApproval === originalApp || !appAfterApproval.includes('@app.get("/health")')) {
    throw new E2EFailure('assert workspace', `approved patch was not applied in ${workspace}`)
  }
  if (!/passed/i.test(report.commandOutput)) {
    throw new E2EFailure('assert report', 'approval tests did not pass')
  }
  if (!report.gitFiles.some((file) => file.path === 'app.py')) {
    throw new E2EFailure('assert report', `git status missing app.py: ${JSON.stringify(report.gitFiles)}`)
  }
  if (!report.auditChips.some((chip) => chip.startsWith('branch voiceops/'))) {
    throw new E2EFailure('assert report', `audit missing branch chip: ${report.auditChips.join(', ')}`)
  }
  if (report.layout.desktopOverflowX || report.layout.mobileOverflowX) {
    throw new E2EFailure('assert layout', `horizontal overflow: ${JSON.stringify(report.layout)}`)
  }
}

function printReport(report, json) {
  if (json) {
    console.log(JSON.stringify(report, null, 2))
    return
  }
  console.log('External agent patch closure UI E2E passed')
  console.log(`URL: ${report.webUrl}/react?external_patch_closure_e2e=1`)
  console.log(`Workspace: ${report.workspace}`)
  console.log(`Action: ${report.actionId}`)
  console.log(`Branch: ${report.branch}`)
  console.log(`Provider: ${report.externalAgent.provider}`)
  console.log(`Model: ${report.externalAgent.model}`)
}

async function printFailure(error, logs) {
  console.error(`External agent patch closure UI E2E failed: ${error.message || error}`)
  if (error.step) console.error(`Failed step: ${error.step}`)
  console.error('Backend log tail:')
  console.error(logs.backend.slice(-40).join('').trim() || '(empty)')
  console.error('Frontend log tail:')
  console.error(logs.frontend.slice(-40).join('').trim() || '(empty)')
}

async function writeWorkspace(workspace) {
  await fs.mkdir(path.join(workspace, 'tests'), { recursive: true })
  await fs.writeFile(
    path.join(workspace, 'app.py'),
    [
      'from fastapi import FastAPI',
      '',
      'app = FastAPI()',
      '',
      '@app.get("/")',
      'def root():',
      '    return {"service": "external-agent-closure"}',
      '',
    ].join('\n'),
    'utf8',
  )
  await fs.writeFile(
    path.join(workspace, 'tests', 'test_app.py'),
    [
      'from fastapi.testclient import TestClient',
      'from app import app',
      '',
      'client = TestClient(app)',
      '',
      'def test_health():',
      '    response = client.get("/health")',
      '    assert response.status_code == 200',
      '    assert response.json()["status"] == "ok"',
      '',
    ].join('\n'),
    'utf8',
  )
  await fs.writeFile(
    path.join(workspace, 'agent_cli.py'),
    [
      'import argparse',
      'from pathlib import Path',
      '',
      'parser = argparse.ArgumentParser()',
      'parser.add_argument("--workspace", required=True)',
      'parser.add_argument("--prompt")',
      'parser.add_argument("--model")',
      'args = parser.parse_args()',
      '',
      'target = Path(args.workspace) / "app.py"',
      'target.write_text("""from fastapi import FastAPI',
      '',
      'app = FastAPI()',
      '',
      '@app.get("/")',
      'def root():',
      '    return {"service": "external-agent-closure"}',
      '',
      '@app.get("/health")',
      'def health():',
      '    return {"status": "ok"}',
      '""", encoding="utf-8")',
      'print(f"patched health endpoint with {args.model}")',
      '',
    ].join('\n'),
    'utf8',
  )
  await fs.writeFile(path.join(workspace, '.gitignore'), '__pycache__/\n.pytest_cache/\n', 'utf8')
}

function initGit(workspace) {
  execFileSync('git', ['init'], { cwd: workspace, stdio: 'pipe' })
  execFileSync('git', ['config', 'user.email', 'tests@example.com'], { cwd: workspace, stdio: 'pipe' })
  execFileSync('git', ['config', 'user.name', 'VoiceOps External E2E'], { cwd: workspace, stdio: 'pipe' })
  execFileSync('git', ['add', '.'], { cwd: workspace, stdio: 'pipe' })
  execFileSync('git', ['commit', '-m', 'initial'], { cwd: workspace, stdio: 'pipe' })
}

function startBackend(port, tempRoot, workspace, logBuffer) {
  const env = {
    ...process.env,
    USERS_STORE_PATH: path.join(tempRoot, 'users.json'),
    COLLAB_STORE_BACKEND: 'json',
    COLLAB_STORE_PATH: path.join(tempRoot, 'collab.json'),
    SPEAKER_STORE_BACKEND: 'json',
    SPEAKER_STORE_PATH: path.join(tempRoot, 'speakers.json'),
    EXTERNAL_AGENT_STORE_PATH: path.join(tempRoot, 'external-agents.json'),
    EXTERNAL_AGENT_CREDENTIAL_SECRET: 'external-agent-patch-e2e-secret',
    AGENT_RUNS_PATH: path.join(tempRoot, 'agent-runs.json'),
    MEMORY_STORE_PATH: path.join(tempRoot, 'memory.json'),
    RAG_INDEX_PATH: path.join(tempRoot, 'rag-index.json'),
    VOICEOPS_CACHE_PATH: path.join(tempRoot, 'cache.json'),
    JWT_SECRET: 'ui-external-agent-patch-e2e-secret',
    VOICEOPS_WORKSPACE: workspace,
    EXTERNAL_AGENT_CLI_EXECUTION_ENABLED: 'true',
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
      this.stdout.on('data', (chunk) => pushLog(logBuffer, chunk))
      this.stderr.on('data', (chunk) => pushLog(logBuffer, chunk))
    })
}

function startFrontend(port, apiUrl, logBuffer) {
  const child = spawn(
    'npm',
    ['run', 'dev', '--', '--host', '127.0.0.1', '--port', String(port), '--strictPort'],
    {
      cwd: frontendRoot,
      env: { ...process.env, VITE_DEV_PORT: String(port), VITE_API_TARGET: apiUrl },
      stdio: ['ignore', 'pipe', 'pipe'],
    },
  )
  child.stdout.on('data', (chunk) => pushLog(logBuffer, chunk))
  child.stderr.on('data', (chunk) => pushLog(logBuffer, chunk))
  return child
}

function pushLog(logBuffer, chunk) {
  logBuffer.push(chunk.toString())
  if (logBuffer.length > 80) logBuffer.splice(0, logBuffer.length - 80)
}

async function waitForHttp(url, label) {
  const deadline = Date.now() + 45_000
  let lastError = ''
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url)
      if (response.ok || response.status < 500) return
      lastError = `${response.status} ${response.statusText}`
    } catch (error) {
      lastError = error.message
    }
    await new Promise((resolve) => setTimeout(resolve, 250))
  }
  throw new E2EFailure(label, `timed out waiting for ${url}: ${lastError}`)
}

function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer()
    server.unref()
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
  await new Promise((resolve) => {
    const timer = setTimeout(() => {
      child.kill('SIGKILL')
      resolve()
    }, 3000)
    child.once('exit', () => {
      clearTimeout(timer)
      resolve()
    })
  })
}

main()
