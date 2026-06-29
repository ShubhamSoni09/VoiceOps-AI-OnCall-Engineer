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
const keepWorkspace = args.has('--keep-workspace')
const asJson = args.has('--json')

class E2EFailure extends Error {
  constructor(step, message) {
    super(`${step}: ${message}`)
    this.step = step
  }
}

async function main() {
  const tempRoot = await fs.mkdtemp(path.join(os.tmpdir(), 'voiceops-ui-closure-e2e-'))
  const workspace = path.join(tempRoot, 'workspace')
  const logs = { backend: [], frontend: [] }
  const processes = []
  let browser
  let observerContext

  try {
    await writeWorkspace(workspace)
    initGit(workspace)

    const apiPort = await freePort()
    const webPort = await freePort()
    const apiUrl = `http://127.0.0.1:${apiPort}`
    const webUrl = `http://127.0.0.1:${webPort}`

    processes.push(startBackend(apiPort, tempRoot, workspace, logs.backend))
    await waitForHttp(`${apiUrl}/health`, 'backend health')

    processes.push(startFrontend(webPort, apiUrl, logs.frontend))
    await waitForHttp(webUrl, 'frontend dev server')

    browser = await chromium.launch({
      headless: !process.env.VOICEOPS_E2E_HEADED,
    })
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
    await page.goto(`${webUrl}/react?meeting_closure_smoke=pending&e2e=${Date.now()}`)

    await page.getByRole('button', { name: /^sign in$/i }).click()

    observerContext = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
    const observerPage = await observerContext.newPage()
    await observerPage.goto(`${webUrl}/react?observer_e2e=${Date.now()}`)
    await observerPage.getByLabel('Email').fill('admin@voiceops.dev')
    await observerPage.getByLabel('Password').fill('admin123')
    await observerPage.getByRole('button', { name: /^sign in$/i }).click()
    await expect(observerPage.getByRole('heading', { name: 'Team room' })).toBeVisible({ timeout: 30_000 })

    const smokeStatus = page.getByTestId('meeting-closure-smoke-status')
    await expect(smokeStatus).toContainText('E2E gate ready', { timeout: 90_000 })

    const teamSettings = await openTeamSettings(page)
    await expect(teamSettings.getByText(/SPEAKER_00/).first()).toBeVisible()
    await expect(teamSettings.getByText(/SPEAKER_01/).first()).toBeVisible()
    await expect(page.getByText(/failing health check in app\.py/i).first()).toBeVisible()

    const pendingAction = observerPage.getByTestId('agent-action-pending_approval').filter({ hasText: 'patch' }).first()
    await expect(pendingAction).toContainText('awaiting approval', { timeout: 30_000 })
    await expect(pendingAction).toContainText('Priya Nair')
    await expect(pendingAction).toContainText('agent pipeline · approval required before workspace write')
    await expect(pendingAction.getByText('Diff preview')).toBeVisible()
    const handoffReview = observerPage.getByTestId('handoff-review')
    await expect(handoffReview).toContainText('Open review')
    await expect(handoffReview).toContainText('Patch awaiting approval')
    await expect(handoffReview).toContainText('agent pipeline · approval required before workspace write')
    await pendingAction.getByRole('button', { name: /^Approve$/i }).click()

    await expect(smokeStatus).toContainText('E2E gate completed', { timeout: 90_000 })
    await expect(page.getByText(/Sam Ortiz approved the patch on voiceops\/act-/i).first()).toBeVisible()

    const completedAction = observerPage.getByTestId('agent-action-completed').filter({ hasText: 'patch' }).first()
    await expect(completedAction).toContainText('completed')
    await expect(completedAction).toContainText(/voiceops\/act-/)
    await expect(completedAction).toContainText('Verification python -m pytest -q')
    await expect(completedAction.getByText('Diff preview')).toBeVisible()
    const systemDetails = await openSystemDetails(observerPage)
    await expect(systemDetails.getByText('System summary')).toBeVisible()

    const report = await observerPage.evaluate(async () => {
      const token = localStorage.getItem('voiceops_token')
      const headers = token ? { Authorization: `Bearer ${token}` } : {}
      const [roomResponse, gitResponse, auditResponse] = await Promise.all([
        fetch('/collab/rooms/main', { headers }),
        fetch('/workspace/git/status', { headers }),
        fetch('/collab/rooms/main/audit?kind=action&limit=5', { headers }),
      ])
      const room = await roomResponse.json()
      const git = await gitResponse.json()
      const audit = await auditResponse.json()
      const completed = [...(room.actions || [])]
        .reverse()
        .find((action) => action.action === 'patch' && action.status === 'completed')
      const auditPatch = (audit || []).find((event) => event.source_id === completed?.id)
      return {
        actionId: completed?.id || '',
        branch: completed?.approval?.git?.branch_name || '',
        approver: completed?.approval?.decided_by_name || '',
        routeTrace: completed?.approval?.route_trace || null,
        filesChanged: completed?.approval?.git?.files_changed || completed?.files_changed || [],
        testsPassed: /passed/i.test(completed?.command_output || ''),
        auditTitle: auditPatch?.title || '',
        auditStatus: auditPatch?.status || '',
        auditTone: auditPatch?.tone || '',
        auditChips: auditPatch?.chips || [],
        gitDirty: git.dirty,
        gitFiles: git.files || [],
      }
    })
    report.requesterStatusPill = await page
      .getByTestId('meeting-closure-smoke-status')
      .textContent()
    report.approverVisibleStatus = await completedAction.locator('.action-status').textContent()

    assertReport(report)
    printReport({ ...report, workspace, webUrl }, asJson)
  } catch (error) {
    await printFailure(error, logs)
    process.exitCode = 1
  } finally {
    if (observerContext) await observerContext.close().catch(() => {})
    if (browser) await browser.close().catch(() => {})
    await Promise.all(processes.map((child) => stopProcess(child)))
    if (!keepWorkspace) await fs.rm(tempRoot, { recursive: true, force: true })
  }
}

