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

const browserCaptionText = 'Browser caption says Alice opened app.py before backend attribution'
const expectedTexts = [browserCaptionText, 'Live audio chunk 2']

class E2EFailure extends Error {
  constructor(step, message) {
    super(`${step}: ${message}`)
    this.step = step
  }
}

async function main() {
  const tempRoot = await fs.mkdtemp(path.join(os.tmpdir(), 'voiceops-live-mock-mic-e2e-'))
  const workspace = path.join(tempRoot, 'workspace')
  const logs = { backend: [], frontend: [] }
  const processes = []
  let browser
  let context

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
      args: [
        '--use-fake-device-for-media-stream',
        '--use-fake-ui-for-media-stream',
      ],
    })
    context = await browser.newContext({
      viewport: { width: 1440, height: 1000 },
      permissions: ['microphone'],
    })
    await context.addInitScript(({ captionText }) => {
      window.__voiceopsMockSpeechRecognition = {
        starts: 0,
        aborts: 0,
        emitted: [],
      }

      class FakeSpeechRecognition {
        constructor() {
          this.continuous = false
          this.interimResults = false
          this.lang = 'en-US'
          this.onresult = null
          this.onerror = null
          this.onend = null
          this._active = false
          this._timers = []
        }

        start() {
          if (this._active) return
          this._active = true
          window.__voiceopsMockSpeechRecognition.starts += 1
          this._timers.push(window.setTimeout(() => {
            if (!this._active || typeof this.onresult !== 'function') return
            window.__voiceopsMockSpeechRecognition.emitted.push(captionText)
            const result = {
              0: { transcript: captionText },
              isFinal: false,
              length: 1,
            }
            this.onresult({
              resultIndex: 0,
              results: {
                0: result,
                length: 1,
              },
            })
          }, 150))
        }

        stop() {
          this._finish()
        }

        abort() {
          window.__voiceopsMockSpeechRecognition.aborts += 1
          this._finish()
        }

        _finish() {
          this._active = false
          this._timers.forEach((timer) => window.clearTimeout(timer))
          this._timers = []
          if (typeof this.onend === 'function') this.onend()
        }
      }

      window.SpeechRecognition = FakeSpeechRecognition
      window.webkitSpeechRecognition = FakeSpeechRecognition
    }, { captionText: browserCaptionText })
    const page = await context.newPage()
    await page.goto(`${webUrl}/react?mock_mic_e2e=${Date.now()}`)

    await page.getByRole('button', { name: /^sign in$/i }).click()
    await expect(page.getByRole('heading', { name: 'Team room' })).toBeVisible({ timeout: 30_000 })
    await expect(page.getByRole('button', { name: /start live meeting/i })).toBeVisible({ timeout: 30_000 })

    const browserSupport = await page.evaluate(() => ({
      mediaRecorder: Boolean(window.MediaRecorder),
      getUserMedia: Boolean(navigator.mediaDevices?.getUserMedia),
      instantCaptions: Boolean(window.SpeechRecognition || window.webkitSpeechRecognition),
    }))
    if (!browserSupport.mediaRecorder || !browserSupport.getUserMedia || !browserSupport.instantCaptions) {
      throw new E2EFailure('browser support', `missing media support: ${JSON.stringify(browserSupport)}`)
    }

    await page.getByRole('button', { name: /start live meeting/i }).click()
    await expect(page.getByRole('button', { name: /stop live meeting/i })).toBeVisible({ timeout: 30_000 })
    await expect(page.getByText(browserCaptionText).first()).toBeVisible({ timeout: 15_000 })

    const report = await pollMockMicReport(page)

    await page.getByRole('button', { name: /stop live meeting/i }).click()
    await expect(page.getByRole('button', { name: /start live meeting/i })).toBeVisible({ timeout: 30_000 })

    assertReport(report)
    printReport({ ...report, workspace, webUrl, browserSupport }, asJson)
  } catch (error) {
    await printFailure(error, logs)
    process.exitCode = 1
  } finally {
    if (context) await context.close().catch(() => {})
    if (browser) await browser.close().catch(() => {})
    await Promise.all(processes.map((child) => stopProcess(child)))
    if (!keepWorkspace) await fs.rm(tempRoot, { recursive: true, force: true })
  }
}

async function pollMockMicReport(page) {
  const deadline = Date.now() + 45_000
  let lastReport = null
  while (Date.now() < deadline) {
    lastReport = await readMockMicReport(page)
    if (
      expectedTexts.every((text) => lastReport.liveTexts.includes(text))
      && expectedTexts.every((text) => lastReport.feedText.includes(text))
      && lastReport.speakerLabels.includes('SPEAKER_01')
      && lastReport.speakerLabels.includes('SPEAKER_00')
      && lastReport.liveMessageCount >= 2
      && lastReport.chunksSent >= 2
      && lastReport.latency.hasPanel
      && lastReport.latency.finalChunks >= 2
    ) {
      return lastReport
    }
    await delay(500)
  }
  throw new E2EFailure('poll mock mic report', `timed out waiting for browser media chunks: ${JSON.stringify(lastReport)}`)
}

