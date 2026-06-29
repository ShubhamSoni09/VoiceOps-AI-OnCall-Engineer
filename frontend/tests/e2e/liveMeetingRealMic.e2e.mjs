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

const options = parseArgs(process.argv.slice(2))

class E2EFailure extends Error {
  constructor(step, message) {
    super(`${step}: ${message}`)
    this.step = step
  }
}

async function main() {
  const tempRoot = await fs.mkdtemp(path.join(os.tmpdir(), 'voiceops-real-mic-e2e-'))
  const workspace = path.join(tempRoot, 'workspace')
  const audioPath = path.join(tempRoot, 'browser-real-meeting.wav')
  const logs = { backend: [], frontend: [] }
  const processes = []
  let browser
  let context
  let latestReport = null

  try {
    await writeWorkspace(workspace)
    initGit(workspace)
    generateBrowserAudio(audioPath)

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
        `--use-file-for-fake-audio-capture=${audioPath}`,
      ],
    })
    context = await browser.newContext({
      viewport: { width: 1440, height: 1000 },
      permissions: ['microphone'],
    })
    await context.addInitScript(() => {
      window.__voiceopsRealMicCaption = { starts: 0, emitted: [] }
      class FakeSpeechRecognition {
        constructor() {
          this.continuous = false
          this.interimResults = false
          this.lang = 'en-US'
          this.onresult = null
          this.onend = null
          this._timer = null
          this._active = false
        }

        start() {
          if (this._active) return
          this._active = true
          window.__voiceopsRealMicCaption.starts += 1
          this._timer = window.setTimeout(() => {
            if (!this._active || typeof this.onresult !== 'function') return
            const text = 'Browser preview: Alice and Bob are speaking about app.py'
            window.__voiceopsRealMicCaption.emitted.push(text)
            this.onresult({
              resultIndex: 0,
              results: {
                0: {
                  0: { transcript: text },
                  isFinal: false,
                  length: 1,
                },
                length: 1,
              },
            })
          }, 200)
        }

        stop() {
          this._finish()
        }

        abort() {
          this._finish()
        }

        _finish() {
          this._active = false
          if (this._timer) window.clearTimeout(this._timer)
          this._timer = null
          if (typeof this.onend === 'function') this.onend()
        }
      }
      window.SpeechRecognition = FakeSpeechRecognition
      window.webkitSpeechRecognition = FakeSpeechRecognition
    })

    const page = await context.newPage()
    await page.goto(`${webUrl}/react?real_mic_e2e=${Date.now()}&live_timeslice_ms=7000`)
    await page.getByRole('button', { name: /^sign in$/i }).click()
    await expect(page.getByRole('heading', { name: 'Team room' })).toBeVisible({ timeout: 30_000 })
    await expect(page.getByText('Demo readiness')).toBeVisible({ timeout: 30_000 })

    await page.getByRole('button', { name: /start live meeting/i }).click()
    await expect(page.getByRole('button', { name: /stop live meeting/i })).toBeVisible({ timeout: 30_000 })
    await expect(page.getByText('Browser preview: Alice and Bob are speaking about app.py').first()).toBeVisible({ timeout: 30_000 })

    const report = await pollRealMicReport(page)
    latestReport = report

    await page.getByRole('button', { name: /stop live meeting/i }).click()
    await expect(page.getByRole('button', { name: /start live meeting/i })).toBeVisible({ timeout: 30_000 })

    assertReport(report)
    await writeEvidenceRecord(realBrowserEvidenceRecord(report, 'passed'))
    printReport({ ...report, workspace, webUrl, audioPath }, options.json)
  } catch (error) {
    await writeEvidenceRecord(realBrowserEvidenceRecord(latestReport, 'failed', error.message || String(error))).catch(() => {})
    await printFailure(error, logs)
    process.exitCode = 1
  } finally {
    if (context) await context.close().catch(() => {})
    if (browser) await browser.close().catch(() => {})
    await Promise.all(processes.map((child) => stopProcess(child)))
    if (!options.keepWorkspace) await fs.rm(tempRoot, { recursive: true, force: true })
  }
}