async function openTeamSettings(page) {
  const settings = page.locator('details.left-rail-details', { hasText: /Speakers/ })
  await expect(settings).toBeVisible({ timeout: 30_000 })
  const isOpen = await settings.evaluate((node) => node.open)
  if (!isOpen) {
    await settings.locator('summary').click()
  }
  await expect(settings.locator('.speaker-block')).toBeVisible({ timeout: 10_000 })
  return settings
}

async function openSystemDetails(page) {
  const systemDetails = page.locator('details.rail-section-details', { hasText: 'System details' })
  await expect(systemDetails).toBeVisible({ timeout: 30_000 })
  if (!(await systemDetails.evaluate((node) => node.open))) {
    await systemDetails.locator('summary').first().click()
  }
  return systemDetails
}

function assertReport(report) {
  if (report.requesterStatusPill !== 'E2E gate completed') {
    throw new E2EFailure('assert report', `expected requester page to sync completed smoke pill, got ${report.requesterStatusPill || 'empty'}`)
  }
  if (report.approverVisibleStatus !== 'completed') {
    throw new E2EFailure('assert report', `expected approver page completed action, got ${report.approverVisibleStatus || 'empty'}`)
  }
  if (!report.actionId) throw new E2EFailure('assert report', 'missing completed patch action')
  if (!report.branch.startsWith(`voiceops/${report.actionId}-`)) {
    throw new E2EFailure('assert report', `unexpected branch ${report.branch}`)
  }
  if (report.approver !== 'Sam Ortiz') {
    throw new E2EFailure('assert report', `expected Sam Ortiz approver, got ${report.approver || 'empty'}`)
  }
  if (report.routeTrace?.route !== 'agent_pipeline') {
    throw new E2EFailure('assert report', `missing route trace on completed action: ${JSON.stringify(report.routeTrace)}`)
  }
  if (report.routeTrace?.action_policy !== 'approval_required_before_workspace_write') {
    throw new E2EFailure('assert report', `unexpected route policy ${report.routeTrace?.action_policy || 'empty'}`)
  }
  if (!report.filesChanged.includes('app.py')) {
    throw new E2EFailure('assert report', `app.py missing from changed files: ${report.filesChanged.join(', ')}`)
  }
  if (report.auditTitle !== 'Patch proposal' || report.auditStatus !== 'completed' || report.auditTone !== 'ok') {
    throw new E2EFailure('assert report', `unexpected audit event ${report.auditTitle}/${report.auditStatus}/${report.auditTone}`)
  }
  if (!report.auditChips.some((chip) => chip.startsWith('branch voiceops/'))) {
    throw new E2EFailure('assert report', `audit event missing branch chip: ${report.auditChips.join(', ')}`)
  }
  if (!report.gitFiles.some((file) => file.path === 'app.py')) {
    throw new E2EFailure('assert report', 'workspace git status did not include app.py')
  }
  if (!report.testsPassed) throw new E2EFailure('assert report', 'approval tests did not pass')
}

