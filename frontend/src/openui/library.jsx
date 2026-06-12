/**
 * VoiceOps OpenUI component library.
 *
 * This is how we satisfy the OpenUI (thesysdev) sponsor requirement: instead of
 * pulling in @openuidev/react-ui's React-19 chat kit, we register OUR OWN design
 * system as an OpenUI Lang component library. The OpenUI runtime (@openuidev/
 * react-lang) parses OpenUI Lang and renders it through these components, so the
 * generated UI looks exactly like the rest of VoiceOps.
 *
 * Component contract (react-lang 0.2.x): each renderer receives
 *   { props, renderNode, statementId }
 * where `props` is the validated Zod object and `renderNode(value)` renders a
 * nested parsed element node (or array of them).
 *
 * When a real LLM key is available, `voiceopsSystemPrompt` is the system prompt
 * to hand the model so it emits OpenUI Lang our <Renderer> can render live.
 */
import { Fragment } from 'react'
import { defineComponent, createLibrary } from '@openuidev/react-lang'
import { z } from 'zod'
import { Lightning, Target, GitCommit, Wrench, Warning, CheckCircle } from '@phosphor-icons/react'

/* render an array (or single) of parsed OpenUI element nodes */
function renderNodes(renderNode, value) {
  if (value == null) return null
  const list = Array.isArray(value) ? value : [value]
  return list.map((node, i) => <Fragment key={i}>{renderNode(node)}</Fragment>)
}

const Briefing = defineComponent({
  name: 'Briefing',
  description: 'Top-level incident briefing panel. Always the root component.',
  props: z.object({
    title: z.string().describe('Incident title, one line'),
    children: z.array(z.any()).optional().describe('Summary, MetricGrid, Findings, Callout blocks'),
  }),
  component: ({ props, renderNode }) => (
    <div className="openui-panel">
      <div className="oui-head">
        incident briefing
        <span className="badge"><Lightning size={12} weight="fill" /> rendered by OpenUI</span>
      </div>
      <div className="oui-body">
        <div className="oui-title">{props.title}</div>
        {renderNodes(renderNode, props.children)}
      </div>
    </div>
  ),
})

const Summary = defineComponent({
  name: 'Summary',
  description: 'A one-paragraph plain-text summary of the incident',
  props: z.object({ text: z.string() }),
  component: ({ props }) => <div className="text">{props.text}</div>,
})

const MetricGrid = defineComponent({
  name: 'MetricGrid',
  description: 'A responsive grid of Metric tiles',
  props: z.object({ children: z.array(z.any()).optional().describe('Metric tiles') }),
  component: ({ props, renderNode }) => (
    <div className="metrics">{renderNodes(renderNode, props.children)}</div>
  ),
})

const Metric = defineComponent({
  name: 'Metric',
  description: 'A single metric tile. tone colours the value: bad=red, warn=amber, ok=green',
  props: z.object({
    label: z.string(),
    value: z.string(),
    tone: z.enum(['bad', 'warn', 'ok']).optional(),
  }),
  component: ({ props }) => (
    <div className="metric">
      <div className="k">{props.label}</div>
      <div className={`v${props.tone ? ' ' + props.tone : ''}`}>{props.value}</div>
    </div>
  ),
})

const Findings = defineComponent({
  name: 'Findings',
  description: 'A vertical list of Finding rows',
  props: z.object({ children: z.array(z.any()).optional().describe('Finding rows') }),
  component: ({ props, renderNode }) => (
    <div className="findings">{renderNodes(renderNode, props.children)}</div>
  ),
})

const FINDING_ICON = { cause: Target, commit: GitCommit, fix: Wrench }
const Finding = defineComponent({
  name: 'Finding',
  description: 'A single diagnostic finding. kind picks the icon: cause | commit | fix',
  props: z.object({
    kind: z.enum(['cause', 'commit', 'fix']),
    text: z.string(),
  }),
  component: ({ props }) => {
    const Icon = FINDING_ICON[props.kind] || Target
    return (
      <div className="finding">
        <Icon size={15} />
        <span>{props.text}</span>
      </div>
    )
  },
})

const Callout = defineComponent({
  name: 'Callout',
  description: 'A short highlighted note. tone: warn (amber) | ok (green)',
  props: z.object({
    tone: z.enum(['warn', 'ok']),
    text: z.string(),
  }),
  component: ({ props }) => {
    const Icon = props.tone === 'ok' ? CheckCircle : Warning
    return (
      <div className={`callout ${props.tone}`}>
        <Icon size={14} />
        <span>{props.text}</span>
      </div>
    )
  },
})

export const voiceopsLibrary = createLibrary({
  components: [Briefing, Summary, MetricGrid, Metric, Findings, Finding, Callout],
  root: 'Briefing',
})

// Hand this to the LLM once a key is wired up so it emits renderable OpenUI Lang.
export const voiceopsSystemPrompt = voiceopsLibrary.prompt({
  preamble:
    'You are VoiceOps, an AI on-call engineer. Summarise the incident analysis as a single Briefing.',
  additionalRules: [
    'Always start with root = Briefing(...).',
    'Use a MetricGrid with 2 to 4 Metric tiles for the live numbers.',
    'Use Findings for cause / commit / fix, and a Callout for the proposed next action.',
  ],
})
