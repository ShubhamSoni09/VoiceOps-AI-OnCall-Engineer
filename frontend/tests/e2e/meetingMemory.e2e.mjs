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
  const tempRoot = await fs.mkdtemp(path.join(os.tmpdir(), 'voiceops-meeting-memory-e2e-'))
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
    await page.goto(`${webUrl}/react?meeting_memory_e2e=${Date.now()}`)

    await page.getByRole('button', { name: /^sign in$/i }).click()
    await expect(page.getByRole('heading', { name: 'Team room' })).toBeVisible({ timeout: 30_000 })

    await seedMeetingMemory(page)
    await page.reload()

    await expect(page.getByText('Meeting memory', { exact: true })).toBeVisible({ timeout: 30_000 })
    await expect(page.getByText('app.py').first()).toBeVisible({ timeout: 30_000 })

    await page.getByLabel('Search meeting memory and audit').fill('what files did Priya mention?')
    await page.getByRole('button', { name: 'Search meeting memory' }).click()

    const answer = page.locator('.memory-answer')
    await expect(answer).toContainText('Files mentioned: app.py.', { timeout: 30_000 })
    await expect(page.locator('.memory-answer .rag-trace')).toContainText('provider local_provenance')
    await expect(page.locator('.memory-answer .rag-trace')).toContainText('rag local_sparse')
    await expect(page.locator('.memory-answer .rag-trace')).toContainText('short')
    await expect(page.locator('.memory-answer .rag-trace')).toContainText('long')
    await expect(page.locator('.memory-source-summary')).toContainText('citations')
    await expect(page.locator('.memory-source-summary')).toContainText('candidates')
    await expect(page.locator('.memory-source-summary')).toContainText('docs')
    await expect(page.locator('.citation-list')).toContainText('app.py')
    await expect(page.locator('.citation-list')).toContainText('Priya Nair')
    const report = await page.evaluate(async () => {
      const token = localStorage.getItem('voiceops_token')
      const headers = {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      }
      const response = await fetch('/collab/rooms/main/rag/query', {
        method: 'POST',
        headers,
        body: JSON.stringify({ question: 'what files did Priya mention?', limit: 8 }),
      })
      const data = await response.json()
      const roomResponse = await fetch('/collab/rooms/main', {
        headers: { Authorization: `Bearer ${token}` },
      })
      const room = await roomResponse.json()
      return {
        answer: data.answer || '',
        mode: data.mode || '',
        citationSources: (data.citations || []).map((citation) => citation.source),
        citationActors: (data.citations || []).map((citation) => citation.actor_name),
        citationTexts: (data.citations || []).map((citation) => citation.excerpt),
        pageAnswer: document.querySelector('.memory-answer')?.textContent?.trim() || '',
        ragIndexStatus: document.querySelector('.rag-index-status')?.textContent?.trim() || '',
        ragTrace: document.querySelector('.memory-answer .rag-trace')?.textContent?.trim() || '',
        visibleCitations: [...document.querySelectorAll('.citation-row')]
          .map((node) => node.textContent?.trim() || '')
          .filter(Boolean),
        visibleItems: [...document.querySelectorAll('.memory-item')]
          .map((node) => node.textContent?.trim() || '')
          .filter(Boolean),
        roomMemoryCount: (room.memory || []).length,
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

async function seedMeetingMemory(page) {
  await page.evaluate(async () => {
    const token = localStorage.getItem('voiceops_token')
    const headers = {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
    }
    const messages = [
      'We decided to keep the patch small in app.py',
      'Need to follow up on the open rollback question?',
    ]
    for (const text of messages) {
      const response = await fetch('/collab/rooms/main/messages', {
        method: 'POST',
        headers,
        body: JSON.stringify({ text, source: 'meeting_audio' }),
      })
      if (!response.ok) {
        throw new Error(`message ingest failed: ${response.status}`)
      }
    }
  })
}

function assertReport(report) {
  if (report.mode !== 'local_hybrid') {
    throw new E2EFailure('assert report', `expected local_hybrid mode, got ${report.mode || 'empty'}`)
  }
  if (!report.answer.includes('app.py')) {
    throw new E2EFailure('assert report', `expected answer to mention app.py, got ${report.answer || 'empty'}`)
  }
  if (!report.pageAnswer.includes('Files mentioned: app.py.')) {
    throw new E2EFailure('assert report', `expected page answer to show app.py, got ${report.pageAnswer || 'empty'}`)
  }
  if (
    !report.ragTrace.includes('provider local_provenance')
    || !report.ragTrace.includes('rag local_sparse')
    || !report.ragTrace.includes('short')
    || !report.ragTrace.includes('long')
  ) {
    throw new E2EFailure('assert report', `expected visible RAG trace, got ${report.ragTrace || 'empty'}`)
  }
  if (!report.citationSources.includes('memory')) {
    throw new E2EFailure('assert report', `expected memory citation, got ${report.citationSources.join(', ')}`)
  }
  if (!report.citationActors.includes('Priya Nair')) {
    throw new E2EFailure('assert report', `expected Priya Nair citation actor, got ${report.citationActors.join(', ')}`)
  }
  if (!report.citationTexts.includes('app.py')) {
    throw new E2EFailure('assert report', `expected app.py citation excerpt, got ${report.citationTexts.join(', ')}`)
  }
  if (!report.visibleCitations.some((item) => item.includes('Priya Nair') && item.includes('app.py'))) {
    throw new E2EFailure('assert report', 'visible citation list did not include Priya Nair app.py source')
  }
  if (new Set(report.visibleCitations).size !== report.visibleCitations.length) {
    throw new E2EFailure('assert report', `visible citation list contains duplicates: ${report.visibleCitations.join(' | ')}`)
  }
  if (!report.visibleItems.some((item) => item.includes('code reference') && item.includes('app.py'))) {
    throw new E2EFailure('assert report', 'visible memory list did not include app.py code reference')
  }
  if (report.roomMemoryCount < 2) {
    throw new E2EFailure('assert report', `expected room memory items, got ${report.roomMemoryCount}`)
  }
}

function printReport(report, json) {
  if (json) {
    console.log(JSON.stringify(report, null, 2))
    return
  }
  console.log('Meeting memory UI E2E passed')
  console.log(`URL: ${report.webUrl}/react?meeting_memory_e2e=1`)
  console.log(`Workspace: ${report.workspace}`)
  console.log(`Answer: ${report.answer}`)
  console.log(`Mode: ${report.mode}`)
  console.log(`Raw citations: ${report.citationTexts.join(', ')}`)
  console.log(`Visible citations: ${report.visibleCitations.join(' | ')}`)
}

async function printFailure(error, logs) {
  console.error(`Meeting memory UI E2E failed: ${error.message || error}`)
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
    RAG_INDEX_PATH: path.join(tempRoot, 'rag-index.json'),
    VOICEOPS_CACHE_PATH: path.join(tempRoot, 'cache.json'),
    JWT_SECRET: 'ui-meeting-memory-e2e-secret',
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
      '    return {"service": "meeting-memory"}',
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
  execFileSync('git', ['config', 'user.name', 'VoiceOps Memory E2E'], { cwd: workspace, stdio: 'pipe' })
  execFileSync('git', ['add', '.'], { cwd: workspace, stdio: 'pipe' })
  execFileSync('git', ['commit', '-m', 'initial'], { cwd: workspace, stdio: 'pipe' })
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

main()