function parseArgs(argv) {
  const parsed = {
    json: false,
    keepWorkspace: false,
    timeout: 300,
    model: 'tiny',
    device: '',
    computeType: '',
    workerMode: 'persistent_subprocess',
    chunks: 1,
    evidencePath: process.env.DEMO_EVIDENCE_PATH || path.join(backendRoot, 'data', 'demo_evidence.json'),
  }
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index]
    if (arg === '--json') parsed.json = true
    else if (arg === '--keep-workspace') parsed.keepWorkspace = true
    else if (arg === '--timeout') parsed.timeout = Number(argv[++index] || parsed.timeout)
    else if (arg === '--model') parsed.model = argv[++index] || parsed.model
    else if (arg === '--device') parsed.device = argv[++index] || ''
    else if (arg === '--compute-type') parsed.computeType = argv[++index] || ''
    else if (arg === '--worker-mode') parsed.workerMode = argv[++index] || parsed.workerMode
    else if (arg === '--chunks') parsed.chunks = Number(argv[++index] || parsed.chunks)
    else if (arg === '--evidence-path') parsed.evidencePath = argv[++index] || parsed.evidencePath
  }
  parsed.timeout = Number.isFinite(parsed.timeout) && parsed.timeout > 0 ? parsed.timeout : 300
  parsed.chunks = Number.isFinite(parsed.chunks) && parsed.chunks > 0 ? Math.round(parsed.chunks) : 1
  return parsed
}

function realBrowserEvidenceRecord(report, status, error = '') {
  const latency = report?.latency || {}
  return {
    id: 'real_browser_live',
    label: 'Real browser mic live',
    status,
    checked_at: new Date().toISOString(),
    source: 'frontend_live_meeting_real_mic_e2e',
    provider: report?.provider || 'whisperx',
    detail: status === 'passed'
      ? 'Chromium fake microphone reached MediaRecorder, backend WhisperX, and the React timeline.'
      : error || 'Real browser microphone E2E failed.',
    error: status === 'passed' ? null : error || 'Real browser microphone E2E failed.',
    command: [
      'npm',
      'run',
      'e2e:real-mic',
      '--',
      '--timeout',
      String(options.timeout),
      '--worker-mode',
      options.workerMode,
    ],
    speaker_labels: report?.speakerLabels || [],
    distinct_speaker_count: (report?.speakerLabels || []).length,
    requested_chunks: options.chunks,
    completed_chunks: Number(latency.finalChunks || 0),
    timeline_message_count: report?.liveMessageCount || 0,
    latency: {
      label: latency.label || '',
      cold_start: latency.coldStart || '',
      best_warm: latency.bestWarm || '',
      final_chunks: Number(latency.finalChunks || 0),
    },
    metrics: {
      chunks_sent: report?.chunksSent || 0,
      browser_caption_starts: report?.speechRecognition?.starts || 0,
      feed_contains_alice: Boolean(report?.feedText?.toLowerCase().includes('alice')),
      feed_contains_bob: Boolean(report?.feedText?.toLowerCase().includes('bob')),
    },
  }
}

async function writeEvidenceRecord(record) {
  if (!options.evidencePath) return
  await fs.mkdir(path.dirname(options.evidencePath), { recursive: true })
  let records = []
  try {
    const raw = JSON.parse(await fs.readFile(options.evidencePath, 'utf8'))
    if (Array.isArray(raw.records)) records = raw.records
  } catch {
    records = []
  }
  const merged = new Map(records.filter((item) => item?.id).map((item) => [item.id, item]))
  merged.set(record.id, record)
  const order = { mock_e2e: 10, real_live_backend: 20, real_browser_live: 30 }
  const nextRecords = [...merged.values()].sort((a, b) => (
    (order[a.id] || 100) - (order[b.id] || 100) || String(a.id).localeCompare(String(b.id))
  ))
  await fs.writeFile(
    options.evidencePath,
    `${JSON.stringify({ checked_at: new Date().toISOString(), records: nextRecords }, null, 2)}\n`,
    'utf8',
  )
}

