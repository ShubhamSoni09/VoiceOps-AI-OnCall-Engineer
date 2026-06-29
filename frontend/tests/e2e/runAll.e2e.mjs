import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import process from 'node:process'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const frontendRoot = path.resolve(__dirname, '../..')
const projectRoot = path.resolve(frontendRoot, '..')

const args = new Set(process.argv.slice(2))
const asJson = args.has('--json')
const keepWorkspace = args.has('--keep-workspace')
const requireRealLive = args.has('--real-live')
const requireRealBrowserLive = args.has('--real-browser-live')
const evidencePath = optionValue(process.argv.slice(2), '--evidence-path')
  || process.env.DEMO_EVIDENCE_PATH
  || path.join(projectRoot, 'backend', 'data', 'demo_evidence.json')

const checks = [
  {
    name: 'live meeting smoke',
    script: 'liveMeetingSmoke.e2e.mjs',
    summarize: (report) => ({
      provider: report.provider || 'mock',
      speakerLabels: report.speakerLabels,
      liveTexts: report.liveTexts,
      liveMessageCount: report.liveMessageCount,
      agentMessageCount: report.agentMessageCount,
      agentSources: report.agentSources,
      agentTexts: report.agentTexts,
      agentActorNames: report.agentActorNames,
      targetClosure: report.targetClosureText,
      latency: report.latency,
      layout: report.layout,
    }),
  },
  {
    name: 'live mock microphone',
    script: 'liveMeetingMockMic.e2e.mjs',
    summarize: (report) => ({
      provider: report.provider || 'mock',
      speakerLabels: report.speakerLabels,
      liveTexts: report.liveTexts,
      chunksSent: report.chunksSent,
      liveMessageCount: report.liveMessageCount,
      latency: report.latency,
      browserCaptionStarts: report.speechRecognition?.starts || 0,
      browserCaptionPreviews: report.speechRecognition?.emitted?.length || 0,
    }),
  },
  {
    name: 'meeting memory',
    script: 'meetingMemory.e2e.mjs',
    summarize: (report) => ({
      answer: report.answer,
      mode: report.mode,
      itemTexts: report.itemTexts,
      actor: report.itemActors?.[0] || '',
    }),
  },
  {
    name: 'speaker calibration',
    script: 'speakerCalibration.e2e.mjs',
    summarize: (report) => ({
      actor: report.actorName,
      mapping: report.mappingUserName,
      audit: report.auditText,
      reattributedMessages: report.auditReattributedMessages,
      reattributedMemory: report.auditReattributedMemory,
    }),
  },
  {
    name: 'generated speaker verification',
    script: 'generatedSpeakerVerification.e2e.mjs',
    summarize: (report) => ({
      generatedRequests: report.generatedRequests,
      uploadRequests: report.uploadRequests,
      generatedPanel: report.generatedVerificationText,
      uploadPanel: report.uploadVerificationText,
    }),
  },
  {
    name: 'meeting closure',
    script: 'meetingClosure.e2e.mjs',
    summarize: (report) => ({
      actionId: report.actionId,
      branch: report.branch,
      approver: report.approver,
      filesChanged: report.filesChanged,
      testsPassed: report.testsPassed,
    }),
  },
  {
    name: 'agent controls',
    script: 'agentControls.e2e.mjs',
    summarize: (report) => ({
      runId: report.runId,
      runStatus: report.status,
      findingTitle: report.findingTitle,
      externalAssignmentAgent: report.assignment?.agentId,
      externalAssignmentModel: report.assignment?.model,
      externalCliTemplate: report.assignment?.providerCommandTemplate,
      desktopOverflowX: report.layout?.desktopOverflowX,
      mobileOverflowX: report.layout?.mobileOverflowX,
    }),
  },
  {
    name: 'external agent patch closure',
    script: 'externalAgentPatchClosure.e2e.mjs',
    summarize: (report) => ({
      actionId: report.actionId,
      branch: report.branch,
      provider: report.externalAgent?.provider,
      model: report.externalAgent?.model,
      runtime: report.runtime?.execution_mode,
      filesChanged: report.filesChanged,
      desktopOverflowX: report.layout?.desktopOverflowX,
      mobileOverflowX: report.layout?.mobileOverflowX,
    }),
  },
]

if (requireRealLive) {
  checks.push({
    name: 'real live meeting',
    script: 'realLiveMeeting.e2e.mjs',
    args: realLiveArgs(process.argv.slice(2)),
    summarize: (report) => ({
      provider: report.provider,
      speakerLabels: report.speakerLabels,
      distinctSpeakerCount: report.distinctSpeakerCount,
      requestedChunks: report.requestedChunks,
      completedChunks: report.completedChunks,
      timelineMessageCount: report.timelineMessageCount,
      latency: report.latency,
      liveElapsedMs: report.liveElapsedMs,
    }),
  })
}

