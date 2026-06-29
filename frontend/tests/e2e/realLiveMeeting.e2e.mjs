import { spawnSync } from 'node:child_process'
import path from 'node:path'
import process from 'node:process'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const projectRoot = path.resolve(__dirname, '../../..')
const backendRoot = path.join(projectRoot, 'backend')

const parsed = parseArgs(process.argv.slice(2))

function main() {
  const command = buildCommand(parsed)
  const startedAt = Date.now()
  const result = spawnSync(command[0], command.slice(1), {
    cwd: backendRoot,
    encoding: 'utf8',
    maxBuffer: 1024 * 1024 * 20,
    timeout: (parsed.timeout + 120) * 1000,
  })

  const report = parseJsonOutput(result.stdout)
  if (result.status !== 0 || !report || report.status !== 'passed') {
    printFailure(result, report)
    process.exit(result.status || 1)
  }

  const normalized = normalizeReport(report, command, Date.now() - startedAt)
  if (parsed.json) {
    console.log(JSON.stringify(normalized, null, 2))
    return
  }
  printReport(normalized)
}

function parseArgs(argv) {
  const options = {
    json: false,
    audio: '',
    generateMacosTts: false,
    timeout: 300,
    model: 'tiny',
    device: '',
    computeType: '',
    workerMode: 'persistent_subprocess',
    chunks: 2,
    maxAudioSeconds: 12,
    keepAudio: false,
    evidencePath: process.env.DEMO_EVIDENCE_PATH || '',
  }

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index]
    if (arg === '--json') options.json = true
    else if (arg === '--audio') options.audio = argv[++index] || ''
    else if (arg === '--generate-macos-tts') options.generateMacosTts = true
    else if (arg === '--timeout') options.timeout = Number(argv[++index] || options.timeout)
    else if (arg === '--model') options.model = argv[++index] || options.model
    else if (arg === '--device') options.device = argv[++index] || ''
    else if (arg === '--compute-type') options.computeType = argv[++index] || ''
    else if (arg === '--worker-mode') options.workerMode = argv[++index] || options.workerMode
    else if (arg === '--chunks') options.chunks = Number(argv[++index] || options.chunks)
    else if (arg === '--max-audio-seconds') options.maxAudioSeconds = Number(argv[++index] || options.maxAudioSeconds)
    else if (arg === '--keep-audio') options.keepAudio = true
    else if (arg === '--evidence-path') options.evidencePath = argv[++index] || ''
  }

  if (!options.audio) options.generateMacosTts = true
  options.timeout = Number.isFinite(options.timeout) && options.timeout > 0 ? options.timeout : 300
  options.chunks = Number.isFinite(options.chunks) && options.chunks > 0 ? Math.round(options.chunks) : 2
  options.maxAudioSeconds = Number.isFinite(options.maxAudioSeconds) && options.maxAudioSeconds > 0
    ? options.maxAudioSeconds
    : 12
  return options
}

function buildCommand(options) {
  const command = [
    process.env.PYTHON || 'python',
    'scripts/smoke_live_meeting_real.py',
    '--timeout',
    String(options.timeout),
    '--receive-timeout',
    String(options.timeout + 60),
    '--model',
    options.model,
    '--worker-mode',
    options.workerMode,
    '--chunks',
    String(options.chunks),
    '--max-audio-seconds',
    String(options.maxAudioSeconds),
    '--json',
  ]
  if (options.audio) command.push('--audio', options.audio)
  if (options.generateMacosTts) command.push('--generate-macos-tts')
  if (options.device) command.push('--device', options.device)
  if (options.computeType) command.push('--compute-type', options.computeType)
  if (options.keepAudio) command.push('--keep-audio')
  if (options.evidencePath) command.push('--evidence-path', options.evidencePath)
  return command
}

function normalizeReport(report, command, durationMs) {
  const chunkResults = Array.isArray(report.chunk_results) ? report.chunk_results : []
  const completedLatencies = chunkResults
    .map((chunk) => Number(chunk.elapsed_ms || 0))
    .filter((value) => Number.isFinite(value) && value > 0)
  const coldStartMs = completedLatencies[0] || 0
  const warmLatencies = completedLatencies.slice(1)
  const bestWarmMs = warmLatencies.length ? Math.min(...warmLatencies) : 0
  return {
    status: 'passed',
    provider: report.provider || 'whisperx',
    duration_ms: durationMs,
    command,
    generatedAudio: Boolean(report.generated_audio),
    audioSeconds: report.audio_seconds || null,
    requestedChunks: report.requested_chunks || chunkResults.length,
    completedChunks: report.completed_chunks || chunkResults.length,
    speakerLabels: report.speaker_labels || [],
    distinctSpeakerCount: report.distinct_speaker_count || 0,
    timelineMessageCount: report.timeline_message_count || 0,
    timelineTexts: report.timeline_texts || [],
    partialTexts: report.partial_texts || [],
    chunkResults,
    latency: {
      coldStartMs,
      bestWarmMs,
      completedLatencies,
      warmPathProven: bestWarmMs > 0,
    },
    stages: report.stages || [],
    liveElapsedMs: report.live_elapsed_ms || 0,
  }
}

function parseJsonOutput(output) {
  const text = (output || '').trim()
  if (!text) return null
  try {
    return JSON.parse(text)
  } catch {
    const start = text.find('{')
    const end = text.rfind('}')
    if (start >= 0 && end > start) {
      try {
        return JSON.parse(text.slice(start, end + 1))
      } catch {
        return null
      }
    }
  }
  return null
}

function printFailure(result, report) {
  console.error('Real live meeting E2E failed')
  console.error(`Exit code: ${result.status}`)
  if (report?.error) console.error(`Error: ${report.error}`)
  console.error('stdout:')
  console.error(tail(result.stdout) || '(empty)')
  console.error('stderr:')
  console.error(tail(result.stderr) || '(empty)')
}

function printReport(report) {
  console.log('Real live meeting E2E passed')
  console.log(`Provider: ${report.provider}`)
  console.log(`Speakers: ${report.speakerLabels.join(', ')}`)
  console.log(`Chunks: ${report.completedChunks}/${report.requestedChunks}`)
  console.log(`Latency: cold ${report.latency.coldStartMs}ms; best warm ${report.latency.bestWarmMs || 'pending'}ms`)
  console.log(`Timeline messages: ${report.timelineMessageCount}`)
}

function tail(text, lines = 30) {
  return (text || '').trim().split(/\r?\n/).slice(-lines).join('\n')
}

main()
