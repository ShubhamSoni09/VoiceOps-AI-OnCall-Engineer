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
  const tempRoot = await fs.mkdtemp(path.join(os.tmpdir(), 'voiceops-speaker-calibration-e2e-'))
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
    await page.goto(`${webUrl}/react?speaker_calibration_e2e=${Date.now()}`)

    await page.getByRole('button', { name: /^sign in$/i }).click()
    await expect(page.getByRole('heading', { name: 'Team room' })).toBeVisible({ timeout: 30_000 })

    await page.evaluate(async () => {
      const token = localStorage.getItem('voiceops_token')
      const headers = {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      }
      const response = await fetch('/speakers/rooms/main/segments', {
        method: 'POST',
        headers,
        body: JSON.stringify({
          session_id: 'speaker-calibration-e2e',
          source: 'meeting_audio',
          segments: [
            {
              speaker_label: 'SPEAKER_09',
              text: 'Need to review app.py health route',
              confidence: 0.62,
            },
          ],
        }),
      })
      if (!response.ok) {
        throw new Error(`segment ingest failed: ${response.status}`)
      }
    })

    await page.reload()
    await expect(page.getByText('SPEAKER_09').first()).toBeVisible({ timeout: 30_000 })
    await expect(page.getByText('Confirm speaker')).toBeVisible()

    const speakerRow = page.locator('.unknown-speaker').filter({ hasText: 'SPEAKER_09' })
    await expect(speakerRow).toHaveCount(1)
    await speakerRow.locator('select').selectOption('user-priya')

    await expect(page.locator('.feed').getByText('Priya Nair mapped SPEAKER_09 to Priya Nair.')).toBeVisible({ timeout: 30_000 })
    await expect(page.getByText('Need to review app.py health route').first()).toBeVisible()

    const report = await page.evaluate(async () => {
      const token = localStorage.getItem('voiceops_token')
      const headers = { Authorization: `Bearer ${token}` }
      const [roomResponse, speakerResponse, auditResponse] = await Promise.all([
        fetch('/collab/rooms/main', { headers }),
        fetch('/speakers/rooms/main', { headers }),
        fetch('/collab/rooms/main/audit?kind=speaker&limit=3', { headers }),
      ])
      const room = await roomResponse.json()
      const speakers = await speakerResponse.json()
      const auditEvents = await auditResponse.json()
      const speakerMessage = (room.messages || []).find((message) => message.speaker_label === 'SPEAKER_09')
      const audit = (room.messages || []).find((message) => (
        message.metadata?.event === 'speaker_mapping_created'
        && message.metadata?.speaker_label === 'SPEAKER_09'
      ))
      const mapping = (speakers.mappings || []).find((item) => item.speaker_label === 'SPEAKER_09')
      return {
        actorName: speakerMessage?.actor_name || '',
        originalActorName: speakerMessage?.metadata?.original_actor_name || '',
        identifiedUserId: speakerMessage?.metadata?.identified_user_id || '',
        mappingUserName: mapping?.user_name || '',
        unknownLabels: (speakers.unknown_speakers || []).map((item) => item.speaker_label),
        auditText: audit?.text || '',
        auditReattributedMessages: audit?.metadata?.reattributed_messages ?? null,
        auditReattributedMemory: audit?.metadata?.reattributed_memory ?? null,
        auditEventTitle: auditEvents?.[0]?.title || '',
        auditEventDetail: auditEvents?.[0]?.detail || '',
        auditEventKind: auditEvents?.[0]?.kind || '',
      }
    })

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

function assertReport(report) {
  if (report.actorName !== 'Priya Nair') {
    throw new E2EFailure('assert report', `expected reattributed actor Priya Nair, got ${report.actorName || 'empty'}`)
  }
  if (report.originalActorName !== 'Unknown speaker') {
    throw new E2EFailure('assert report', `expected original Unknown speaker, got ${report.originalActorName || 'empty'}`)
  }
  if (report.identifiedUserId !== 'user-priya') {
    throw new E2EFailure('assert report', `expected user-priya, got ${report.identifiedUserId || 'empty'}`)
  }
  if (report.mappingUserName !== 'Priya Nair') {
    throw new E2EFailure('assert report', `expected mapping Priya Nair, got ${report.mappingUserName || 'empty'}`)
  }
  if (report.unknownLabels.includes('SPEAKER_09')) {
    throw new E2EFailure('assert report', 'mapped SPEAKER_09 still appears in unknown speakers')
  }
  if (report.auditText !== 'Priya Nair mapped SPEAKER_09 to Priya Nair.') {
    throw new E2EFailure('assert report', `unexpected audit text: ${report.auditText || 'empty'}`)
  }
  if (report.auditReattributedMessages !== 1) {
    throw new E2EFailure('assert report', `expected one reattributed message, got ${report.auditReattributedMessages}`)
  }
  if (report.auditReattributedMemory !== 2) {
    throw new E2EFailure('assert report', `expected two reattributed memory items, got ${report.auditReattributedMemory}`)
  }
  if (report.auditEventKind !== 'speaker' || report.auditEventTitle !== 'Speaker mapping') {
    throw new E2EFailure('assert report', `unexpected audit event ${report.auditEventKind}/${report.auditEventTitle}`)
  }
  if (report.auditEventDetail !== 'SPEAKER_09 mapped to Priya Nair') {
    throw new E2EFailure('assert report', `unexpected audit detail: ${report.auditEventDetail || 'empty'}`)
  }
}

function printReport(report, json) {
  if (json) {
    console.log(JSON.stringify(report, null, 2))
    return
  }
  console.log('Speaker calibration UI E2E passed')
  console.log(`URL: ${report.webUrl}/react?speaker_calibration_e2e=1`)
  console.log(`Workspace: ${report.workspace}`)
  console.log(`Actor: ${report.actorName}`)
  console.log(`Mapping: ${report.mappingUserName}`)
  console.log(`Audit: ${report.auditText}`)
}

async function printFailure(error, logs) {
  console.error(`Speaker calibration UI E2E failed: ${error.message || error}`)
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
    JWT_SECRET: 'ui-speaker-calibration-e2e-secret',
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
      '    return {"service": "speaker-calibration"}',
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
  execFileSync('git', ['config', 'user.name', 'VoiceOps Speaker E2E'], { cwd: workspace, stdio: 'pipe' })
  execFileSync('git', ['add', '.'], { cwd: workspace, stdio: 'pipe' })
  execFileSync('git', ['commit', '-m', 'initial'], { cwd: workspace, stdio: 'pipe' })
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

main()