if (requireRealBrowserLive) {
  checks.push({
    name: 'real browser live meeting',
    script: 'liveMeetingRealMic.e2e.mjs',
    args: realBrowserLiveArgs(process.argv.slice(2)),
    summarize: (report) => ({
      provider: report.provider,
      speakerLabels: report.speakerLabels,
      chunksSent: report.chunksSent,
      liveMessageCount: report.liveMessageCount,
      latency: report.latency,
      liveTexts: report.liveTexts,
      captionPreviewStarts: report.speechRecognition?.starts || 0,
    }),
  })
}

function main() {
  const startedAt = Date.now()
  const results = []

  for (const check of checks) {
    const report = runCheck(check)
    results.push({
      name: check.name,
      status: 'passed',
      duration_ms: report.duration_ms,
      ...check.summarize(report.data),
    })
  }

  const summary = {
    status: 'ready',
    duration_ms: Date.now() - startedAt,
    checks: results,
  }
  writeEvidenceRecord(mockEvidenceRecord(summary))

  if (asJson) {
    console.log(JSON.stringify(summary, null, 2))
    return
  }

  printReport(summary)
}

function mockEvidenceRecord(summary) {
  const live = summary.checks.find((check) => check.name === 'live meeting smoke') || {}
  const closure = summary.checks.find((check) => check.name === 'meeting closure') || {}
  const agentControls = summary.checks.find((check) => check.name === 'agent controls') || {}
  const externalPatch = summary.checks.find((check) => check.name === 'external agent patch closure') || {}
  return {
    id: 'mock_e2e',
    label: 'Mock closure harness',
    status: 'passed',
    checked_at: new Date().toISOString(),
    source: 'frontend_run_all_e2e',
    provider: live.provider || 'mock',
    detail: 'Mock browser E2E completed live meeting, memory, calibration, speaker verification UI, and meeting closure gates.',
    duration_ms: summary.duration_ms,
    command: ['npm', 'run', 'e2e:all', '--', '--json'],
    speaker_labels: live.speakerLabels || [],
    distinct_speaker_count: (live.speakerLabels || []).length,
    requested_chunks: live.latency?.finalChunks || null,
    completed_chunks: live.latency?.finalChunks || null,
    timeline_message_count: live.liveMessageCount || null,
    latency: live.latency || {},
    metrics: {
      checks_passed: summary.checks.length,
      agent_message_count: live.agentMessageCount || 0,
      closure_tests_passed: Boolean(closure.testsPassed),
      closure_branch: closure.branch || '',
      closure_action_id: closure.actionId || '',
      agent_controls_cancelled: agentControls.runStatus === 'cancelled',
      external_assignment_model: agentControls.externalAssignmentModel || '',
      external_patch_branch: externalPatch.branch || '',
      external_patch_runtime: externalPatch.runtime || '',
    },
  }
}

