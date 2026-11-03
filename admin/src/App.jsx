import React, { useEffect, useState } from 'react'
import { api, getToken, setToken } from './lib/api.js'
import Login from './pages/Login.jsx'
import Queue from './pages/Queue.jsx'
import Metrics from './pages/Metrics.jsx'
import Ledger from './pages/Ledger.jsx'
import Report from './citizen/Report.jsx'
import Feed from './citizen/Feed.jsx'
import Tasks from './citizen/Tasks.jsx'
import Wallet from './citizen/Wallet.jsx'

// Which side of the mechanism you see depends on the account, not on the URL.
// The city and its citizens are the same application, and seeing both from one
// link is the only way the chain from report to payout is visible at all.
const MUNICIPAL_TABS = [
  ['queue', 'Queue'],
  ['metrics', 'Accountability'],
  ['ledger', 'Ledger'],
]

const CITIZEN_TABS = [
  ['report', 'Report a fault'],
  ['feed', 'Nearby'],
  ['tasks', 'Your tasks'],
  ['wallet', 'Wallet'],
]

export default function App() {
  const [me, setMe] = useState(null)
  const [checking, setChecking] = useState(true)
  const [tab, setTab] = useState(null)
  const [mechanism, setMechanism] = useState(null)
  // Bumped whenever something is filed or settled, so the sibling tabs reload
  // rather than showing a wallet from before the task was completed.
  const [version, setVersion] = useState(0)

  useEffect(() => {
    if (!getToken()) {
      setChecking(false)
      return
    }
    api
      .me()
      .then(setMe)
      .catch(() => setToken(null))
      .finally(() => setChecking(false))
  }, [])

  useEffect(() => {
    api.mechanism().then(setMechanism).catch(() => {})
  }, [])

  const tabs = me?.is_admin ? MUNICIPAL_TABS : CITIZEN_TABS
  const current = tab && tabs.some(([key]) => key === tab) ? tab : tabs[0]?.[0]

  if (checking) {
    return (
      <div className="shell">
        <p className="muted">Loading…</p>
      </div>
    )
  }
  if (!me) return <Login onDone={(user) => { setMe(user); setTab(null) }} />

  const bump = () => setVersion((n) => n + 1)

  return (
    <div className="shell">
      <header className="top">
        <div>
          <h1>STAR-Net</h1>
          <p className="tagline">
            {me.is_admin
              ? 'Municipal dashboard · reward vests on confirmed resolution, not on submission'
              : 'Reward vests on confirmed resolution, not on submission'}
          </p>
        </div>
        <div className="row">
          <span className="muted small">
            {me.handle}
            {!me.is_admin && me.tier ? ` · ${me.tier}` : ''}
          </span>
          <button
            className="btn"
            onClick={() => {
              setToken(null)
              setMe(null)
              setTab(null)
            }}
          >
            Sign out
          </button>
        </div>
      </header>

      <nav className="tabs">
        {tabs.map(([key, label]) => (
          <button key={key} aria-selected={current === key} onClick={() => setTab(key)}>
            {label}
          </button>
        ))}
      </nav>

      {me.is_admin ? (
        <>
          {current === 'queue' && <Queue />}
          {current === 'metrics' && <Metrics />}
          {current === 'ledger' && <Ledger mechanism={mechanism} />}
        </>
      ) : (
        <>
          {current === 'report' && <Report onFiled={bump} />}
          {current === 'feed' && <Feed me={me} refreshKey={version} />}
          {current === 'tasks' && <Tasks onDone={bump} />}
          {current === 'wallet' && <Wallet refreshKey={version} />}
        </>
      )}
    </div>
  )
}
