import { chromium, expect } from '@playwright/test'
import process from 'node:process'

const args = new Set(process.argv.slice(2))
const asJson = args.has('--json')
const appUrl = optionValue(process.argv.slice(2), '--url') || 'http://127.0.0.1:5174/react'

async function main() {
  let browser
  try {
    await waitForHttp(appUrl)
    browser = await chromium.launch({ headless: !process.env.VOICEOPS_E2E_HEADED })
    const page = await browser.newPage({ viewport: { width: 1440, height: 950 } })
    await page.addInitScript(() => {
      Object.defineProperty(navigator, 'mediaDevices', { value: undefined, configurable: true })
      Object.defineProperty(window, 'MediaRecorder', { value: undefined, configurable: true })
    })
    await page.goto(`${appUrl}${appUrl.includes('?') ? '&' : '?'}mic_unavailable_e2e=${Date.now()}`)
    await page.getByRole('button', { name: /^sign in$/i }).click()
    await expect(page.getByRole('heading', { name: 'Team room' })).toBeVisible({ timeout: 30_000 })

    const command = page.getByRole('textbox', { name: 'Type command' })
    await expect(command).toBeEnabled({ timeout: 10_000 })
    const warning = page.locator('.ptt-voice-warning')
    await expect(warning).toBeVisible({ timeout: 10_000 })
    await expect(warning).toContainText('This browser cannot use the microphone.')
    await expect(page.getByRole('button', { name: 'Type command' })).toBeVisible({ timeout: 10_000 })
    await expect(page.locator('.ptt .live-meeting')).toHaveCount(0)
    await expect(page.locator('.ptt-open-url')).toHaveCount(0)
    await expect(page.locator('.ptt-copy-url')).toHaveCount(1)

    const report = await page.evaluate(() => ({
      commandEnabled: !document.querySelector('input[aria-label="Type command"]')?.disabled,
      copyOnly: document.querySelectorAll('.ptt-copy-url').length === 1
        && document.querySelectorAll('.ptt-open-url').length === 0,
      liveButtons: document.querySelectorAll('button[aria-label*="live" i]').length,
      notice: document.querySelector('.ptt-voice-warning')?.textContent?.trim() || '',
    }))
    if (!report.commandEnabled || !report.copyOnly || report.liveButtons !== 0) {
      throw new Error(`mic unavailable fallback failed: ${JSON.stringify(report)}`)
    }
    print(report)
  } finally {
    if (browser) await browser.close().catch(() => {})
  }
}

function print(report) {
  if (asJson) {
    console.log(JSON.stringify({ status: 'passed', ...report }, null, 2))
    return
  }
  console.log('mic unavailable fallback: passed')
}

async function waitForHttp(url) {
  const deadline = Date.now() + 15_000
  let lastError = ''
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url)
      if (response.ok || response.status < 500) return
      lastError = `${response.status} ${response.statusText}`
    } catch (error) {
      lastError = error.message
    }
    await new Promise((resolve) => setTimeout(resolve, 250))
  }
  throw new Error(`timed out waiting for ${url}: ${lastError}`)
}

function optionValue(argv, name) {
  const index = argv.indexOf(name)
  return index >= 0 ? argv[index + 1] : ''
}

main().catch((error) => {
  console.error(error.message || String(error))
  process.exit(1)
})