async function readMockMicReport(page) {
  return page.evaluate(async (captionText) => {
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
    const diagnosticsText = document.querySelector('.live-diagnostics')?.textContent?.trim() || ''
    const chunkMatch = diagnosticsText.match(/Chunks(?:\s+sent)?\s*(\d+)/i)
    const latency = readLatencyPanel()
    return {
      liveMessageCount: liveMessages.length,
      liveTexts,
      speakerLabels,
      provider: speakers.provider || '',
      diagnosticsText,
      latency,
      captionPreviewText: document.querySelector('.caption-preview')?.textContent?.trim() || '',
      feedText: document.querySelector('.feed')?.textContent?.trim() || '',
      draftMessageCount: document.querySelectorAll('.feed .msg.draft').length,
      draftCaptionCount: Array.from(document.querySelectorAll('.feed .msg.draft')).filter((message) => (
        message.textContent?.includes(captionText)
      )).length,
      pttText: document.querySelector('.ptt')?.textContent?.trim() || '',
      chunksSent: chunkMatch ? Number(chunkMatch[1]) : 0,
      unknownLabels: (speakers.unknown_speakers || []).map((item) => item.speaker_label),
      speechRecognition: window.__voiceopsMockSpeechRecognition || null,
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
        bestWarm: rows['Best warm'] || '',
        coldStart: rows['Cold start'] || '',
        finalChunks: Number(rows['Final chunks'] || 0),
      }
    }
  }, browserCaptionText)
}

function assertReport(report) {
  for (const text of expectedTexts) {
    if (!report.liveTexts.includes(text)) {
      throw new E2EFailure('assert report', `missing mock microphone transcript text: ${text}`)
    }
    if (!report.feedText.includes(text)) {
      throw new E2EFailure('assert report', `feed did not render mock microphone text: ${text}`)
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
  if (!report.pttText.includes('Live meeting') && !report.pttText.includes('Streaming room audio')) {
    throw new E2EFailure('assert report', 'push-to-talk surface did not enter live meeting mode')
  }
  if (report.chunksSent < 2) {
    throw new E2EFailure('assert report', `expected at least two browser media chunks, got ${report.chunksSent}`)
  }
  if (!report.speechRecognition || report.speechRecognition.starts < 1) {
    throw new E2EFailure('assert report', `browser instant captions did not start: ${JSON.stringify(report.speechRecognition)}`)
  }
  if (!report.speechRecognition.emitted?.includes(browserCaptionText)) {
    throw new E2EFailure('assert report', `browser instant captions did not emit preview text: ${JSON.stringify(report.speechRecognition)}`)
  }
  if (report.draftCaptionCount > 0) {
    throw new E2EFailure('assert report', 'browser caption draft was not removed after backend speaker attribution')
  }
  if (report.draftMessageCount > 0) {
    throw new E2EFailure('assert report', `expected no remaining draft live messages, got ${report.draftMessageCount}`)
  }
  if (!report.diagnosticsText.includes('Captions')) {
    throw new E2EFailure('assert report', 'live diagnostics did not expose caption state')
  }
  if (!report.latency.hasPanel || !report.diagnosticsText.includes('Live latency')) {
    throw new E2EFailure('assert report', 'live diagnostics did not expose latency panel')
  }
  if (report.latency.finalChunks < 2) {
    throw new E2EFailure('assert report', `expected at least two finalized latency chunks, got ${report.latency.finalChunks}`)
  }
}

function printReport(report, json) {
  if (json) {
    console.log(JSON.stringify(report, null, 2))
    return
  }
  console.log('Live meeting mock microphone E2E passed')
  console.log(`URL: ${report.webUrl}/react?mock_mic_e2e=1`)
  console.log(`Workspace: ${report.workspace}`)
  console.log(`Provider: ${report.provider || 'mock'}`)
  console.log(`Chunks sent: ${report.chunksSent}`)
  console.log(`Latency: ${report.latency.label}; best warm ${report.latency.bestWarm}; final chunks ${report.latency.finalChunks}`)
  console.log(`Browser captions: ${report.speechRecognition?.starts || 0} start(s), ${(report.speechRecognition?.emitted || []).length} preview(s)`)
  console.log(`Speakers: ${report.speakerLabels.join(', ')}`)
  console.log(`Live transcript: ${report.liveTexts.join(' | ')}`)
}

async function printFailure(error, logs) {
  console.error(`Live meeting mock microphone E2E failed: ${error.message || error}`)
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
    SPEAKER_VERIFICATION_PATH: path.join(tempRoot, 'speaker_verification.json'),
    MEMORY_STORE_PATH: path.join(tempRoot, 'memory.json'),
    VOICEOPS_CACHE_PATH: path.join(tempRoot, 'cache.json'),
    JWT_SECRET: 'ui-live-mock-mic-e2e-secret',
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
      '    return {"service": "live-mock-mic"}',
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
  execFileSync('git', ['config', 'user.name', 'VoiceOps Mock Mic E2E'], { cwd: workspace, stdio: 'pipe' })
  execFileSync('git', ['add', '.'], { cwd: workspace, stdio: 'pipe' })
  execFileSync('git', ['commit', '-m', 'initial'], { cwd: workspace, stdio: 'pipe' })
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

main()
