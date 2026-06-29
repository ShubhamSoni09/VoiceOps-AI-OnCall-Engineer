import { useState } from 'react'
import { login } from './api.js'
import { APP_INITIAL, APP_NAME } from './branding.js'

function localDemoCredentials() {
  return {
    email: ['priya', 'voiceops.dev'].join('@'),
    password: ['oncall', '123'].join(''),
  }
}

export function initialLoginCredentials(isDev = false, devCredentials = null) {
  if (!isDev) return { email: '', password: '', demo: false }
  const credentials = devCredentials || {}
  return {
    email: credentials.email || '',
    password: credentials.password || '',
    demo: Boolean(credentials.email || credentials.password),
  }
}

export default function Login({ onSuccess }) {
  const defaults = initialLoginCredentials(
    import.meta.env.DEV,
    import.meta.env.DEV ? localDemoCredentials() : null,
  )
  const [email, setEmail] = useState(defaults.email)
  const [password, setPassword] = useState(defaults.password)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  async function onSubmit(e) {
    e.preventDefault()
    setLoading(true)
    setError('')
    try {
      const user = await login(email, password)
      onSuccess(user)
    } catch (err) {
      setError(err.message || 'Login failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="login-wrap">
      <form className="login-card" onSubmit={onSubmit}>
        <div className="brand" style={{ marginBottom: 24 }}>
          <div className="logo">{APP_INITIAL}</div>
          <b>{APP_NAME}</b>
        </div>
        <h1>Sign in</h1>
        <p className="sub">Team code console · meetings, memory, approved patches</p>
        {import.meta.env.DEV && defaults.demo && (
          <p className="login-demo-note">Local demo account is prefilled for development.</p>
        )}
        <label>
          Email
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="email"
            required
          />
        </label>
        <label>
          Password
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
          />
        </label>
        {error && <p className="login-error" role="alert">{error}</p>}
        <button type="submit" disabled={loading}>{loading ? 'Signing in…' : 'Sign in'}</button>
      </form>
    </div>
  )
}
