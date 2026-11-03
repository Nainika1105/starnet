import React, { useCallback, useEffect, useState } from 'react'
import { api } from '../lib/api.js'
import { ago, Empty, Err, Pill, when } from '../components/bits.jsx'

// Plain-language names for the vesting states, since the reader is the person
// waiting to be paid rather than someone reading the state machine.
const STATE_LABELS = {
  PENDING_VERIFICATION: 'waiting to be verified',
  PENDING_RESOLUTION: 'waiting for the repair',
  PENDING_CONFIRMATION: 'waiting for the repair to be confirmed',
  PAID: 'paid',
  FORFEITED: 'forfeited',
}

const ENTRY_LABELS = {
  VEST: 'vested',
  REVERSAL: 'reversed',
  FORFEIT: 'forfeited',
  REDEMPTION: 'spent',
}

export default function Wallet({ refreshKey }) {
  const [wallet, setWallet] = useState(null)
  const [catalog, setCatalog] = useState([])
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(null)
  const [redeemed, setRedeemed] = useState(null)

  const load = useCallback(() => {
    api.wallet().then(setWallet).catch((e) => setError(e.message))
  }, [])

  useEffect(load, [load, refreshKey])
  useEffect(() => {
    api.catalog().then(setCatalog).catch(() => {})
  }, [])

  async function redeem(item) {
    setError(null)
    setBusy(item.sku)
    try {
      // A nonce makes the redemption idempotent: a double-tapped button or a
      // retried request collides on it instead of charging twice.
      const nonce = `${item.sku}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
      const response = await api.redeem(item.sku, nonce)
      setRedeemed(response)
      load()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(null)
    }
  }

  if (!wallet) {
    return (
      <div className="panel">
        <h2>Your wallet</h2>
        <Err>{error}</Err>
        {!error && <p className="muted">Loading…</p>}
      </div>
    )
  }

  const pending = Object.entries(wallet.pending_by_state || {})
  const totalPending = pending.reduce((sum, [, amount]) => sum + Number(amount), 0)

  return (
    <>
      <div className="panel">
        <h2>Your wallet</h2>
        <div className="wallet-figures">
          <div>
            <span className="eyebrow">Released to you</span>
            <span className="figure">{wallet.balance}</span>
          </div>
          <div>
            <span className="eyebrow">Still conditional</span>
            <span className="figure amber">{totalPending.toFixed(4)}</span>
          </div>
          <div>
            <span className="eyebrow">Your tier</span>
            <span className="figure small-figure">{wallet.tier}</span>
          </div>
        </div>
        <p className="muted small">
          Released and conditional are shown separately on purpose. The second number is what the
          mechanism is holding back until the faults you reported are repaired and confirmed —
          it is a promise, not a balance.
        </p>

        {pending.length > 0 && (
          <table style={{ marginTop: 14 }}>
            <thead>
              <tr>
                <th>Withheld because it is</th>
                <th className="num">Points</th>
              </tr>
            </thead>
            <tbody>
              {pending.map(([state, amount]) => (
                <tr key={state}>
                  <td>{STATE_LABELS[state] || state}</td>
                  <td className="num">{amount}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="panel">
        <h2>What you have contributed</h2>
        {wallet.contributions.length === 0 ? (
          <Empty>Nothing yet. File a report, or complete a task routed to you.</Empty>
        ) : (
          <div className="ledger-wrap">
            <table>
              <thead>
                <tr>
                  <th>Issue</th>
                  <th>Your role</th>
                  <th>Status</th>
                  <th>Stake</th>
                  <th className="num">Entitlement</th>
                  <th className="num">Paid so far</th>
                </tr>
              </thead>
              <tbody>
                {wallet.contributions.map((c) => (
                  <tr key={`${c.issue_id}-${c.role}`} style={{ cursor: 'default' }}>
                    <td>{c.category}</td>
                    <td className="small">{c.role}</td>
                    <td>
                      <Pill status={c.issue_status} />
                    </td>
                    <td className="small">{STATE_LABELS[c.vesting_state] || c.vesting_state}</td>
                    <td className="num">{c.entitlement}</td>
                    <td className="num">{c.vested_so_far}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="panel">
        <h2>Your ledger</h2>
        <p className="muted small">
          Append-only. Nothing here is ever edited or deleted — a reward withdrawn after a repair
          failed appears as a negative entry beneath the one that paid it, not in place of it.
        </p>
        {wallet.ledger.length === 0 ? (
          <Empty>No entries yet.</Empty>
        ) : (
          <div className="ledger-wrap">
            <table>
              <thead>
                <tr>
                  <th>When</th>
                  <th>Stage</th>
                  <th>Type</th>
                  <th className="num">Amount</th>
                  <th>Note</th>
                </tr>
              </thead>
              <tbody>
                {wallet.ledger.map((e) => (
                  <tr key={e.id} style={{ cursor: 'default' }}>
                    <td className="small">{ago(e.created_at)} ago</td>
                    <td className="small">{e.stage === 'NONE' ? '—' : e.stage}</td>
                    <td>
                      <span className={`tag ${Number(e.amount) < 0 ? 'bad' : 'ok'}`}>
                        {ENTRY_LABELS[e.entry_type] || e.entry_type}
                      </span>
                    </td>
                    <td className={`num ${Number(e.amount) < 0 ? 'neg' : ''}`}>{e.amount}</td>
                    <td className="small muted">{e.note || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="panel">
        <h2>Spend your points</h2>
        <p className="banner warn">
          <strong>Simulated throughout</strong>
          <span>
            Points are never exchanged for money or goods. Redeeming deducts the points and
            records the choice; nothing is issued. The server refuses to start if that is
            switched off, because an instance that reports successful redemptions while issuing
            nothing would be worse than one that does not run.
          </span>
        </p>

        <Err>{error}</Err>
        {redeemed && (
          <p className="banner ok">
            <strong>{redeemed.message}</strong>
            <span>
              Reference {redeemed.fulfilment_reference} — the SIM prefix is there so this cannot
              be mistaken for a voucher code.
            </span>
          </p>
        )}

        <div className="catalog">
          {catalog.map((item) => {
            const affordable = Number(wallet.balance) >= Number(item.cost)
            return (
              <div className="catalog-item" key={item.sku}>
                <div>
                  <h3>{item.name}</h3>
                  <p className="muted small">{item.description}</p>
                </div>
                <div className="row">
                  <span className="num">{item.cost} pts</span>
                  <button
                    className="btn"
                    disabled={!affordable || busy === item.sku}
                    onClick={() => redeem(item)}
                  >
                    {busy === item.sku ? 'Redeeming…' : affordable ? 'Redeem' : 'Not enough'}
                  </button>
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </>
  )
}
