import React, { useEffect, useState } from 'react'
import { api } from '../lib/api.js'
import { Empty, Err, fmt } from '../components/bits.jsx'

export default function Metrics() {
  const [rows, setRows] = useState([])
  const [error, setError] = useState(null)

  useEffect(() => {
    api.metrics().then(setRows).catch((e) => setError(e.message))
  }, [])

  const worst = rows.reduce((max, r) => Math.max(max, r.median_resolution_hours ?? 0), 0) || 1

  return (
    <div className="panel">
      <h2>Ward accountability</h2>
      <p className="muted small">
        The city is scored too. Median resolution time and reopen rate are the municipality's own
        public metrics; the equity multiplier shows where reporting is thinner than expected.
        "Awaiting verify" counts reports held back for a citizen verifier — a ward where that
        stays high has thin verifier coverage, not thin demand.
      </p>
      <Err>{error}</Err>
      {rows.length === 0 ? (
        <Empty>No issues recorded yet.</Empty>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Ward</th>
              <th className="num">Open</th>
              <th className="num">Awaiting verify</th>
              <th className="num">Total</th>
              <th className="num">Median resolution</th>
              <th className="num">Reopen rate</th>
              <th className="num">Expected rate</th>
              <th className="num">Observed</th>
              <th className="num">Equity</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.ward} style={{ cursor: 'default' }}>
                <td>{r.ward.replace('WARD-', '')}</td>
                <td className="num">{r.open_issues}</td>
                <td className="num">
                  {r.awaiting_verification > 0 ? (
                    r.awaiting_verification
                  ) : (
                    <span className="muted">—</span>
                  )}
                </td>
                <td className="num">{r.total_issues}</td>
                <td className="num">
                  {r.median_resolution_hours == null ? (
                    <span className="muted">—</span>
                  ) : (
                    <>
                      {fmt(r.median_resolution_hours, 1)}h
                      <span
                        aria-hidden="true"
                        style={{
                          display: 'inline-block',
                          marginLeft: 6,
                          height: 6,
                          width: `${Math.round((r.median_resolution_hours / worst) * 40)}px`,
                          background: 'var(--accent)',
                          borderRadius: 3,
                          verticalAlign: 'middle',
                        }}
                      />
                    </>
                  )}
                </td>
                <td className="num">{fmt(r.reopen_rate * 100, 0)}%</td>
                <td className="num">{fmt(r.expected_report_rate, 0)}</td>
                <td className="num">{fmt(r.observed_report_rate, 0)}</td>
                <td className="num">
                  <strong>{fmt(r.equity_multiplier, 2)}×</strong>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
