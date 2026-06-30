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
const agentDisplayName = 'Ada'

const smokeTexts = [
  'Alice mentioned app.py in the live meeting smoke path',
  'Bob is reviewing the speaker label correction',
  `${agentDisplayName} what files were mentioned?`,
]

class E2EFailure extends Error {
  constructor(step, message) {
    super(`${step}: ${message}`)
    this.step = step
  }
}

async function main() {
  const tempRoot = await fs.mkdtemp(path.join(os.tmpdir(), 'voiceops-live-smoke-e2e-'))
  const workspace = path.join(tempRoot, 'workspace')
  const logs = { backend: [], frontend: [] }
  const processes = []
  let browser

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
    await page.goto(`${webUrl}/react?live_smoke=1&e2e=${Date.now()}`, { waitUntil: 'domcontentloaded' })

    await page.getByLabel('Email', { exact: true }).fill('admin@voiceops.dev')
    await page.getByLabel('Password', { exact: true }).fill('admin123')
    await page.getByRole('button', { name: /^sign in$/i }).click()
    await expect(page.getByRole('heading', { name: 'Team room' })).toBeVisible({ timeout: 30_000 })
    await openRuntimeDiagnostics(page)
    await expect(page.getByText('Operator diagnostics')).toBeVisible({ timeout: 30_000 })
    await expect(page.getByText('Target closure')).toBeVisible({ timeout: 30_000 })

    const teamSettings = page.locator('details.left-rail-details', { hasText: /Speakers/ })
    await teamSettings.locator('summary').click()

    const agentSettings = page.locator('.agent-settings')
    await expect(agentSettings).toBeVisible({ timeout: 30_000 })
    await agentSettings.getByLabel('AI teammate name', { exact: true }).fill(agentDisplayName)
    await agentSettings.getByLabel('AI teammate initials', { exact: true }).fill('AD')
    await agentSettings.getByLabel('AI teammate wake words', { exact: true }).fill('ada, helper')
    await agentSettings.getByRole('button', { name: /save ai teammate settings/i }).click()
    await expect(agentSettings.getByText('saved')).toBeVisible({ timeout: 30_000 })
    const aiCoworkerRow = page.locator('.person.ai-coworker-person')
    await expect(aiCoworkerRow).toBeVisible({ timeout: 30_000 })
    await expect(aiCoworkerRow).toContainText(agentDisplayName)
    await expect(aiCoworkerRow).toContainText('AI coworker')

    const liveMeetingButton = page.getByRole('button', { name: /start live meeting/i })
    await expect(liveMeetingButton).toBeVisible({ timeout: 30_000 })
    await liveMeetingButton.scrollIntoViewIfNeeded()
    await liveMeetingButton.click({ force: true })
    await expect(page.getByRole('button', { name: /stop live meeting/i })).toBeVisible({ timeout: 30_000 })
    await expect(page.getByLabel('Type command', { exact: true })).toBeEnabled({ timeout: 10_000 })
    await page.getByLabel('Type command', { exact: true }).fill('what files were mentioned?')
    await expect(page.getByRole('button', { name: /send command/i })).toBeEnabled({ timeout: 10_000 })
    await page.getByLabel('Type command', { exact: true }).fill('')

    for (const text of smokeTexts) {
      await expect(page.getByText(text).first()).toBeVisible({ timeout: 30_000 })
    }
    await expect(page.getByText('Live diagnostics')).toBeVisible({ timeout: 30_000 })

    const report = await pollLiveReport(page)
    const layout = await verifyResponsiveLayout(page)

    await page.getByRole('button', { name: /stop live meeting/i }).click()

    assertReport({ ...report, layout })
    printReport({ ...report, layout, workspace, webUrl }, asJson)
  } catch (error) {
    await printFailure(error, logs)
    process.exitCode = 1
  } finally {
    if (browser) await browser.close().catch(() => {})
    await Promise.all(processes.map((child) => stopProcess(child)))
    if (!keepWorkspace) await fs.rm(tempRoot, { recursive: true, force: true })
  }
}

async function openRuntimeDiagnostics(page) {
  const systemDetails = page.locator('details.rail-section-details', { hasText: 'System details' })
  await expect(systemDetails).toBeVisible({ timeout: 30_000 })
  if (!(await systemDetails.evaluate((node) => node.open))) {
    await systemDetails.locator('summary').first().click()
  }

  const diagnostics = systemDetails.locator('details.diagnostic-details', { hasText: 'Runtime diagnostics' })
  await expect(diagnostics).toBeVisible({ timeout: 10_000 })
  if (!(await diagnostics.evaluate((node) => node.open))) {
    await diagnostics.locator('summary').first().click()
  }
  await expect(diagnostics.getByText('Recent audit')).toBeVisible({ timeout: 10_000 })
  return diagnostics
}

