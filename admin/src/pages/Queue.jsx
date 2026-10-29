import React, { useCallback, useEffect, useState } from 'react'
import { api, mediaUrl } from '../lib/api.js'
import { Empty, Err, Pill, ago, fmt, when } from '../components/bits.jsx'
import IssueMap from '../components/IssueMap.jsx'

const CATEGORIES = ['POTHOLE', 'STREETLIGHT', 'WATER_LEAK', 'GARBAGE', 'TREE', 'FOOTPATH']
const WARDS = ['WARD-NORTH', 'WARD-CENTRAL', 'WARD-SOUTH', 'WARD-EAST', 'WARD-WEST']
// REPORTED is listed although it is not municipal work: those issues are
// waiting on a citizen verifier, and an admin should be able to see what is
// held up rather than only what has already been admitted to the queue.
const STATUSES = [
  'REPORTED',
  'VERIFIED',
  'ASSIGNED',
  'REOPENED',
  'RESOLVED',
  'CONFIRMED',
  'REJECTED',
]

export default function Queue() {
  const [rows, setRows] = useState([])
  const [filters, setFilters] = useState({ ward: '', category: '', status: '' })
  const [selectedId, setSelectedId] = useState(null)
  const [detail, setDetail] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    setError(null)
    try {
      setRows(await api.queue(filters))
    } catch (e) {
      setError(e.message)
    }
  }, [filters])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    if (!selectedId) {
      setDetail(null)
      return
    }
    api.issue(selectedId).then(setDetail).catch((e) => setError(e.message))
  }, [selectedId])

  async function act(fn) {
    setBusy(true)
    setError(null)
    try {
      const updated = await fn()
      setDetail(updated)
      await load()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <div className="panel">
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <div className="row">
            <select value={filters.status} onChange={(e) => setFilters({ ...filters, status: e.target.value })}>
              <option value="">Needs action</option>
              {STATUSES.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
            <select value={filters.ward} onChange={(e) => setFilters({ ...filters, ward: e.target.value })}>
              <option value="">All wards</option>
              {WARDS.map((w) => (
                <option key={w} value={w}>{w}</option>
              ))}
            </select>
            <select value={filters.category} onChange={(e) => setFilters({ ...filters, category: e.target.value })}>
              <option value="">All categories</option>
              {CATEGORIES.map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
          </div>
          <button className="btn" onClick={load}>Refresh</button>
        </div>
        <Err>{error}</Err>
      </div>

      <div className="grid-2">
        <div className="panel">
          <h2>Ranked queue · {rows.length} issue{rows.length === 1 ? '' : 's'}</h2>
          <p className="muted small">
            Ordered by priority, then equity multiplier, then age — so an under-reported ward
            is not outranked by a newer report from a loud one.
          </p>
          {rows.length === 0 ? (
            <Empty>Nothing matches these filters.</Empty>
          ) : (
            <table>
              <thead>
                <tr>
                  <th className="num">P</th>
                  <th>Category</th>
                  <th>Ward</th>
                  <th>Status</th>
                  <th className="num">E</th>
                  <th className="num">Corrob.</th>
                  <th className="num">Reward</th>
                  <th className="num">Age</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr
                    key={r.id}
                    aria-selected={r.id === selectedId}
                    onClick={() => setSelectedId(r.id)}
                  >
                    <td className="num"><strong>{fmt(r.priority)}</strong></td>
                    <td>{r.category}</td>
                    <td className="small">{r.ward.replace('WARD-', '')}</td>
                    <td><Pill status={r.status} /></td>
                    <td className="num">{fmt(r.equity_multiplier, 1)}×</td>
                    <td className="num">{r.corroboration_count}</td>
                    <td className="num">{fmt(r.reward_total)}</td>
                    <td className="num small">{ago(r.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div>
          <div className="panel">
            <h3>Open issues</h3>
            <IssueMap issues={rows} selectedId={selectedId} onSelect={setSelectedId} />
          </div>
          <IssuePanel detail={detail} busy={busy} act={act} />
        </div>
      </div>
    </>
  )
}

function IssuePanel({ detail, busy, act }) {
  const [crew, setCrew] = useState('Road Works Crew 1')
  const [reason, setReason] = useState('')

  if (!detail) {
    return (
      <div className="panel">
        <h3>Issue detail</h3>
        <Empty>Select an issue from the queue.</Empty>
      </div>
    )
  }

  const canAssign = ['VERIFIED', 'REOPENED'].includes(detail.status)
  const canResolve = ['VERIFIED', 'ASSIGNED', 'REOPENED'].includes(detail.status)

  return (
    <div className="panel">
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <h3 style={{ margin: 0 }}>{detail.category}</h3>
        <Pill status={detail.status} />
      </div>

      {detail.photo_url && (
        <p><img className="photo" src={mediaUrl(detail.photo_url)} alt={`Reported ${detail.category}`} /></p>
      )}

      <dl className="kv">
        <dt>Reported by</dt><dd>{detail.discoverer_handle}</dd>
        <dt>Ward</dt><dd>{detail.ward}</dd>
        <dt>Priority</dt><dd>{fmt(detail.priority)}</dd>
        <dt>Equity</dt><dd>{fmt(detail.equity_multiplier, 2)}×</dd>
        <dt>Confidence</dt><dd>{fmt(detail.confidence)}</dd>
        <dt>Reward pool</dt><dd>{fmt(detail.reward_total)} pts</dd>
        <dt>Corroborations</dt><dd>{detail.corroboration_count}</dd>
        {detail.assignee_label && (<><dt>Assigned to</dt><dd>{detail.assignee_label}</dd></>)}
        {detail.reopen_count > 0 && (<><dt>Reopened</dt><dd>{detail.reopen_count}×</dd></>)}
      </dl>

      {detail.status === 'RESOLVED' && (
        <p className="note">
          Marked resolved — the final 45% of every reward is withheld until an independent
          citizen photographs the repair on site.
        </p>
      )}

      <h3 style={{ marginTop: 16 }}>Actions</h3>
      <div className="row">
        <input value={crew} onChange={(e) => setCrew(e.target.value)} placeholder="Crew or contractor" />
        <button className="btn" disabled={busy || !canAssign} onClick={() => act(() => api.assign(detail.id, crew))}>
          Assign
        </button>
        <button className="btn primary" disabled={busy || !canResolve} onClick={() => act(() => api.resolve(detail.id))}>
          Mark resolved
        </button>
      </div>
      <div className="row" style={{ marginTop: 8 }}>
        <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Reason" />
        <button
          className="btn danger"
          disabled={busy || !reason}
          onClick={() => act(() => api.reject(detail.id, reason))}
        >
          Reject
        </button>
        <button
          className="btn danger"
          disabled={busy || !reason}
          onClick={() => act(() => api.reopen(detail.id, reason))}
        >
          Reopen
        </button>
        <button
          className="btn"
          disabled={busy || !reason}
          onClick={() => act(() => api.closeOffApp(detail.id, reason))}
        >
          Fixed off-app
        </button>
      </div>

      <h3 style={{ marginTop: 16 }}>Timeline</h3>
      <ul className="timeline">
        {detail.timeline.map((e, i) => (
          <li key={i}>
            <strong>{e.event_type}</strong>
            {e.payload?.reason ? ` — ${e.payload.reason}` : ''}
            <div className="when">{when(e.created_at)}</div>
          </li>
        ))}
      </ul>

      <h3 style={{ marginTop: 16 }}>Reports ({detail.reports.length})</h3>
      <table>
        <thead>
          <tr>
            <th>Who</th>
            <th>Role</th>
            <th className="num">s_img</th>
            <th className="num">s_geo</th>
            {/* How far the photo's own EXIF GPS sat from the claimed pin. This
                is the geo check made inspectable: a large number next to a low
                s_geo says why the score is low. */}
            <th className="num" title="Photo EXIF GPS vs the claimed location">GPS Δ</th>
            <th className="num">ρ</th>
          </tr>
        </thead>
        <tbody>
          {detail.reports.map((r) => (
            <tr key={r.id} style={{ cursor: 'default' }}>
              <td>{r.reporter_handle}</td>
              <td className="small">{r.role}</td>
              <td className="num">{fmt(r.s_img)}</td>
              <td className="num">{fmt(r.s_geo)}</td>
              <td className="num">
                {r.photo_gps_distance_m == null ? (
                  <span className="muted" title="The photo carried no GPS">—</span>
                ) : (
                  `${fmt(r.photo_gps_distance_m, 0)}m`
                )}
              </td>
              <td className="num">{fmt(r.independence_weight)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
