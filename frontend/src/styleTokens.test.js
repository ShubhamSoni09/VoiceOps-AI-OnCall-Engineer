import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const stylesheetPath = path.join(__dirname, 'index.css')
const packagePath = path.join(__dirname, '..', 'package.json')

function readStylesheet() {
  return fs.readFileSync(stylesheetPath, 'utf8')
}

function readPackage() {
  return JSON.parse(fs.readFileSync(packagePath, 'utf8'))
}

function sourceFiles(dir = __dirname) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const fullPath = path.join(dir, entry.name)
    if (entry.isDirectory()) return sourceFiles(fullPath)
    return /\.(css|jsx?)$/.test(entry.name) ? [fullPath] : []
  })
}

function themeTokens(css, selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const match = css.match(new RegExp(`${escaped}\\s*\\{([\\s\\S]*?)\\}`))
  return Object.fromEntries(
    [...(match?.[1] || '').matchAll(/--([a-zA-Z0-9-]+):\s*([^;]+);/g)]
      .map((token) => [token[1], token[2].trim()]),
  )
}

function hexToRgb(value) {
  const hex = value.trim()
  if (!/^#[0-9a-f]{6}$/i.test(hex)) return null
  const numeric = Number.parseInt(hex.slice(1), 16)
  return [(numeric >> 16) & 255, (numeric >> 8) & 255, numeric & 255]
}

function linear(value) {
  const channel = value / 255
  return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4
}

function luminance(rgb) {
  return (0.2126 * linear(rgb[0])) + (0.7152 * linear(rgb[1])) + (0.0722 * linear(rgb[2]))
}

function contrastRatio(foreground, background) {
  const fg = hexToRgb(foreground)
  const bg = hexToRgb(background)
  if (!fg || !bg) return 0
  const lighter = Math.max(luminance(fg), luminance(bg))
  const darker = Math.min(luminance(fg), luminance(bg))
  return (lighter + 0.05) / (darker + 0.05)
}

describe('CSS design tokens', () => {
  it('defines every custom property referenced by the app stylesheet', () => {
    const css = readStylesheet()
    const definitions = new Set(
      [...css.matchAll(/--([a-zA-Z0-9-]+)\s*:/g)].map((match) => match[1]),
    )
    const references = [...css.matchAll(/var\(--([a-zA-Z0-9-]+)/g)].map((match) => match[1])
    const missing = [...new Set(references.filter((token) => !definitions.has(token)))].sort()

    expect(missing).toEqual([])
  })

  it('uses the paired on-accent token for accent-backed controls', () => {
    const css = readStylesheet()
    const accentRulesWithWhiteText = [...css.matchAll(/[^{}]+{[^{}]*background:\s*var\(--accent\)[^{}]*color:\s*#fff\b[^{}]*}/gi)]
      .map((match) => match[0].trim())

    expect(accentRulesWithWhiteText).toEqual([])
  })

  it('keeps core text tokens above WCAG AA contrast on app surfaces', () => {
    const css = readStylesheet()
    const themes = {
      dark: themeTokens(css, ':root,\n:root[data-theme="dark"]'),
      light: themeTokens(css, ':root[data-theme="light"]'),
    }
    const pairs = [
      ['ink', 'bg'],
      ['ink-dim', 'bg'],
      ['ink-faint', 'bg'],
      ['ink', 'panel'],
      ['ink-dim', 'panel'],
      ['ink-faint', 'panel'],
      ['ink-placeholder', 'bg-sunk'],
      ['ink-placeholder', 'panel'],
      ['ink-placeholder', 'elev'],
      ['on-accent', 'accent'],
      ['btn-fg', 'btn-bg'],
    ]
    const failures = Object.entries(themes).flatMap(([theme, tokens]) => (
      pairs
        .map(([foreground, background]) => ({
          theme,
          pair: `${foreground} on ${background}`,
          ratio: contrastRatio(tokens[foreground], tokens[background]),
        }))
        .filter((item) => item.ratio < 4.5)
        .map((item) => `${item.theme} ${item.pair} ${item.ratio.toFixed(2)}`)
    ))

    expect(failures).toEqual([])
  })

  it('keeps semantic status colors readable as text on light and dark surfaces', () => {
    const css = readStylesheet()
    const themes = {
      dark: themeTokens(css, ':root,\n:root[data-theme="dark"]'),
      light: themeTokens(css, ':root[data-theme="light"]'),
    }
    const statuses = ['crit', 'warn', 'ok', 'info']
    const surfaces = ['bg', 'panel', 'bg-sunk']
    const failures = Object.entries(themes).flatMap(([theme, tokens]) => (
      statuses.flatMap((status) => (
        surfaces
          .map((surface) => ({
            theme,
            pair: `${status} on ${surface}`,
            ratio: contrastRatio(tokens[status], tokens[surface]),
          }))
          .filter((item) => item.ratio < 4.5)
          .map((item) => `${item.theme} ${item.pair} ${item.ratio.toFixed(2)}`)
      ))
    ))

    expect(failures).toEqual([])
  })

  it('does not show pointer cursors for disabled buttons by default', () => {
    const css = readStylesheet()

    expect(css).toMatch(/button:disabled\s*\{[^}]*cursor:\s*not-allowed;[^}]*\}/)
  })

  it('keeps native cursors instead of custom cursor decoration', () => {
    const css = readStylesheet()

    expect(css).not.toMatch(/cursor:\s*(url|none)\b/)
  })

  it('keeps action cards border-led instead of pairing borders with large shadows', () => {
    const css = readStylesheet()
    const actionRule = css.match(/\.action\s*\{([^}]*)\}/)?.[1] || ''

    expect(actionRule).toContain('border: 1px solid var(--line-strong)')
    expect(actionRule).not.toMatch(/box-shadow|--shadow-card/)
  })

  it('keeps the app shell quiet instead of adding landing-page decoration', () => {
    const css = readStylesheet()
    const appRule = css.match(/\.app\s*\{([^}]*)\}/)?.[1] || ''
    const loginWrapRule = css.match(/\.login-wrap\s*\{([^}]*)\}/)?.[1] || ''

    expect(appRule).toContain('height: 100dvh')
    expect(loginWrapRule).toContain('min-height: 100dvh')
    expect(css).not.toMatch(/\b100vh\b/)
    expect(css).not.toMatch(/radial-gradient|backdrop-filter|filter:\s*blur/)
  })

  it('keeps one lightweight icon family instead of stacking UI kits', () => {
    const dependencies = readPackage().dependencies || {}

    expect(dependencies).toHaveProperty('@phosphor-icons/react')
    expect(Object.keys(dependencies).sort()).not.toEqual(
      expect.arrayContaining([
        '@chakra-ui/react',
        '@mui/material',
        'antd',
        'lucide-react',
        '@mantine/core',
      ]),
    )
  })

  it('keeps visible copy plain by avoiding em and en dashes', () => {
    const offenders = sourceFiles()
      .filter((file) => /[\u2014\u2013]/.test(fs.readFileSync(file, 'utf8')))
      .map((file) => path.relative(__dirname, file))

    expect(offenders).toEqual([])
  })

  it('uses icons and text instead of emoji decoration', () => {
    const emojiPattern = /[\u{1F300}-\u{1FAFF}\u2600-\u27BF]/u
    const offenders = sourceFiles()
      .filter((file) => emojiPattern.test(fs.readFileSync(file, 'utf8')))
      .map((file) => path.relative(__dirname, file))

    expect(offenders).toEqual([])
  })

  it('uses the icon library instead of hand-rolled SVG markup', () => {
    const offenders = sourceFiles()
      .filter((file) => !/\.test\./.test(file))
      .filter((file) => /<svg\b|<path\b/.test(fs.readFileSync(file, 'utf8')))
      .map((file) => path.relative(__dirname, file))

    expect(offenders).toEqual([])
  })

  it('honors reduced motion for all signal animations', () => {
    const css = readStylesheet()
    const reducedMotionBlock = css.match(/@media \(prefers-reduced-motion:\s*reduce\)\s*\{([\s\S]*?)\n\}/)?.[1] || ''

    expect(css).toMatch(/@keyframes\s+\w+/)
    expect(reducedMotionBlock).toContain('animation: none !important')
    expect(reducedMotionBlock).toContain('transition: none !important')
  })

  it('keeps form placeholders readable instead of using ghost text', () => {
    const css = readStylesheet()
    const placeholderRules = [...css.matchAll(/[^{}]+::placeholder\s*\{[^{}]*color:\s*var\(--([^)]+)\)[^{}]*}/g)]
      .map((match) => ({ rule: match[0], token: match[1] }))

    expect(placeholderRules.length).toBeGreaterThan(0)
    expect(placeholderRules.map((rule) => rule.token)).not.toContain('ink-ghost')
    expect(new Set(placeholderRules.map((rule) => rule.token))).toEqual(new Set(['ink-placeholder']))
  })

  it('keeps collapsed summary metadata readable instead of ghosted', () => {
    const css = readStylesheet()

    expect(css).toMatch(/\.feed-recent-marker small\s*\{[^}]*color:\s*var\(--ink-faint\);/)
    expect(css).toMatch(/\.feed-repeat-details summary\s*\{[^}]*color:\s*var\(--ink-dim\);/)
    expect(css).toMatch(/\.feed-repeat-details summary small\s*\{[^}]*color:\s*var\(--ink-dim\);/)
    expect(css).toMatch(/\.rail-section-details > summary small\s*\{[^}]*color:\s*var\(--ink-faint\);/)
    expect(css).toMatch(/\.workdash-assignment-details > summary small,\s*\.workdash-queue-details > summary small\s*\{[^}]*color:\s*var\(--ink-dim\);/)
    expect(css).toMatch(/\.left-rail-details summary small\s*\{[^}]*color:\s*var\(--ink-faint\);/)
    expect(css).toMatch(/\.team-title small\s*\{[^}]*color:\s*var\(--ink-faint\);/)
    expect(css).toMatch(/\.memory-details summary small\s*\{[^}]*color:\s*var\(--ink-faint\);/)
    expect(css).toMatch(/\.memory-health-head small\s*\{[^}]*color:\s*var\(--ink-dim\);/)
    expect(css).toMatch(/\.action-requester\s*\{[^}]*color:\s*var\(--ink-faint\);/)
    expect(css).toMatch(/\.workdash-status-cell span,\s*\.workdash-status-cell small\s*\{[^}]*color:\s*var\(--ink-dim\);/)
    expect(css).toMatch(/\.workdash-status-cell small\s*\{[^}]*color:\s*var\(--ink-dim\);/)
    expect(css).toMatch(/\.workdash-next span\s*\{[^}]*color:\s*var\(--ink-dim\);/)
  })

  it('does not push the stable left rail roster below an empty scroll area', () => {
    const css = readStylesheet()

    expect(css).toMatch(/\.rail-foot\s*\{[^}]*margin-top:\s*auto;/)
    expect(css).toMatch(/\.rail--left-no-body \.rail-foot\s*\{[^}]*margin-top:\s*0;/)
  })

  it('uses neutral letter spacing across product UI text', () => {
    const css = readStylesheet()
    const values = [...css.matchAll(/letter-spacing:\s*([^;]+);/g)]
      .map((match) => match[1].trim())

    expect(values.length).toBeGreaterThan(0)
    expect(new Set(values)).toEqual(new Set(['0']))
  })

  it('keeps the mobile center row tall enough for the current feed', () => {
    const css = readStylesheet()
    const mobileBlock = css.match(/@media \(max-width: 520px\)\s*\{([\s\S]*?)\/\* ============================================================/m)?.[1] || ''

    expect(mobileBlock).toMatch(/\.body\s*\{[^}]*grid-template-rows:\s*clamp\(118px,\s*15dvh,\s*142px\) minmax\(224px,\s*1fr\) clamp\(172px,\s*25dvh,\s*196px\);/)
    expect(mobileBlock).not.toMatch(/\.body\s*\{[^}]*grid-template-rows:[^}]*minmax\(0,\s*1fr\);/)
  })

  it('keeps the mobile incident header compact when repository names are long', () => {
    const css = readStylesheet()
    const mobileBlock = css.match(/@media \(max-width: 520px\)\s*\{([\s\S]*?)\/\* ============================================================/m)?.[1] || ''

    expect(mobileBlock).toMatch(/\.inc-facts\s*\{[^}]*display:\s*grid;/)
    expect(mobileBlock).toContain('grid-template-columns: repeat(2, minmax(0, 1fr))')
    expect(mobileBlock).toMatch(/\.inc-facts \.fact:first-child\s*\{[^}]*grid-column:\s*1 \/ -1;/)
    expect(mobileBlock).toMatch(/\.inc-facts \.fact:nth-child\(4\)\s*\{[^}]*display:\s*none;/)
    expect(mobileBlock).toMatch(/\.inc-facts \.fact \.v\s*\{[^}]*white-space:\s*nowrap;[^}]*text-overflow:\s*ellipsis;/)
    expect(mobileBlock).toMatch(/\.inc-header h1\s*\{[^}]*font-size:\s*17px;/)
    expect(mobileBlock).toMatch(/\.inc-header \.top \.mono\s*\{[^}]*display:\s*none;/)
  })

  it('uses a compact team room summary on narrow layouts', () => {
    const css = readStylesheet()
    const tabletBlock = css.match(/@media \(max-width: 880px\)\s*\{([\s\S]*?)@media \(max-width: 520px\)/m)?.[1] || ''

    expect(tabletBlock).toMatch(/\.rail--left \.team-block \.person\s*\{[^}]*display:\s*none;/)
    expect(tabletBlock).toMatch(/\.rail--left \.team-compact\s*\{[^}]*display:\s*flex;/)
    expect(tabletBlock).toMatch(/\.rail--left \.team-speaker-summary\s*\{[^}]*min-height:\s*26px;/)
    expect(tabletBlock).toMatch(/\.rail--left \.left-rail-details\s*\{[^}]*display:\s*none;/)
  })

  it('keeps the tablet meeting feed visible between setup and dashboard rows', () => {
    const css = readStylesheet()
    const tabletBlock = css.match(/@media \(max-width: 880px\)\s*\{([\s\S]*?)@media \(max-width: 520px\)/m)?.[1] || ''

    expect(tabletBlock).toMatch(/\.body\s*\{[^}]*grid-template-rows:\s*clamp\(112px,\s*14dvh,\s*132px\) minmax\(190px,\s*1fr\) clamp\(150px,\s*26dvh,\s*190px\);/)
    expect(tabletBlock).not.toMatch(/grid-template-rows:[^;]*minmax\(0,\s*1fr\)/)
  })

  it('lets the command footer grow for microphone recovery messages', () => {
    const css = readStylesheet()

    expect(css).toMatch(/\.app\s*\{[^}]*grid-template-rows:\s*52px minmax\(0,\s*1fr\) auto;/)
    expect(css).toMatch(/\.ptt\s*\{[^}]*min-height:\s*84px;/)
  })

  it('stacks compact feed audit rows on small screens', () => {
    const css = readStylesheet()
    const mobileBlock = css.match(/@media \(max-width: 520px\)\s*\{([\s\S]*?)\/\* ============================================================/m)?.[1] || ''

    expect(css).toMatch(/\.feed-repeat-row\s*\{[^}]*grid-template-columns:\s*minmax\(64px,\s*\.75fr\) auto minmax\(0,\s*1\.8fr\);/)
    expect(css).toMatch(/\.feed-repeat-row em\s*\{[^}]*white-space:\s*normal;[^}]*overflow-wrap:\s*anywhere;[^}]*-webkit-line-clamp:\s*2;/)
    expect(mobileBlock).toMatch(/\.feed-repeat-details summary\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\);/)
    expect(mobileBlock).toMatch(/\.feed-repeat-row\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\);/)
  })

  it('keeps the mobile feed constrained inside the center panel', () => {
    const css = readStylesheet()
    const mobileBlock = css.match(/@media \(max-width: 520px\)\s*\{([\s\S]*?)\/\* ============================================================/m)?.[1] || ''

    expect(mobileBlock).toMatch(/\.feed\s*\{[^}]*flex:\s*1 1 0;[^}]*padding:\s*12px 14px 18px;/)
    expect(mobileBlock).toMatch(/\.stepper\s*\{[^}]*padding:\s*6px 18px 7px;/)
    expect(mobileBlock).toMatch(/\.ptt\s*\{[^}]*min-height:\s*68px;/)
  })

  it('keeps handoff summaries compact in the right rail', () => {
    const css = readStylesheet()

    expect(css).toMatch(/\.handoff-lines p\s*\{[^}]*-webkit-line-clamp:\s*1;/)
    expect(css).toMatch(/\.handoff-review-item\.compact\s*\{[^}]*display:\s*grid;[^}]*grid-template-columns:\s*auto minmax\(0,\s*1fr\) auto;[^}]*padding:\s*6px 7px;/)
    expect(css).toMatch(/\.handoff-review-item\.compact > div\s*\{[^}]*display:\s*contents;/)
  })

  it('keeps meeting memory controls compact by default', () => {
    const css = readStylesheet()

    expect(css).toMatch(/\.memory-controls\s*\{[^}]*margin:\s*7px 0 8px;[^}]*padding-top:\s*7px;/)
    expect(css).toMatch(/\.memory-details summary\s*\{[^}]*min-height:\s*27px;/)
  })

  it('keeps compact interactive rows at a usable minimum height', () => {
    const css = readStylesheet()

    expect(css).toMatch(/\.feed-history summary\s*\{[^}]*min-height:\s*28px;/)
    expect(css).toMatch(/\.feed-repeat-details summary\s*\{[^}]*min-height:\s*28px;/)
    expect(css).toMatch(/\.workdash-context summary\s*\{[^}]*min-height:\s*28px;/)
    expect(css).toMatch(/\.workdash-assignment-details > summary,\s*\.workdash-queue-details > summary\s*\{[^}]*min-height:\s*28px;/)
    expect(css).toMatch(/\.memory-query input\s*\{[^}]*min-height:\s*28px;/)
    expect(css).toMatch(/\.memory-query button\s*\{[^}]*height:\s*28px;/)
    expect(css).toMatch(/\.command-entry button\s*\{[^}]*width:\s*36px;[^}]*height:\s*36px;/)
    expect(css).toMatch(/\.action-audit-details summary\s*\{[^}]*min-height:\s*28px;[^}]*display:\s*inline-flex;/)
  })

  it('keeps agent setup summaries compact inside the right rail', () => {
    const css = readStylesheet()

    expect(css).toMatch(/\.repo-flow\.compact p\s*\{[^}]*font-size:\s*10\.7px;[^}]*display:\s*-webkit-box;[^}]*-webkit-line-clamp:\s*2;[^}]*overflow:\s*hidden;/)
    expect(css).toMatch(/\.agent-trace-details\s*\{[^}]*margin-top:\s*6px;[^}]*padding-top:\s*6px;/)
    expect(css).toMatch(/\.agent-run-summary\s*\{[^}]*margin-top:\s*6px;[^}]*padding-top:\s*6px;/)
    expect(css).toMatch(/\.agent-run-summary p\s*\{[^}]*-webkit-line-clamp:\s*1;/)
  })

  it('gives mobile microphone recovery copy the full command column', () => {
    const css = readStylesheet()
    const mobileBlock = css.match(/@media \(max-width: 520px\)\s*\{([\s\S]*?)\/\* ============================================================/m)?.[1] || ''

    expect(css).toMatch(/\.ptt-voice-warning\s*\{[^}]*display:\s*grid;[^}]*grid-template-columns:\s*auto minmax\(0,\s*1fr\);/)
    expect(css).toMatch(/\.ptt-voice-warning small\s*\{[^}]*grid-column:\s*2;/)
    expect(css).toMatch(/\.ptt-recovery-actions\s*\{[^}]*grid-column:\s*2;[^}]*display:\s*flex;[^}]*flex-wrap:\s*wrap;[^}]*gap:\s*6px;/)
    expect(css).toMatch(/\.ptt-type-fallback,\s*\.ptt-open-url,\s*\.ptt-copy-url\s*\{[^}]*min-height:\s*24px;/)
    expect(css).toMatch(/\.live-setup-primary small\s*\{[^}]*overflow-wrap:\s*anywhere;[^}]*white-space:\s*normal;/)
    expect(mobileBlock).toMatch(/\.ptt\.voice-unavailable\s*\{[^}]*grid-template-columns:\s*auto minmax\(0,\s*1fr\);/)
    expect(mobileBlock).toMatch(/\.ptt\.voice-unavailable\.voice-retryable\s*\{[^}]*grid-template-columns:\s*auto minmax\(0,\s*1fr\) auto;/)
    expect(mobileBlock).toMatch(/\.ptt\.voice-unavailable\.voice-retryable \.ptt-actions\s*\{[^}]*display:\s*flex;[^}]*min-width:\s*72px;/)
    expect(mobileBlock).toMatch(/\.ptt\.voice-unavailable\.voice-retryable \.live-meeting\s*\{[^}]*width:\s*auto;[^}]*min-width:\s*72px;/)
    expect(mobileBlock).toMatch(/\.ptt\.voice-unavailable\.voice-retryable \.live-meeting \.live-label-full\s*\{[^}]*display:\s*none;/)
    expect(mobileBlock).toMatch(/\.ptt\.voice-unavailable\.voice-retryable \.live-meeting \.live-label-short\s*\{[^}]*display:\s*inline;/)
    expect(mobileBlock).toMatch(/\.ptt\.voice-unavailable\.voice-blocked \.ptt-actions\s*\{[^}]*display:\s*none;/)
    expect(mobileBlock).toMatch(/\.ptt\.voice-unavailable \.ptt-voice-warning\s*\{[^}]*width:\s*100%;/)
  })

  it('keeps the mobile live meeting button visibly labeled', () => {
    const css = readStylesheet()
    const mobileBlock = css.match(/@media \(max-width: 520px\)\s*\{([\s\S]*?)\/\* ============================================================/m)?.[1] || ''

    expect(css).toMatch(/\.live-label-short\s*\{\s*display:\s*none;\s*\}/)
    expect(mobileBlock).toMatch(/\.ptt-actions\s*\{[^}]*min-width:\s*64px;/)
    expect(mobileBlock).toMatch(/\.live-meeting\s*\{[^}]*width:\s*auto;[^}]*min-width:\s*64px;[^}]*height:\s*30px;/)
    expect(mobileBlock).toMatch(/\.live-meeting \.live-label-full\s*\{[^}]*display:\s*none;/)
    expect(mobileBlock).toMatch(/\.live-meeting \.live-label-short\s*\{[^}]*display:\s*inline;/)
  })

  it('keeps phone access to work status and approvals without showing the full right rail console', () => {
    const css = readStylesheet()
    const tabletBlock = css.match(/@media \(max-width: 880px\)\s*\{([\s\S]*?)@media \(max-width: 520px\)/m)?.[1] || ''
    const mobileBlock = css.match(/@media \(max-width: 520px\)\s*\{([\s\S]*?)\/\* ============================================================/m)?.[1] || ''

    expect(css).toMatch(/\.workdash-status-strip\s*\{[^}]*grid-template-columns:\s*repeat\(3,\s*minmax\(0,\s*1fr\)\);/)
    expect(css).toMatch(/\.workdash-status-cell\s*\{[^}]*padding:\s*7px 7px 6px;/)
    expect(tabletBlock).toMatch(/\.rail--right \.rail-section\s*\{[^}]*display:\s*none;/)
    expect(tabletBlock).toMatch(/\.rail--right \.rail-section--workdash,\s*\.rail--right \.rail-section--actions\s*\{[^}]*display:\s*block;/)
    expect(tabletBlock).toMatch(/\.rail--right \.ai-coworker,\s*\.rail--right \.setup-action-strip,\s*\.rail--right \.rail-section--memory,\s*\.rail--right \.rail-section--handoff,\s*\.rail--right > \.scroll > \.rail-section-details\s*\{[^}]*display:\s*none;/)
    expect(tabletBlock).toMatch(/\.rail--right \.workdash > :not\(\.workdash-next\)\s*\{[^}]*display:\s*none;/)
    expect(tabletBlock).toMatch(/\.rail--right \.rail-section--actions\.rail-section--empty-actions,\s*\.rail--right \.rail-section--actions\.rail-section--no-pending-actions,\s*\.rail--right \.rail-section--actions \.panel-empty\s*\{[^}]*display:\s*none;/)
    expect(mobileBlock).toMatch(/\.rail--right\s*\{[^}]*display:\s*flex;/)
    expect(mobileBlock).toMatch(/\.rail--right \.rail-section\s*\{[^}]*display:\s*none;/)
    expect(mobileBlock).toMatch(/\.rail--right \.rail-section--workdash\s*\{[^}]*display:\s*block;[^}]*order:\s*3;/)
    expect(mobileBlock).toMatch(/\.rail--right \.rail-section--actions\s*\{[^}]*display:\s*block;[^}]*order:\s*4;/)
    expect(mobileBlock).toMatch(/\.rail--right \.rail-section--memory\s*\{[^}]*display:\s*none;[^}]*order:\s*2;/)
    expect(mobileBlock).toMatch(/\.rail--right \.scroll\s*\{[^}]*padding:\s*8px 10px;[^}]*gap:\s*0;/)
    expect(css).toMatch(/\.ai-coworker\s*\{[^}]*display:\s*grid;[^}]*background:\s*var\(--elev\);/)
    expect(mobileBlock).toMatch(/\.rail--right \.ai-coworker\s*\{[^}]*display:\s*none;/)
    expect(css).toMatch(/\.setup-runway-card\s*\{[^}]*grid-column:\s*1 \/ -1;[^}]*grid-template-columns:\s*auto minmax\(0,\s*1fr\) auto auto;/)
    expect(css).toMatch(/\.setup-action-details > summary\s*\{[^}]*grid-template-columns:\s*auto minmax\(0,\s*1fr\);/)
    expect(css).toMatch(/\.setup-action-detail-grid\s*\{[^}]*grid-template-columns:\s*repeat\(3,\s*minmax\(0,\s*1fr\)\);/)
    expect(mobileBlock).toMatch(/\.rail--right \.setup-action-strip\s*\{[^}]*display:\s*none;[^}]*padding:\s*5px 7px;[^}]*gap:\s*5px;/)
    expect(mobileBlock).toMatch(/\.rail--right \.setup-action-strip\.blocked,\s*\.rail--right \.setup-action-strip\.attention\s*\{[^}]*display:\s*none;/)
    expect(mobileBlock).toMatch(/\.rail--right \.setup-action-strip \.setup-runway-card > small,\s*\.rail--right \.setup-action-strip \.setup-action-details\s*\{[^}]*display:\s*none;/)
    expect(mobileBlock).toMatch(/\.rail--right \.setup-action-cell small\s*\{[^}]*display:\s*none;/)
    expect(mobileBlock).toMatch(/\.rail--right \.workdash\s*\{[^}]*padding:\s*7px 8px;[^}]*gap:\s*0;/)
    expect(css).toMatch(/\.workdash-next\s*\{[^}]*grid-template-columns:\s*auto minmax\(0,\s*1fr\);/)
    expect(mobileBlock).toMatch(/\.rail--right \.workdash > :not\(\.workdash-next\)\s*\{[^}]*display:\s*none;/)
    expect(mobileBlock).toMatch(/\.rail--right \.workdash > \.workdash-queue-details\s*\{[^}]*display:\s*block;[^}]*margin-top:\s*6px;/)
    expect(mobileBlock).toMatch(/\.rail--right \.workdash-queue-details \.workdash-queue-body\s*\{[^}]*max-height:\s*64px;[^}]*overflow-y:\s*auto;/)
    expect(mobileBlock).toMatch(/\.rail--right \.workdash-next\s*\{[^}]*padding:\s*6px 7px;/)
    expect(mobileBlock).toMatch(/\.rail--right \.action-title\s*\{[^}]*display:\s*flex;[^}]*align-items:\s*baseline;/)
    expect(mobileBlock).toMatch(/\.rail--right \.action-summary\s*\{[^}]*-webkit-line-clamp:\s*1;/)
    expect(mobileBlock).toMatch(/\.rail--right \.action-audit-details\s*\{[^}]*display:\s*none;/)
    expect(mobileBlock).toMatch(/\.rail--right \.rail-section--actions\.rail-section--empty-actions,\s*\.rail--right \.rail-section--actions\.rail-section--no-pending-actions\s*\{[^}]*display:\s*none;/)
    expect(mobileBlock).toMatch(/\.rail--right \.rail-section--actions \.panel-empty\s*\{[^}]*display:\s*none;/)
    expect(mobileBlock).toMatch(/\.rail--right \.memory-panel\s*\{[^}]*padding-top:\s*7px;[^}]*border-top:\s*1px solid var\(--line\);/)
    expect(mobileBlock).toMatch(/\.rail--right \.memory-query\s*\{[^}]*height:\s*30px;/)
    expect(mobileBlock).toMatch(/\.rail--right \.memory-health,\s*\.rail--right \.memory-details,\s*\.rail--right \.memory-list,\s*\.rail--right \.memory-panel > \.panel-empty,\s*\.rail--right \.memory-controls,\s*\.rail--right \.citation-list,\s*\.rail--right \.rag-trace\s*\{[^}]*display:\s*none;/)
    expect(mobileBlock).toMatch(/\.rail--right \.memory-answer p\s*\{[^}]*-webkit-line-clamp:\s*1;/)
  })

  it('keeps mobile summary rows and command controls touchable', () => {
    const css = readStylesheet()
    const mobileBlock = css.match(/@media \(max-width: 520px\)\s*\{([\s\S]*?)\/\* ============================================================/m)?.[1] || ''

    expect(mobileBlock).toMatch(/\.rail--right details > summary,\s*\.feed-history summary\s*\{[^}]*min-height:\s*28px;/)
    expect(mobileBlock).toMatch(/\.command-entry button\s*\{[^}]*width:\s*30px;[^}]*height:\s*30px;/)
    expect(mobileBlock).toMatch(/\.command-entry input\s*\{[^}]*min-height:\s*30px;/)
  })
})
