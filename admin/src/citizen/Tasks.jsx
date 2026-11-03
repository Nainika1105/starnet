import React, { useCallback, useEffect, useState } from 'react'
import { api, mediaUrl } from '../lib/api.js'
import { ago, Empty, Err, fmt, when } from '../components/bits.jsx'
import Capture from './Capture.jsx'

const KINDS = {
  VERIFY: {
    title: 'Verify a reported fault',
    prompt: 'Photograph the fault',
    explain:
      'Someone reported this and the confidence model could not settle it alone. Going to look is what admits it to the city’s queue — and releases the reporter’s first slice.',
    yes: 'The fault is there',
    no: 'Nothing wrong here',
  },
  CONFIRM: {
    title: 'Confirm a repair',
    prompt: 'Photograph the repair',
    explain:
      'The city says this is fixed. Nothing of the final 45% moves until somebody independent stands at the spot and says so — and that is this task.',
    yes: 'It is repaired',
    no: 'Still broken',
  },
}

export default function Tasks({ onDone }) {
  const [tasks, setTasks] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [captures, setCaptures] = useState({})
  const [busy, setBusy] = useState(null)
  const [outcome, setOutcome] = useState(null)

  const load = useCallback(() => {
    setLoading(true)
    api
      .myTasks()
      .then(setTasks)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  useEffect(load, [load])

  async function complete(task, verdict) {
    const capture = captures[task.id]
    if (verdict === 'CONFIRMED' && !capture) {
      setError('A photo is required to confirm. The server refuses a confirmation without one.')
      return
    }
    setError(null)
    setBusy(task.id)
    try {
      const response = await api.completeTask(task.id, verdict, capture?.blob)
      setOutcome({ verdict, ...response })
      setCaptures((current) => {
        const next = { ...current }
        delete next[task.id]
        return next
      })
      load()
      if (onDone) onDone()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="panel">
      <h2>Your tasks</h2>
      <p className="muted small">
        The system chooses who is asked. You cannot pick up a task on an issue you reported, nor
        one belonging to anyone you have repeatedly settled for, and a contractor can never
        nominate whoever signs off their own work.
      </p>

      <Err>{error}</Err>

      {outcome && (
        <p className={`banner ${outcome.verdict === 'CONFIRMED' ? 'ok' : 'warn'}`}>
          <strong>Issue is now {outcome.issue_status}</strong>
          <span>{outcome.message}</span>
        </p>
      )}

      {loading ? (
        <p className="muted">Loading…</p>
      ) : tasks.length === 0 ? (
        <Empty>
          Nothing routed to you right now. Tasks are a trust privilege — a brand-new account is
          never sent one, and routing prefers whoever is nearest the spot, then whoever is least
          loaded.
        </Empty>
      ) : (
        <div className="task-list">
          {tasks.map((task) => {
            const kind = KINDS[task.type] || KINDS.VERIFY
            const issue = task.issue
            return (
              <div className="task" key={task.id}>
                <div className="task-head">
                  <div>
                    <span className="tag">{task.type}</span>
                    <h3>{kind.title}</h3>
                    <p className="muted small">{kind.explain}</p>
                  </div>
                  {issue.photo_url && (
                    <img className="thumb" src={mediaUrl(issue.photo_url)} alt="The reported fault" />
                  )}
                </div>

                <table>
                  <tbody>
                    <tr>
                      <td>Category</td>
                      <td className="num">{issue.category}</td>
                    </tr>
                    <tr>
                      <td>Ward</td>
                      <td className="num">{issue.ward}</td>
                    </tr>
                    <tr>
                      <td>Where</td>
                      <td className="num">
                        {fmt(issue.lat, 4)}, {fmt(issue.lng, 4)}
                      </td>
                    </tr>
                    <tr>
                      <td>Reward pool</td>
                      <td className="num">{issue.reward_total} pts</td>
                    </tr>
                    <tr>
                      <td>Opened</td>
                      <td className="num">{ago(task.created_at)} ago</td>
                    </tr>
                    {task.expires_at && (
                      <tr>
                        <td>Expires</td>
                        <td className="num">{when(task.expires_at)}</td>
                      </tr>
                    )}
                  </tbody>
                </table>

                <Capture
                  label={kind.prompt}
                  captured={captures[task.id]}
                  onCapture={(capture) =>
                    setCaptures((current) => ({ ...current, [task.id]: capture }))
                  }
                />

                <div className="row">
                  <button
                    className="btn primary"
                    disabled={busy === task.id}
                    onClick={() => complete(task, 'CONFIRMED')}
                  >
                    {kind.yes}
                  </button>
                  <button
                    className="btn danger"
                    disabled={busy === task.id}
                    onClick={() => complete(task, 'REJECTED')}
                  >
                    {kind.no}
                  </button>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
