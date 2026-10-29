import React, { useState } from 'react'
import { api, setToken } from '../lib/api.js'
import { Err } from '../components/bits.jsx'

export default function Login({ onDone }) {
  const [email, setEmail] = useState('admin@starnet.demo')
  const [password, setPassword] = useState('starnet123')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  async function submit(event) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const { access_token } = await api.login(email, password)
      setToken(access_token)
      const me = await api.me()
      if (!me.is_admin) {
        setToken(null)
        throw new Error('That account is a citizen account, not a municipal one.')
      }
      onDone(me)
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="shell">
      <div className="panel center-card">
        <h1>STAR-Net</h1>
        <p className="tagline">Municipal dashboard</p>
        <form onSubmit={submit}>
          <p>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="Email"
              style={{ width: '100%' }}
              autoComplete="username"
            />
          </p>
          <p>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Password"
              style={{ width: '100%' }}
              autoComplete="current-password"
            />
          </p>
          <button className="btn primary" disabled={busy} style={{ width: '100%' }}>
            {busy ? 'Signing in…' : 'Sign in'}
          </button>
          <Err>{error}</Err>
        </form>
        <p className="muted small" style={{ marginBottom: 0 }}>
          Seeded demo account: <span className="mono">admin@starnet.demo / starnet123</span>
        </p>
      </div>
    </div>
  )
}
