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
  const tempRoot = await fs.mkdtemp(path.join(os.tmpdir(), 'voiceops-generated-speaker-e2e-'))
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
    const routeState = await installGeneratedVerificationRoutes(page, tempRoot, workspace)
    await page.goto(`${webUrl}/react?generated_speaker_verification_e2e=${Date.now()}&live_smoke=1`)

    await page.getByLabel('Email', { exact: true }).fill('admin@voiceops.dev')
    await page.getByLabel('Password', { exact: true }).fill('admin123')
    await page.getByRole('button', { name: /^sign in$/i }).click()
    await expect(page.getByRole('heading', { name: 'Team room' })).toBeVisible({ timeout: 30_000 })
    await openSystemDetails(page)
    await expect(page.getByText('Real audio verify')).toBeVisible({ timeout: 30_000 })

    const button = page.getByRole('button', { name: /generate sample/i })
    await expect(button).toBeVisible({ timeout: 30_000 })
    await button.click()

    await expect(page.getByRole('button', { name: /running sample/i })).toBeVisible({ timeout: 30_000 })
    await expect(page.getByText('Generating macOS two-speaker sample')).toBeVisible({ timeout: 30_000 })
    await expect(page.locator('.speaker-verification-job')).toContainText('generating audio')
    const generatedVerificationText = await page.locator('.speaker-verification-job').textContent()

    routeState.job = null
    await page.reload()
    await openSystemDetails(page)
    await expect(page.getByText('Real audio verify')).toBeVisible({ timeout: 30_000 })
    const uploadPath = path.join(tempRoot, 'two-speaker-upload.wav')
    await fs.writeFile(uploadPath, Buffer.from('fake-wav-audio'))
    await page.locator('.verification-upload input[type="file"]').setInputFiles(uploadPath)
    await expect(page.getByText('two-speaker-upload.wav')).toBeVisible({ timeout: 30_000 })
    await page.locator('.verification-upload').getByRole('button', { name: /^verify$/i }).click()
    await expect(page.locator('.speaker-verification-job')).toContainText('Accepted real audio sample', { timeout: 30_000 })
    await expect(page.locator('.speaker-verification-job')).toContainText('accepted upload')
    const uploadVerificationText = await page.locator('.speaker-verification-job').textContent()

    const report = await page.evaluate(() => ({
      runtimeText: document.querySelector('.runtime-status')?.textContent?.trim() || '',
    }))
    report.generatedVerificationText = generatedVerificationText?.trim() || ''
    report.uploadVerificationText = uploadVerificationText?.trim() || ''
    report.generatedRequests = routeState.generatedRequests
    report.generatedBodies = routeState.generatedBodies
    report.uploadRequests = routeState.uploadRequests
    report.uploadBodies = routeState.uploadBodies

    assertReport(report)
    printReport({ ...report, workspace, webUrl }, asJson)
  } catch (error) {
    await printFailure(error, logs)
    process.exitCode = 1
  } finally {
    if (browser) await browser.close().catch(() => {})
    await Promise.all(processes.map((child) => stopProcess(child)))
    if (!keepWorkspace) await fs.rm(tempRoot, { recursive: true, force: true })
  }
}

async function openSystemDetails(page) {
  const systemDetails = page.locator('details.rail-section-details', { hasText: 'System details' })
  await expect(systemDetails).toBeVisible({ timeout: 30_000 })
  if (!(await systemDetails.evaluate((node) => node.open))) {
    await systemDetails.locator('summary').first().click()
  }
  const operatorDetails = systemDetails.locator('details.diagnostic-details', { hasText: 'Operator diagnostics' })
  await expect(operatorDetails).toBeVisible({ timeout: 30_000 })
  if (!(await operatorDetails.evaluate((node) => node.open))) {
    await operatorDetails.locator('summary').first().click()
  }
}