function printReport(report, json) {
  if (json) {
    console.log(JSON.stringify(report, null, 2))
    return
  }
  console.log('Meeting closure UI E2E passed')
  console.log(`URL: ${report.webUrl}/react?meeting_closure_smoke=approve`)
  console.log(`Workspace: ${report.workspace}`)
  console.log(`Action: ${report.actionId}`)
  console.log(`Branch: ${report.branch}`)
  console.log(`Approver: ${report.approver}`)
  console.log(`Files: ${report.filesChanged.join(', ')}`)
  console.log(`Tests passed: ${report.testsPassed}`)
  console.log(`Requester sync: ${report.requesterStatusPill}`)
}

async function printFailure(error, logs) {
  console.error(`Meeting closure UI E2E failed: ${error.message || error}`)
  if (error.step) console.error(`Failed step: ${error.step}`)
  console.error('Backend log tail:')
  console.error(logs.backend.slice(-30).join('').trim() || '(empty)')
  console.error('Frontend log tail:')
  console.error(logs.frontend.slice(-30).join('').trim() || '(empty)')
}

function startBackend(port, tempRoot, workspace, logBuffer) {
  const env = {
    ...process.env,
    USERS_STORE_PATH: path.join(tempRoot, 'users.json'),
    COLLAB_STORE_BACKEND: 'json',
    COLLAB_STORE_PATH: path.join(tempRoot, 'collab.json'),
    SPEAKER_STORE_BACKEND: 'json',
    SPEAKER_STORE_PATH: path.join(tempRoot, 'speakers.json'),
    MEMORY_STORE_PATH: path.join(tempRoot, 'memory.json'),
    VOICEOPS_CACHE_PATH: path.join(tempRoot, 'cache.json'),
    JWT_SECRET: 'ui-meeting-closure-e2e-secret',
    LLM_PROVIDER: 'mock',
    STT_PROVIDER: 'mock',
    TTS_PROVIDER: 'mock',
    SPEAKER_PROVIDER: 'mock',
    VOICEOPS_WORKSPACE: workspace,
  }
  return startProcess(
    process.env.PYTHON || 'python',
    ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(port)],
    { cwd: backendRoot, env },
    logBuffer,
  )
}

function startFrontend(port, apiUrl, logBuffer) {
  return startProcess(
    'npm',
    ['run', 'dev', '--', '--host', '127.0.0.1', '--port', String(port), '--strictPort'],
    {
      cwd: frontendRoot,
      env: {
        ...process.env,
        VITE_DEV_PORT: String(port),
        VITE_API_TARGET: apiUrl,
      },
    },
    logBuffer,
  )
}

function startProcess(command, argsList, options, logBuffer) {
  const child = spawn(command, argsList, {
    ...options,
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  child.stdout.on('data', (chunk) => pushLog(logBuffer, chunk))
  child.stderr.on('data', (chunk) => pushLog(logBuffer, chunk))
  child.on('exit', (code, signal) => {
    if (code !== 0 && signal !== 'SIGTERM') {
      pushLog(logBuffer, `\n[process exited code=${code} signal=${signal || ''}]\n`)
    }
  })
  return child
}

function pushLog(logBuffer, chunk) {
  logBuffer.push(chunk.toString())
  if (logBuffer.length > 80) logBuffer.splice(0, logBuffer.length - 80)
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
    await delay(250)
  }
  throw new E2EFailure(label, `timed out waiting for ${url}: ${lastError}`)
}

async function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer()
    server.unref()
    server.on('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address()
      server.close(() => resolve(port))
    })
  })
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
      '    return {"service": "meeting-closure"}',
      '',
    ].join('\n'),
    'utf8',
  )
  await fs.writeFile(path.join(workspace, '.gitignore'), '__pycache__/\n.pytest_cache/\n', 'utf8')
  await fs.writeFile(
    path.join(workspace, 'tests', 'test_app.py'),
    [
      'from fastapi.testclient import TestClient',
      'from app import app',
      '',
      'client = TestClient(app)',
      '',
      'def test_root():',
      '    assert client.get("/").status_code == 200',
      '',
      'def test_health():',
      '    response = client.get("/health")',
      '    assert response.status_code == 200',
      '    assert response.json()["status"] == "ok"',
      '',
    ].join('\n'),
    'utf8',
  )
}

function initGit(workspace) {
  execFileSync('git', ['init'], { cwd: workspace, stdio: 'pipe' })
  execFileSync('git', ['config', 'user.email', 'tests@example.com'], { cwd: workspace, stdio: 'pipe' })
  execFileSync('git', ['config', 'user.name', 'VoiceOps UI E2E'], { cwd: workspace, stdio: 'pipe' })
  execFileSync('git', ['add', '.'], { cwd: workspace, stdio: 'pipe' })
  execFileSync('git', ['commit', '-m', 'initial'], { cwd: workspace, stdio: 'pipe' })
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

main()