async function pollLiveReport(page) {
  const deadline = Date.now() + 30_000
  let lastReport = null
  while (Date.now() < deadline) {
    lastReport = await readLiveReport(page)
    if (
      smokeTexts.every((text) => lastReport.liveTexts.includes(text))
      && lastReport.speakerLabels.includes('SPEAKER_01')
      && lastReport.speakerLabels.includes('SPEAKER_00')
      && lastReport.liveMessageCount >= 3
      && lastReport.agentMessageCount >= 1
      && lastReport.agentSources.includes('rag_query')
      && lastReport.agentTexts.some((text) => text.includes('app.py'))
      && lastReport.feedCitationCount > 0
      && lastReport.feedTraceText.includes('provider local_sparse')
      && lastReport.latency.hasPanel
      && lastReport.latency.finalChunks >= 3
      && lastReport.latency.bestWarm !== 'pending'
      && lastReport.targetClosureText.includes('100%')
    ) {
      return lastReport
    }
    await delay(300)
  }
  throw new E2EFailure('poll live report', `timed out waiting for live timeline data: ${JSON.stringify(lastReport)}`)
}

async function readLiveReport(page) {
  return page.evaluate(async () => {
    const token = localStorage.getItem('voiceops_token')
    const headers = { Authorization: `Bearer ${token}` }
    const [roomResponse, speakerResponse] = await Promise.all([
      fetch('/collab/rooms/main', { headers }),
      fetch('/speakers/rooms/main', { headers }),
    ])
    const room = await roomResponse.json()
    const speakers = await speakerResponse.json()
    const liveMessages = (room.messages || []).filter((message) => (
      message.metadata?.source === 'live_audio'
      || message.metadata?.live_session_id
      || message.metadata?.live_temp_id
      || message.source === 'live_audio'
    ))
    const liveTexts = liveMessages.map((message) => message.text).filter(Boolean)
    const speakerLabels = [...new Set(liveMessages.map((message) => (
      message.speaker_label || message.metadata?.speaker_label || message.metadata?.raw_speaker_label
    )).filter(Boolean))]
    const agentMessages = (room.messages || []).filter((message) => (
      message.role === 'agent'
      || message.actor_kind === 'agent'
      || message.metadata?.trigger === 'wake_word'
    ))
    const agentTexts = agentMessages.map((message) => message.text).filter(Boolean)
    const agentSources = [...new Set(agentMessages.map((message) => message.metadata?.source).filter(Boolean))]
    const agentActorNames = [...new Set(agentMessages.map((message) => message.actor_name).filter(Boolean))]
    const latency = readLatencyPanel()
    return {
      liveMessageCount: liveMessages.length,
      liveTexts,
      speakerLabels,
      agentMessageCount: agentMessages.length,
      agentTexts,
      agentSources,
      agentActorNames,
      unknownLabels: (speakers.unknown_speakers || []).map((item) => item.speaker_label),
      provider: speakers.provider || '',
      diagnosticsText: document.querySelector('.live-diagnostics')?.textContent?.trim() || '',
      latency,
      targetClosureText: document.querySelector('.target-readiness')?.textContent?.trim() || '',
      feedText: document.querySelector('.feed')?.textContent?.trim() || '',
      feedCitationCount: document.querySelectorAll('.rag-citation-chip').length,
      feedCitationText: [...document.querySelectorAll('.rag-citation-chip')]
        .map((node) => node.textContent?.trim() || '')
        .filter(Boolean)
        .join(' | '),
      feedTraceText: [...document.querySelectorAll('.rag-trace')]
        .map((node) => node.textContent?.trim() || '')
        .filter(Boolean)
        .join(' | '),
    }

    function readLatencyPanel() {
      const panel = document.querySelector('.latency-panel')
      const rows = Object.fromEntries(Array.from(panel?.querySelectorAll('.latency-grid div') || []).map((row) => {
        const label = row.querySelector('span')?.textContent?.trim() || ''
        const value = row.querySelector('b')?.textContent?.trim() || ''
        return [label, value]
      }))
      return {
        hasPanel: Boolean(panel),
        text: panel?.textContent?.trim() || '',
        label: panel?.querySelector('.latency-head b')?.textContent?.trim() || '',
        current: rows.Current || '',
        elapsed: rows.Elapsed || '',
        lastFinal: rows['Last final'] || '',
        bestWarm: rows['Best warm'] || '',
        coldStart: rows['Cold start'] || '',
        finalChunks: Number(rows['Final chunks'] || 0),
      }
    }
  })
}