async function installGeneratedVerificationRoutes(page, tempRoot, workspace) {
  const state = {
    generatedRequests: 0,
    generatedBodies: [],
    uploadRequests: 0,
    uploadBodies: [],
    job: null,
  }

  await page.route('**/system/runtime/status', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(runtimeStatus(tempRoot, workspace)),
    })
  })

  await page.route('**/system/readiness', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(readinessStatus(tempRoot, workspace, state.job?.verification)),
    })
  })

  await page.route(/\/system\/speaker\/verification\/generated$/, async (route) => {
    state.generatedRequests += 1
    state.generatedBodies.push(route.request().postData() || '')
    state.job = runningGeneratedJob(tempRoot)
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(state.job),
    })
  })

  await page.route(/\/system\/speaker\/verification$/, async (route) => {
    if (route.request().method() === 'POST') {
      state.uploadRequests += 1
      state.uploadBodies.push(route.request().postData() || '')
      state.job = runningUploadJob(tempRoot)
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(state.job),
      })
      return
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(state.job || idleVerificationJob(tempRoot)),
    })
  })

  await page.route('**/system/speaker/warmup', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        provider: 'whisperx',
        state: 'idle',
        detail: 'Warmup has not been started',
        timeout_seconds: 7,
        stages: [],
        ready: false,
      }),
    })
  })

  return state
}

function runtimeStatus(tempRoot, workspace) {
  return {
    app_name: 'VoiceOps',
    stores: [
      {
        id: 'collab',
        label: 'Collaboration memory',
        backend: 'json',
        path: path.join(tempRoot, 'collab.json'),
        exists: true,
        size_bytes: 2,
        migration_available: true,
      },
      {
        id: 'speakers',
        label: 'Speaker identity',
        backend: 'json',
        path: path.join(tempRoot, 'speakers.json'),
        exists: true,
        size_bytes: 2,
        migration_available: true,
      },
    ],
    providers: [
      {
        id: 'speaker',
        label: 'Speaker identity',
        value: 'whisperx',
        ready: true,
        detail: 'WhisperX ready for generated sample verification',
        checks: [
          { id: 'hf_token', label: 'HF_TOKEN', ready: true },
          { id: 'whisperx', label: 'WhisperX package', ready: true },
          { id: 'pyannote', label: 'pyannote.audio', ready: true },
        ],
      },
      {
        id: 'llm',
        label: 'Intent router',
        value: 'mock',
        ready: true,
      },
    ],
    workspace: {
      configured: true,
      path: workspace,
      exists: true,
    },
    cache: {
      path: path.join(tempRoot, 'cache.json'),
      exists: true,
      entries: 0,
      expired_entries: 0,
      size_bytes: 2,
      git_ttl_seconds: 10,
      workspace_ttl_seconds: 10,
    },
    warnings: [],
  }
}

function readinessStatus(tempRoot, workspace, verification) {
  const speakerVerification = verification || idleVerificationStatus(tempRoot)
  return {
    app_name: 'VoiceOps',
    status: 'ready',
    ready: true,
    checked_at: '2026-06-18T12:00:00+00:00',
    demo_command: 'cd backend && python scripts/demo_readiness.py',
    checks: [
      { id: 'backend', label: 'Backend', ready: true, status: 'ready' },
      { id: 'workspace', label: 'Workspace', ready: true, status: 'ready', detail: workspace },
      { id: 'git', label: 'Git workflow', ready: true, status: 'ready', detail: 'main, clean' },
      { id: 'speaker', label: 'Speaker identity', ready: true, status: 'ready', detail: 'whisperx ready' },
      { id: 'memory', label: 'Meeting memory', ready: true, status: 'ready', detail: 'collab json, speakers json' },
      { id: 'live', label: 'Live meeting', ready: true, status: 'ready', detail: 'whisperx ready' },
      {
        id: 'diarization',
        label: 'Real diarization',
        ready: speakerVerification.verified,
        status: speakerVerification.status,
        detail: speakerVerification.detail,
      },
    ],
    workspace: {
      configured: true,
      path: workspace,
      exists: true,
    },
    git: {
      is_git_repo: true,
      branch: 'main',
      dirty: false,
      files: [],
    },
    providers: runtimeStatus(tempRoot, workspace).providers,
    cache: runtimeStatus(tempRoot, workspace).cache,
    warnings: [],
    speaker_verification: speakerVerification,
  }
}

function idleVerificationJob(tempRoot) {
  return {
    state: 'idle',
    detail: 'No verification job is running',
    poll_url: '/system/speaker/verification',
    source: 'upload',
    filename: null,
    size_bytes: 0,
    strict_multi_speaker: false,
    generated_audio: false,
    stages: [],
    verification: idleVerificationStatus(tempRoot),
  }
}