async function pollRealMicReport(page) {
  const deadline = Date.now() + (options.timeout + 90) * 1000
  let lastReport = null
  let lastProgressAt = 0
  while (Date.now() < deadline) {
    lastReport = await readRealMicReport(page)
    if (Date.now() - lastProgressAt > 15_000) {
      lastProgressAt = Date.now()
      console.error(
        `real mic progress: provider=${lastReport.provider || 'none'} chunks=${lastReport.chunksSent} messages=${lastReport.liveMessageCount} labels=${lastReport.speakerLabels.join(',') || 'none'} latency=${lastReport.latency.label || 'none'}`,
      )
    }
    if (
      lastReport.provider === 'whisperx'
      && lastReport.chunksSent >= options.chunks
      && lastReport.liveMessageCount >= 2
      && lastReport.speakerLabels.includes('SPEAKER_00')
      && lastReport.speakerLabels.includes('SPEAKER_01')
      && lastReport.latency.hasPanel
      && lastReport.latency.finalChunks >= options.chunks
      && lastReport.feedText.toLowerCase().includes('alice')
      && lastReport.feedText.toLowerCase().includes('bob')
    ) {
      return lastReport
    }
    await delay(750)
  }
  throw new E2EFailure('poll real mic report', `timed out waiting for real browser WhisperX UI data: ${JSON.stringify(lastReport)}`)
}

async function readRealMicReport(page) {
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
    const diagnosticsText = document.querySelector('.live-diagnostics')?.textContent?.trim() || ''
    const chunkMatch = diagnosticsText.match(/Chunks(?:\s+sent)?\s*(\d+)/i)
    const feedText = document.querySelector('.feed')?.textContent?.trim() || ''
    const liveTexts = liveMessages.map((message) => message.text).filter(Boolean)
    const speakerLabels = [...new Set(liveMessages.map((message) => (
      message.speaker_label || message.metadata?.speaker_label || message.metadata?.raw_speaker_label
    )).filter(Boolean))]
    const latency = readLatencyPanel()
    return {
      provider: speakers.provider || '',
      liveMessageCount: liveMessages.length,
      liveTexts,
      speakerLabels,
      diagnosticsText,
      latency,
      feedText,
      pttText: document.querySelector('.ptt')?.textContent?.trim() || '',
      captionPreviewText: document.querySelector('.caption-preview')?.textContent?.trim() || '',
      chunksSent: chunkMatch ? Number(chunkMatch[1]) : 0,
      speechRecognition: window.__voiceopsRealMicCaption || null,
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
  })
}

function assertReport(report) {
  if (report.provider !== 'whisperx') {
    throw new E2EFailure('assert report', `expected whisperx provider, got ${report.provider || 'none'}`)
  }
  for (const label of ['SPEAKER_00', 'SPEAKER_01']) {
    if (!report.speakerLabels.includes(label)) {
      throw new E2EFailure('assert report', `missing real speaker label ${label}; got ${report.speakerLabels.join(', ')}`)
    }
  }
  if (report.chunksSent < options.chunks) {
    throw new E2EFailure('assert report', `expected at least ${options.chunks} browser chunks, got ${report.chunksSent}`)
  }
  if (report.liveMessageCount < 2) {
    throw new E2EFailure('assert report', `expected real WhisperX timeline messages, got ${report.liveMessageCount}`)
  }
  const lowerFeed = report.feedText.toLowerCase()
  if (!lowerFeed.includes('alice') || !lowerFeed.includes('bob')) {
    throw new E2EFailure('assert report', `feed did not render both real speakers: ${report.feedText}`)
  }
  if (!report.latency.hasPanel || report.latency.finalChunks < options.chunks) {
    throw new E2EFailure('assert report', `latency panel did not finalize enough real chunks: ${JSON.stringify(report.latency)}`)
  }
  if (!report.speechRecognition || report.speechRecognition.starts < 1) {
    throw new E2EFailure('assert report', 'browser caption preview did not start')
  }
}

