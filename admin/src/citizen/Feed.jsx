import React, { useCallback, useEffect, useState } from 'react'
import { api, mediaUrl } from '../lib/api.js'
import { ago, Empty, Err, fmt, Pill } from '../components/bits.jsx'

export default function Feed({ me, refreshKey }) {
  const [issues, setIssues] = useState([])
  const [selected, setSelected] = useState(null)
  const [detail, setDetail] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState(null)

  const load = useCallback(() => {
    api
      .allIssues()
      .then(setIssues)
      .catch((e) => setError(e.message))
  }, [])

  useEffect(load, [load, refreshKey])

  useEffect(() => {
    if (!selected) {
      setDetail(null)
      return
    }
    setDetail(null)
    api.issue(selected).then(setDetail).catch((e) => setError(e.message))
  }, [selected])

  async function corroborate() {
    if (!detail) return
    setError(null)
    setBusy(true)
    try {
      const response = await api.corroborate(
        detail.id,
        detail.lat,
        detail.lng,
        'I see this too.',
      )
      setNotice(response.message)
      api.issue(detail.id).then(setDetail).catch(() => {})
      load()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const mine = detail && (detail.my_roles || []).length > 0
  const isDiscoverer = detail && detail.discoverer_handle === me?.handle

  return (
    <div className="split">
      <div className="panel">
        <h2>Faults near you</h2>
        <p className="muted small">
          Every open issue in the serviced region. Seeing one you also know about is worth
          reporting — a second independent sighting raises the issue's confidence and buys you a
          share of the corroborator pool, which pays out on the same terms as everyone else's.
        </p>
        <Err>{error}</Err>
        {issues.length === 0 ? (
          <Empty>Nothing open right now.</Empty>
        ) : (
          <div className="ledger-wrap">
            <table>
              <thead>
                <tr>
                  <th>Category</th>
                  <th>Ward</th>
                  <th>Status</th>
                  <th className="num">Corrob.</th>
                  <th className="num">Pool</th>
                  <th className="num">Age</th>
                </tr>
              </thead>
              <tbody>
                {issues.map((issue) => (
                  <tr
                    key={issue.id}
                    aria-selected={selected === issue.id}
                    onClick={() => setSelected(issue.id)}
                  >
                    <td>{issue.category}</td>
                    <td className="small">{issue.ward.replace('WARD-', '')}</td>
                    <td>
                      <Pill status={issue.status} />
                    </td>
                    <td className="num">{issue.corroboration_count}</td>
                    <td className="num">{issue.reward_total}</td>
                    <td className="num">{ago(issue.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="panel">
        <h2>Issue detail</h2>
        {!detail ? (
          <Empty>{selected ? 'Loading…' : 'Pick an issue from the list.'}</Empty>
        ) : (
          <>
            {notice && <p className="banner ok">{notice}</p>}

            {detail.photo_url && (
              <img className="detail-photo" src={mediaUrl(detail.photo_url)} alt="The reported fault" />
            )}

            <table>
              <tbody>
                <tr>
                  <td>Category</td>
                  <td className="num">{detail.category}</td>
                </tr>
                <tr>
                  <td>Status</td>
                  <td>
                    <Pill status={detail.status} />
                  </td>
                </tr>
                <tr>
                  <td>Reported by</td>
                  <td className="num">{detail.discoverer_handle}</td>
                </tr>
                <tr>
                  <td>Confidence</td>
                  <td className="num">{fmt(detail.confidence, 3)}</td>
                </tr>
                <tr>
                  <td>Corroborations</td>
                  <td className="num">{detail.corroboration_count}</td>
                </tr>
                <tr>
                  <td>Reward pool</td>
                  <td className="num">{detail.reward_total} pts</td>
                </tr>
                <tr>
                  <td>Reopened</td>
                  <td className="num">{detail.reopen_count} times</td>
                </tr>
                {detail.assignee_label && (
                  <tr>
                    <td>Assigned to</td>
                    <td className="num">{detail.assignee_label}</td>
                  </tr>
                )}
              </tbody>
            </table>

            <div className="row" style={{ marginTop: 14 }}>
              <button
                className="btn primary"
                disabled={busy || mine || isDiscoverer}
                onClick={corroborate}
              >
                {busy ? 'Recording…' : 'I see this too'}
              </button>
              {isDiscoverer && <span className="small muted">You reported this one.</span>}
              {mine && !isDiscoverer && (
                <span className="small muted">You already have a stake in this issue.</span>
              )}
            </div>

            <h3 style={{ marginTop: 18 }}>What has happened</h3>
            <ul className="timeline">
              {detail.timeline.map((event, index) => (
                <li key={index}>
                  <span className="num small">{event.event_type}</span>
                  <span className="muted small">{ago(event.created_at)} ago</span>
                  {event.payload?.reason && (
                    <span className="muted small"> — {event.payload.reason}</span>
                  )}
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    </div>
  )
}
