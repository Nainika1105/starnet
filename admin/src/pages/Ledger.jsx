import React, { useEffect, useState } from 'react'
import { api } from '../lib/api.js'
import { Empty, Err, fmt, when } from '../components/bits.jsx'

const LABEL = {
  VEST: 'vested',
  FORFEIT: 'forfeited',
  REVERSAL: 'reversed',
  REDEMPTION: 'redeemed',
}

export default function Ledger({ mechanism }) {
  const [rows, setRows] = useState([])
  const [error, setError] = useState(null)
  const [filter, setFilter] = useState('')

  useEffect(() => {
    api.ledger().then(setRows).catch((e) => setError(e.message))
  }, [])

  const shown = filter
    ? rows.filter(
        (r) =>
          r.user.includes(filter) ||
          r.entry_type === filter ||
          r.stage === filter ||
          (r.role || '').includes(filter),
      )
    : rows

  const total = shown.reduce((sum, r) => sum + Number(r.amount), 0)

  return (
    <>
      <div className="panel">
        <h2>Append-only reward ledger</h2>
        <p className="muted small">
          Nothing here is ever updated or deleted. A balance is the signed sum of a user's entries,
          and every row carries the idempotency key that makes its transition impossible to replay.
        </p>
        {mechanism && (
          <p className="note">
            Vesting split: {fmt(mechanism.vesting_split.verification * 100, 0)}% at verification ·{' '}
            {fmt(mechanism.vesting_split.resolution * 100, 0)}% at resolution ·{' '}
            {fmt(mechanism.vesting_split.confirmation * 100, 0)}% at confirmation. A fabricated
            report can collect at most{' '}
            <strong>{fmt(mechanism.max_fabrication_payout_fraction * 100, 1)}%</strong> of an
            issue's pool, because a fake fault can never be repaired or confirmed.
          </p>
        )}
        <div className="row">
          <input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter by handle, stage, role or entry type"
            style={{ flex: 1 }}
          />
          <span className="muted small">
            {shown.length} entries · net {fmt(total)} pts
          </span>
        </div>
        <Err>{error}</Err>
      </div>

      <div className="panel">
        {shown.length === 0 ? (
          <Empty>No ledger entries.</Empty>
        ) : (
          <table>
            <thead>
              <tr>
                <th>When</th>
                <th>User</th>
                <th>Role</th>
                <th>Stage</th>
                <th>Type</th>
                <th className="num">Amount</th>
                <th>Note</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((r) => (
                <tr key={r.id} style={{ cursor: 'default' }}>
                  <td className="small">{when(r.created_at)}</td>
                  <td>{r.user}</td>
                  <td className="small">{r.role || '—'}</td>
                  <td className="small">{r.stage === 'NONE' ? '—' : r.stage}</td>
                  <td className="small">{LABEL[r.entry_type] || r.entry_type}</td>
                  <td className={`num amount ${Number(r.amount) < 0 ? 'neg' : 'pos'}`}>
                    {Number(r.amount) > 0 ? '+' : ''}
                    {fmt(r.amount)}
                  </td>
                  <td className="small muted">{r.note || ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  )
}