function runningGeneratedJob(tempRoot) {
  return {
    job_id: 'verify-generated-e2e',
    state: 'running',
    detail: 'Generating macOS two-speaker sample; starting verification',
    poll_url: '/system/speaker/verification',
    source: 'generated_macos_tts',
    filename: 'generated-macos-two-speaker.wav',
    size_bytes: 0,
    strict_multi_speaker: true,
    generated_audio: true,
    started_at: '2026-06-18T12:00:00+00:00',
    timeout_seconds: 37,
    stages: [
      {
        stage: 'generating_audio',
        message: 'Generating macOS two-speaker sample',
        elapsed_ms: 0,
      },
    ],
    verification: idleVerificationStatus(tempRoot),
  }
}

function runningUploadJob(tempRoot) {
  return {
    job_id: 'verify-upload-e2e',
    state: 'running',
    detail: 'Uploaded real audio; starting verification',
    poll_url: '/system/speaker/verification',
    source: 'upload',
    filename: 'two-speaker-upload.wav',
    size_bytes: 14,
    strict_multi_speaker: true,
    generated_audio: false,
    started_at: '2026-06-18T12:01:00+00:00',
    timeout_seconds: 37,
    stages: [
      {
        stage: 'accepted_upload',
        message: 'Accepted real audio sample',
        elapsed_ms: 0,
      },
    ],
    verification: idleVerificationStatus(tempRoot),
  }
}

function idleVerificationStatus(tempRoot) {
  return {
    path: path.join(tempRoot, 'speaker_verification.json'),
    exists: false,
    verified: false,
    status: 'not_verified',
    provider: 'whisperx',
    distinct_speaker_count: 0,
    speaker_labels: [],
    strict_multi_speaker: false,
    generated_audio: false,
    stages: [],
    quality: {},
    warnings: [],
    config: {},
    detail: 'No real diarization smoke report has been recorded',
  }
}

function assertReport(report) {
  if (report.generatedRequests !== 1) {
    throw new E2EFailure('assert report', `expected one generated verification request, got ${report.generatedRequests}`)
  }
  if (report.uploadRequests !== 1) {
    throw new E2EFailure('assert report', `expected one upload verification request, got ${report.uploadRequests}`)
  }
  if (!report.generatedBodies.some((body) => body.includes('require_multiple_speakers'))) {
    throw new E2EFailure('assert report', 'generated request did not include strict speaker form field')
  }
  if (!report.uploadBodies.some((body) => body.includes('require_multiple_speakers') && body.includes('two-speaker-upload.wav'))) {
    throw new E2EFailure('assert report', 'upload request did not include audio file and strict speaker form field')
  }
  if (!report.generatedVerificationText.includes('Running sample')) {
    throw new E2EFailure('assert report', `generated verification panel did not enter running state: ${report.generatedVerificationText}`)
  }
  if (!report.generatedVerificationText.includes('Generating macOS two-speaker sample')) {
    throw new E2EFailure('assert report', `verification panel did not show generated sample stage: ${report.generatedVerificationText}`)
  }
  if (!report.uploadVerificationText.includes('Accepted real audio sample')) {
    throw new E2EFailure('assert report', `upload verification panel did not show accepted upload stage: ${report.uploadVerificationText}`)
  }
  if (!report.runtimeText.includes('whisperx')) {
    throw new E2EFailure('assert report', 'runtime panel did not render whisperx provider state')
  }
}

function printReport(report, json) {
  if (json) {
    console.log(JSON.stringify(report, null, 2))
    return
  }
  console.log('Generated speaker verification UI E2E passed')
  console.log(`URL: ${report.webUrl}/react?generated_speaker_verification_e2e=1`)
  console.log(`Workspace: ${report.workspace}`)
  console.log(`Generated requests: ${report.generatedRequests}`)
  console.log(`Upload requests: ${report.uploadRequests}`)
}

async function printFailure(error, logs) {
  console.error(`Generated speaker verification UI E2E failed: ${error.message || error}`)
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
    JWT_SECRET: 'ui-generated-speaker-e2e-secret',
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
      '    return {"service": "generated-speaker-verification"}',
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
  execFileSync('git', ['config', 'user.name', 'VoiceOps Generated Speaker E2E'], { cwd: workspace, stdio: 'pipe' })
  execFileSync('git', ['add', '.'], { cwd: workspace, stdio: 'pipe' })
  execFileSync('git', ['commit', '-m', 'initial'], { cwd: workspace, stdio: 'pipe' })
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

main()