async function verifyResponsiveLayout(page) {
  const desktop = await readLayoutMetrics(page)
  await page.setViewportSize({ width: 390, height: 844 })
  await delay(400)
  const mobile = await readLayoutMetrics(page)
  await page.setViewportSize({ width: 1440, height: 1000 })
  await delay(100)
  return { desktop, mobile }
}

async function readLayoutMetrics(page) {
  return page.evaluate(() => {
    const live = document.querySelector('.live-diagnostics')
    const latency = document.querySelector('.latency-panel')
    const target = document.querySelector('.target-readiness')
    const rail = document.querySelector('.rail--right')
    const leftRail = document.querySelector('.rail--left')
    const liveRect = live?.getBoundingClientRect()
    const latencyRect = latency?.getBoundingClientRect()
    const targetRect = target?.getBoundingClientRect()
    const railRect = rail?.getBoundingClientRect()
    const leftRailRect = leftRail?.getBoundingClientRect()
    const speakerAssignControls = Array.from(document.querySelectorAll('.rail--left select[aria-label^="Assign SPEAKER_"]'))
    return {
      viewportWidth: window.innerWidth,
      hasLiveDiagnostics: Boolean(live),
      hasLatencyPanel: Boolean(latency),
      hasTargetClosure: Boolean(target),
      leftRailVisible: Boolean(
        leftRail
        && leftRailRect
        && leftRailRect.width > 0
        && leftRailRect.height > 0
        && getComputedStyle(leftRail).display !== 'none'
      ),
      leftRailHeight: Math.round(leftRailRect?.height || 0),
      speakerAssignControlCount: speakerAssignControls.length,
      railWidth: Math.round(railRect?.width || 0),
      liveWidth: Math.round(liveRect?.width || 0),
      latencyWidth: Math.round(latencyRect?.width || 0),
      targetWidth: Math.round(targetRect?.width || 0),
      latencyFitsLive: Boolean(liveRect && latencyRect && latencyRect.width <= liveRect.width),
      targetFitsRail: Boolean(railRect && targetRect && targetRect.width <= railRect.width),
      overflowX: document.documentElement.scrollWidth > document.documentElement.clientWidth,
    }
  })
}

