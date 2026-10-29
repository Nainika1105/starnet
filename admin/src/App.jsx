import React, { useEffect, useState } from 'react'
import { api, getToken, setToken } from './lib/api.js'
import Login from './pages/Login.jsx'
import Queue from './pages/Queue.jsx'
import Metrics from './pages/Metrics.jsx'
import Ledger from './pages/Ledger.jsx'

const TABS = [
  ['queue', 'Queue'],
  ['metrics', 'Accountability'],
  ['ledger', 'Ledger'],
]

export default function App() {
  const [me, setMe] = useState(null)
  const [checking, setChecking] = useState(true)
  const [tab, setTab] = useState('queue')
  const [mechanism, setMechanism] = useState(null)

  useEffect(() => {
    if (!getToken()) {
      setChecking(false)
      return
    }
    api
      .me()
      .then((user) => setMe(user.is_admin ? user : null))
      .catch(() => setToken(null))
      .finally(() => setChecking(false))
  }, [])

  useEffect(() => {
    api.mechanism().then(setMechanism).catch(() => {})
  }, [])

  if (checking) return <div className="shell"><p className="muted">Loading…</p></div>
  if (!me) return <Login onDone={setMe} />

  return (
    <div className="shell">
      <header className="top">
        <div>
          <h1>STAR-Net</h1>
          <p className="tagline">
            Municipal dashboard · reward vests on confirmed resolution, not on submission
          </p>
        </div>
        <div className="row">
          <span className="muted small">{me.handle}</span>
          <button
            className="btn"
            onClick={() => {
              setToken(null)
              setMe(null)
            }}
          >
            Sign out
          </button>
        </div>
      </header>

      <nav className="tabs">
        {TABS.map(([key, label]) => (
          <button key={key} aria-selected={tab === key} onClick={() => setTab(key)}>
            {label}
          </button>
        ))}
      </nav>

      {tab === 'queue' && <Queue />}
      {tab === 'metrics' && <Metrics />}
      {tab === 'ledger' && <Ledger mechanism={mechanism} />}
    </div>
  )
}
