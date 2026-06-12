import { useState } from 'react'
import { login } from './api.js'

export default function Login({ onSuccess }) {
  const [email, setEmail] = useState('priya@voiceops.dev')
  const [password, setPassword] = useState('oncall123')
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
          <div className="logo">V</div>
          <b>VoiceOps</b>
        </div>
        <h1>Sign in</h1>
        <p className="sub">On-call console · voice commands to your repo</p>
        <label>
          Email
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </label>
        <label>
          Password
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </label>
        {error && <p className="login-error">{error}</p>}
        <button type="submit" disabled={loading}>{loading ? 'Signing in…' : 'Sign in'}</button>
      </form>
    </div>
  )
}