function writeEvidenceRecord(record) {
  fs.mkdirSync(path.dirname(evidencePath), { recursive: true })
  let records = []
  try {
    const raw = JSON.parse(fs.readFileSync(evidencePath, 'utf8'))
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
  fs.writeFileSync(
    evidencePath,
    `${JSON.stringify({ checked_at: new Date().toISOString(), records: nextRecords }, null, 2)}\n`,
    'utf8',
  )
}

function runCheck(check) {
  const startedAt = Date.now()
  const childArgs = [path.join(__dirname, check.script), '--json']
  if (keepWorkspace) childArgs.push('--keep-workspace')
  if (check.args?.length) childArgs.push(...check.args)

  const result = spawnSync(process.execPath, childArgs, {
    cwd: frontendRoot,
    encoding: 'utf8',
    maxBuffer: 1024 * 1024 * 20,
  })

  if (result.status !== 0) {
    printFailedCheck(check, result)
    process.exit(result.status || 1)
  }

  return {
    data: parseJsonOutput(check, result.stdout),
    duration_ms: Date.now() - startedAt,
  }
}

function parseJsonOutput(check, output) {
  const text = output.trim()
  if (!text) {
    throw new Error(`${check.name} produced empty JSON output`)
  }
  try {
    return JSON.parse(text)
  } catch (error) {
    console.error(`${check.name} produced invalid JSON output`)
    console.error(text)
    throw error
  }
}

function printFailedCheck(check, result) {
  console.error(`Demo readiness failed at: ${check.name}`)
  console.error(`Exit code: ${result.status}`)
  console.error('stdout:')
  console.error(result.stdout.trim() || '(empty)')
  console.error('stderr:')
  console.error(result.stderr.trim() || '(empty)')
}

function printReport(summary) {
  console.log('Demo readiness E2E passed')
  console.log(`Duration: ${(summary.duration_ms / 1000).toFixed(1)}s`)
  for (const check of summary.checks) {
    if (check.name === 'live meeting smoke') {
      console.log(`- Live meeting smoke: ${check.liveMessageCount} live messages, ${check.agentMessageCount} agent response via ${check.provider}`)
      console.log(`  Speakers: ${(check.speakerLabels || []).join(', ')}`)
      console.log(`  Agent: ${(check.agentActorNames || []).join(', ')} - ${(check.agentTexts || []).join(' | ')}`)
      console.log(`  Latency: ${check.latency?.label || 'missing'}; best warm ${check.latency?.bestWarm || 'missing'}`)
    } else if (check.name === 'live mock microphone') {
      console.log(`- Live mock microphone: ${check.chunksSent} browser chunks, ${check.liveMessageCount} live messages via ${check.provider}`)
      console.log(`  Browser captions: ${check.browserCaptionStarts} start(s), ${check.browserCaptionPreviews} preview(s)`)
      console.log(`  Speakers: ${(check.speakerLabels || []).join(', ')}`)
      console.log(`  Latency: ${check.latency?.label || 'missing'}; final chunks ${check.latency?.finalChunks || 0}`)
    } else if (check.name === 'meeting memory') {
      console.log(`- Meeting memory: ${check.answer} (${check.actor}, ${check.mode})`)
    } else if (check.name === 'speaker calibration') {
      console.log(`- Speaker calibration: ${check.mapping}; ${check.reattributedMessages} message, ${check.reattributedMemory} memory items`)
    } else if (check.name === 'generated speaker verification') {
      console.log(`- Generated speaker verification: ${check.generatedRequests} generated request(s), ${check.uploadRequests} upload request(s)`)
    } else if (check.name === 'meeting closure') {
      console.log(`- Meeting closure: ${check.actionId} approved by ${check.approver} on ${check.branch}`)
      console.log(`  Files: ${(check.filesChanged || []).join(', ')}; tests passed: ${check.testsPassed}`)
    } else if (check.name === 'agent controls') {
      console.log(`- Agent controls: ${check.runId} ${check.status}; ${check.findingTitle}`)
    } else if (check.name === 'real live meeting') {
      console.log(`- Real live meeting: ${check.completedChunks}/${check.requestedChunks} chunks via ${check.provider}`)
      console.log(`  Speakers: ${(check.speakerLabels || []).join(', ')}`)
      console.log(`  Latency: cold ${check.latency?.coldStartMs || 0}ms; best warm ${check.latency?.bestWarmMs || 'pending'}ms`)
    } else if (check.name === 'real browser live meeting') {
      console.log(`- Real browser live meeting: ${check.chunksSent} browser chunks, ${check.liveMessageCount} live messages via ${check.provider}`)
      console.log(`  Speakers: ${(check.speakerLabels || []).join(', ')}`)
      console.log(`  Latency: ${check.latency?.label || 'missing'}; final chunks ${check.latency?.finalChunks || 0}`)
    }
  }
}

function realLiveArgs(argv) {
  const passthrough = []
  const valueOptions = new Set([
    '--real-live-audio',
    '--real-live-timeout',
    '--real-live-model',
    '--real-live-device',
    '--real-live-compute-type',
    '--real-live-worker-mode',
    '--real-live-chunks',
    '--real-live-max-audio-seconds',
    '--evidence-path',
  ])
  const flagMap = new Map([
    ['--real-live-generate-macos-tts', '--generate-macos-tts'],
    ['--real-live-keep-audio', '--keep-audio'],
  ])
  const valueMap = new Map([
    ['--real-live-audio', '--audio'],
    ['--real-live-timeout', '--timeout'],
    ['--real-live-model', '--model'],
    ['--real-live-device', '--device'],
    ['--real-live-compute-type', '--compute-type'],
    ['--real-live-worker-mode', '--worker-mode'],
    ['--real-live-chunks', '--chunks'],
    ['--real-live-max-audio-seconds', '--max-audio-seconds'],
    ['--evidence-path', '--evidence-path'],
  ])

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index]
    if (flagMap.has(arg)) {
      passthrough.push(flagMap.get(arg))
      continue
    }
    if (valueOptions.has(arg)) {
      const value = argv[index + 1]
      if (value && !value.startsWith('--')) {
        passthrough.push(valueMap.get(arg), value)
        index += 1
      }
    }
  }
  return passthrough
}

function optionValue(argv, option) {
  const index = argv.indexOf(option)
  if (index < 0) return ''
  const value = argv[index + 1]
  return value && !value.startsWith('--') ? value : ''
}

function realBrowserLiveArgs(argv) {
  const passthrough = []
  const valueOptions = new Set([
    '--real-browser-live-timeout',
    '--real-browser-live-model',
    '--real-browser-live-device',
    '--real-browser-live-compute-type',
    '--real-browser-live-worker-mode',
    '--real-browser-live-chunks',
    '--evidence-path',
  ])
  const flagMap = new Map([
    ['--real-browser-live-keep-workspace', '--keep-workspace'],
  ])
  const valueMap = new Map([
    ['--real-browser-live-timeout', '--timeout'],
    ['--real-browser-live-model', '--model'],
    ['--real-browser-live-device', '--device'],
    ['--real-browser-live-compute-type', '--compute-type'],
    ['--real-browser-live-worker-mode', '--worker-mode'],
    ['--real-browser-live-chunks', '--chunks'],
    ['--evidence-path', '--evidence-path'],
  ])

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index]
    if (flagMap.has(arg)) {
      passthrough.push(flagMap.get(arg))
      continue
    }
    if (valueOptions.has(arg)) {
      const value = argv[index + 1]
      if (value && !value.startsWith('--')) {
        passthrough.push(valueMap.get(arg), value)
        index += 1
      }
    }
  }
  return passthrough
}

main()