function generateBrowserAudio(outputPath) {
  const say = which('say')
  const ffmpeg = which('ffmpeg')
  if (!say) throw new E2EFailure('generate audio', 'macOS say command is required for real browser mic smoke')
  if (!ffmpeg) throw new E2EFailure('generate audio', 'ffmpeg is required for real browser mic smoke')

  const dir = path.dirname(outputPath)
  const alice = path.join(dir, 'alice.aiff')
  const bob = path.join(dir, 'bob.aiff')
  execFileSync(say, ['-v', 'Samantha', '-o', alice, 'Alice says the live browser microphone should mention app.py.'], { stdio: 'pipe' })
  execFileSync(say, ['-v', 'Daniel', '-o', bob, 'Bob says the real WhisperX labels should appear in the UI.'], { stdio: 'pipe' })
  execFileSync(
    ffmpeg,
    [
      '-hide_banner',
      '-loglevel',
      'error',
      '-y',
      '-i',
      alice,
      '-i',
      bob,
      '-filter_complex',
      '[0:a][1:a]concat=n=2:v=0:a=1',
      '-ar',
      '16000',
      '-ac',
      '1',
      outputPath,
    ],
    { stdio: 'pipe' },
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
    SPEAKER_VERIFICATION_PATH: path.join(tempRoot, 'speaker_verification.json'),
    MEMORY_STORE_PATH: path.join(tempRoot, 'memory.json'),
    VOICEOPS_CACHE_PATH: path.join(tempRoot, 'cache.json'),
    JWT_SECRET: 'ui-real-mic-e2e-secret',
    LLM_PROVIDER: 'mock',
    STT_PROVIDER: 'mock',
    TTS_PROVIDER: 'mock',
    SPEAKER_PROVIDER: 'whisperx',
    WHISPERX_MODEL: options.model,
    WHISPERX_WORKER_MODE: options.workerMode,
    WHISPERX_WORKER_TIMEOUT_SECONDS: String(options.timeout),
    LIVE_PROCESSING_TIMEOUT_SECONDS: String(options.timeout + 30),
    WHISPERX_MIN_SPEAKERS: '2',
    WHISPERX_MAX_SPEAKERS: '2',
    VOICEOPS_WORKSPACE: workspace,
  }
  if (options.device) env.WHISPERX_DEVICE = options.device
  if (options.computeType) env.WHISPERX_COMPUTE_TYPE = options.computeType
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

function startProcess(command, argsList, processOptions, logBuffer) {
  const child = spawn(command, argsList, {
    ...processOptions,
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
  if (logBuffer.length > 120) logBuffer.splice(0, logBuffer.length - 120)
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
      '    return {"service": "real-mic"}',
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
  execFileSync('git', ['config', 'user.name', 'VoiceOps Real Mic E2E'], { cwd: workspace, stdio: 'pipe' })
  execFileSync('git', ['add', '.'], { cwd: workspace, stdio: 'pipe' })
  execFileSync('git', ['commit', '-m', 'initial'], { cwd: workspace, stdio: 'pipe' })
}

function which(binary) {
  try {
    return execFileSync('which', [binary], { encoding: 'utf8' }).trim()
  } catch {
    return ''
  }
}

function printReport(report, json) {
  if (json) {
    console.log(JSON.stringify(report, null, 2))
    return
  }
  console.log('Live meeting real browser microphone E2E passed')
  console.log(`URL: ${report.webUrl}/react?real_mic_e2e=1`)
  console.log(`Workspace: ${report.workspace}`)
  console.log(`Provider: ${report.provider}`)
  console.log(`Chunks sent: ${report.chunksSent}`)
  console.log(`Latency: ${report.latency.label}; best warm ${report.latency.bestWarm}; final chunks ${report.latency.finalChunks}`)
  console.log(`Speakers: ${report.speakerLabels.join(', ')}`)
  console.log(`Live transcript: ${report.liveTexts.join(' | ')}`)
}

async function printFailure(error, logs) {
  console.error(`Live meeting real browser microphone E2E failed: ${error.message || error}`)
  if (error.step) console.error(`Failed step: ${error.step}`)
  console.error('Backend log tail:')
  console.error(logs.backend.slice(-40).join('').trim() || '(empty)')
  console.error('Frontend log tail:')
  console.error(logs.frontend.slice(-30).join('').trim() || '(empty)')
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

main()
