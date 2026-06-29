import { renderToStaticMarkup } from 'react-dom/server'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it, vi } from 'vitest'
import Login, { initialLoginCredentials } from './Login.jsx'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

describe('Login', () => {
  it('frames the product as a team code console', () => {
    const html = renderToStaticMarkup(<Login onSuccess={vi.fn()} />)

    expect(html).toContain('Team code console')
    expect(html).toContain('meetings, memory, approved patches')
    expect(html).not.toContain('On-call console')
  })

  it('uses browser password-manager semantics', () => {
    const html = renderToStaticMarkup(<Login onSuccess={vi.fn()} />)

    expect(html).toContain('autoComplete="email"')
    expect(html).toContain('autoComplete="current-password"')
  })

  it('only preloads demo credentials in development mode', () => {
    const devCredentials = {
      email: 'priya@voiceops.dev',
      password: 'oncall123',
    }

    expect(initialLoginCredentials(true, devCredentials)).toEqual({
      email: 'priya@voiceops.dev',
      password: 'oncall123',
      demo: true,
    })
    expect(initialLoginCredentials(true)).toEqual({
      email: '',
      password: '',
      demo: false,
    })
    expect(initialLoginCredentials(false)).toEqual({
      email: '',
      password: '',
      demo: false,
    })
  })

  it('labels prefilled credentials as local demo state', () => {
    const html = renderToStaticMarkup(<Login onSuccess={vi.fn()} />)

    expect(html).toContain('Local demo account is prefilled for development.')
  })

  it('announces failed sign-in errors as alerts', () => {
    const source = fs.readFileSync(path.join(__dirname, 'Login.jsx'), 'utf8')

    expect(source).toContain('className="login-error" role="alert"')
  })
})
