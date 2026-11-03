import React, { useState } from 'react'
import { api, setToken } from '../lib/api.js'
import { Err } from '../components/bits.jsx'

/**
 * One sign-in for both sides. Which view you get depends on the account, not on
 * which page you went to — the city and its citizens are the same application.
 *
 * The seeded accounts are offered as buttons because the mechanism only makes
 * sense when you can be several people in turn: report as one, verify as
 * another, confirm as a third. Typing three sets of credentials to see that is
 * friction with no purpose in a demo.
 */

// From api/app/seed.py. The trust figures are what the seed sets, and they
// decide who may be routed a task.
const SEEDED = [
  { email: 'admin@starnet.demo', label: 'city_admin', role: 'The municipality', note: 'Queue, assign, resolve, audit' },
  { email: 'meera@starnet.demo', label: 'meera_k', role: 'Citizen · most trusted', note: 'Gets verify and confirm tasks' },
  { email: 'vikram@starnet.demo', label: 'vikram_s', role: 'Citizen · trusted', note: 'Reports, and settles for others' },
  { email: 'dev@starnet.demo', label: 'dev_p', role: 'Citizen · trusted', note: 'Reports, and settles for others' },
  { email: 'asha@starnet.demo', label: 'asha_r', role: 'Citizen · some history', note: 'Reports; may confirm' },
  { email: 'new@starnet.demo', label: 'newcomer', role: 'Citizen · no history', note: 'Never routed a task' },
]

const PASSWORD = 'starnet123'

export default function Login({ onDone }) {
  const [email, setEmail] = useState('admin@starnet.demo')
  const [password, setPassword] = useState(PASSWORD)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(null)
  const [mode, setMode] = useState('signin') // signin | signup
  const [handle, setHandle] = useState('')

  async function signIn(withEmail, withPassword) {
    setBusy(withEmail)
    setError(null)
    try {
      const { access_token } = await api.login(withEmail, withPassword)
      setToken(access_token)
      onDone(await api.me())
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(null)
    }
  }

  async function signUp(event) {
    event.preventDefault()
    setBusy('signup')
    setError(null)
    try {
      const { access_token } = await api.signup(
        email,
        password,
        handle || email.split('@')[0],
        'WARD-CENTRAL',
      )
      setToken(access_token)
      onDone(await api.me())
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="shell">
      <div className="panel login-card">
        <h1>STAR-Net</h1>
        <p className="tagline">
          Civic fault reporting where reward vests on confirmed resolution, not on submission
        </p>

        <h3>Sign in as one of the seeded accounts</h3>
        <p className="muted small">
          The mechanism needs several different people to do anything — a reporter cannot verify
          their own fault, and neither of them can confirm the repair. Switch between these to
          walk it through. Password is <span className="mono">{PASSWORD}</span> for all of them.
        </p>

        <div className="account-list">
          {SEEDED.map((account) => (
            <button
              key={account.email}
              className="account"
              disabled={busy !== null}
              onClick={() => signIn(account.email, PASSWORD)}
            >
              <span className="account-name">{account.label}</span>
              <span className="account-role">{account.role}</span>
              <span className="account-note muted small">{account.note}</span>
              {busy === account.email && <span className="small muted">Signing in…</span>}
            </button>
          ))}
        </div>

        <Err>{error}</Err>

        <details className="other-signin">
          <summary>Or use another account</summary>
          <form onSubmit={mode === 'signup' ? signUp : (e) => { e.preventDefault(); signIn(email, password) }}>
            <div className="field">
              <label htmlFor="email">Email</label>
              <input
                id="email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="username"
              />
            </div>
            {mode === 'signup' && (
              <div className="field">
                <label htmlFor="handle">Handle</label>
                <input
                  id="handle"
                  value={handle}
                  onChange={(e) => setHandle(e.target.value)}
                  placeholder="how you appear to others"
                />
              </div>
            )}
            <div className="field">
              <label htmlFor="password">Password</label>
              <input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete={mode === 'signup' ? 'new-password' : 'current-password'}
              />
            </div>
            <div className="row">
              <button className="btn primary" disabled={busy !== null}>
                {mode === 'signup' ? 'Create the account' : 'Sign in'}
              </button>
              <button
                type="button"
                className="btn"
                onClick={() => setMode(mode === 'signup' ? 'signin' : 'signup')}
              >
                {mode === 'signup' ? 'I already have one' : 'Create a new account'}
              </button>
            </div>
            {mode === 'signup' && (
              <p className="small muted">
                A brand-new account has no history, so its first report will not clear the
                confidence floor and no task will ever be routed to it. That is the mechanism
                working, not a fault — but it does mean the seeded accounts above are the way to
                see the whole flow.
              </p>
            )}
          </form>
        </details>
      </div>
    </div>
  )
}