function assertReport(report) {
  for (const text of smokeTexts) {
    if (!report.liveTexts.includes(text)) {
      throw new E2EFailure('assert report', `missing live transcript text: ${text}`)
    }
    if (!report.feedText.includes(text)) {
      throw new E2EFailure('assert report', `feed did not render live transcript text: ${text}`)
    }
  }
  for (const label of ['SPEAKER_01', 'SPEAKER_00']) {
    if (!report.speakerLabels.includes(label)) {
      throw new E2EFailure('assert report', `missing speaker label ${label}; got ${report.speakerLabels.join(', ')}`)
    }
  }
  if (report.provider && report.provider !== 'mock') {
    throw new E2EFailure('assert report', `expected mock provider, got ${report.provider}`)
  }
  if (!report.diagnosticsText.includes('Provider') || !report.diagnosticsText.includes('mock')) {
    throw new E2EFailure('assert report', 'live diagnostics did not expose mock provider')
  }
  if (!report.latency.hasPanel || !report.diagnosticsText.includes('Live latency')) {
    throw new E2EFailure('assert report', 'live diagnostics did not expose latency panel')
  }
  if (!report.targetClosureText.includes('Target closure')) {
    throw new E2EFailure('assert report', 'target closure panel did not render')
  }
  if (!report.targetClosureText.includes('100%')) {
    throw new E2EFailure('assert report', `target closure is not complete: ${report.targetClosureText}`)
  }
  if (report.latency.finalChunks < 3) {
    throw new E2EFailure('assert report', `expected at least three finalized latency chunks, got ${report.latency.finalChunks}`)
  }
  if (!report.latency.label.includes('warm') || report.latency.bestWarm === 'pending') {
    throw new E2EFailure('assert report', `expected warm latency summary, got ${JSON.stringify(report.latency)}`)
  }
  for (const [name, metrics] of Object.entries(report.layout || {})) {
    if (metrics.overflowX) {
      throw new E2EFailure('assert report', `${name} layout has horizontal overflow: ${JSON.stringify(metrics)}`)
    }
    if (!metrics.hasLatencyPanel || !metrics.latencyFitsLive) {
      throw new E2EFailure('assert report', `${name} latency panel does not fit live diagnostics: ${JSON.stringify(metrics)}`)
    }
    if (!metrics.hasTargetClosure || !metrics.targetFitsRail) {
      throw new E2EFailure('assert report', `${name} target closure panel does not fit right rail: ${JSON.stringify(metrics)}`)
    }
    if (name === 'mobile' && (!metrics.leftRailVisible || metrics.speakerAssignControlCount < 1)) {
      throw new E2EFailure('assert report', `${name} speaker assignment is not reachable: ${JSON.stringify(metrics)}`)
    }
  }
  if (report.liveMessageCount < 3) {
    throw new E2EFailure('assert report', `expected at least three live messages, got ${report.liveMessageCount}`)
  }
  if (report.agentMessageCount < 1) {
    throw new E2EFailure('assert report', 'expected wake-word agent response in room timeline')
  }
  if (!report.agentSources.includes('rag_query')) {
    throw new E2EFailure('assert report', `expected rag_query agent source, got ${report.agentSources.join(', ') || 'none'}`)
  }
  if (!report.agentTexts.some((text) => text.includes('app.py'))) {
    throw new E2EFailure('assert report', `expected agent answer to mention app.py, got ${report.agentTexts.join(' | ') || 'none'}`)
  }
  if (!report.agentActorNames.includes(agentDisplayName)) {
    throw new E2EFailure('assert report', `expected agent actor ${agentDisplayName}, got ${report.agentActorNames.join(', ') || 'none'}`)
  }
  if (!report.feedText.includes('Files mentioned: app.py')) {
    throw new E2EFailure('assert report', 'feed did not render agent memory answer')
  }
  if (report.feedCitationCount < 1 || !report.feedCitationText.includes('app.py')) {
    throw new E2EFailure('assert report', `feed did not render RAG citations: ${report.feedCitationText || 'none'}`)
  }
  if (!report.feedTraceText.includes('provider local_sparse') || !report.feedTraceText.includes('short')) {
    throw new E2EFailure('assert report', `feed did not render RAG retrieval trace: ${report.feedTraceText || 'none'}`)
  }
  if (!report.feedText.includes(agentDisplayName)) {
    throw new E2EFailure('assert report', `feed did not render custom agent name ${agentDisplayName}`)
  }
}

function printReport(report, json) {
  if (json) {
    console.log(JSON.stringify(report, null, 2))
    return
  }
  console.log('Live meeting smoke UI E2E passed')
  console.log(`URL: ${report.webUrl}/react?live_smoke=1`)
  console.log(`Workspace: ${report.workspace}`)
  console.log(`Provider: ${report.provider || 'mock'}`)
  console.log(`Speakers: ${report.speakerLabels.join(', ')}`)
  console.log(`Latency: ${report.latency.label}; best warm ${report.latency.bestWarm}; final chunks ${report.latency.finalChunks}`)
  console.log(`Live transcript: ${report.liveTexts.join(' | ')}`)
  console.log(`Agent: ${report.agentActorNames.join(', ')} - ${report.agentTexts.join(' | ')}`)
}

async function printFailure(error, logs) {
  console.error(`Live meeting smoke UI E2E failed: ${error.message || error}`)
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
    COLLAB_STORE_BACKEND: 'sqlite',
    COLLAB_SQLITE_PATH: path.join(tempRoot, 'collab.sqlite3'),
    SPEAKER_STORE_BACKEND: 'json',
    SPEAKER_STORE_PATH: path.join(tempRoot, 'speakers.json'),
    MEMORY_STORE_PATH: path.join(tempRoot, 'memory.json'),
    RAG_INDEX_PATH: path.join(tempRoot, 'rag-index.json'),
    VOICEOPS_CACHE_PATH: path.join(tempRoot, 'cache.json'),
    JWT_SECRET: 'ui-live-smoke-e2e-secret',
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
      '    return {"service": "live-smoke"}',
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
    ].join('\n'),
    'utf8',
  )
}

function initGit(workspace) {
  execFileSync('git', ['init'], { cwd: workspace, stdio: 'pipe' })
  execFileSync('git', ['config', 'user.email', 'tests@example.com'], { cwd: workspace, stdio: 'pipe' })
  execFileSync('git', ['config', 'user.name', 'VoiceOps Live E2E'], { cwd: workspace, stdio: 'pipe' })
  execFileSync('git', ['add', '.'], { cwd: workspace, stdio: 'pipe' })
  execFileSync('git', ['commit', '-m', 'initial'], { cwd: workspace, stdio: 'pipe' })
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

main()
