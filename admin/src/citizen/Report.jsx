import React, { useEffect, useState } from 'react'
import { api } from '../lib/api.js'
import { Err, fmt } from '../components/bits.jsx'
import Capture from './Capture.jsx'

const CATEGORIES = [
  ['POTHOLE', 'Pothole'],
  ['WATER_LEAK', 'Water leak'],
  ['STREETLIGHT', 'Streetlight out'],
  ['GARBAGE', 'Garbage dumping'],
  ['TREE', 'Fallen tree or branch'],
  ['FOOTPATH', 'Broken footpath'],
]

// The serviced region, from engines/geo.py. A report outside it is refused
// outright, so the form keeps the reader inside it.
const SERVICE_AREA = { minLat: 12.8, minLng: 77.4, maxLat: 13.18, maxLng: 77.8 }
const DEFAULT = { lat: 12.9705, lng: 77.5921 }

function inServiceArea(lat, lng) {
  return (
    lat >= SERVICE_AREA.minLat &&
    lat <= SERVICE_AREA.maxLat &&
    lng >= SERVICE_AREA.minLng &&
    lng <= SERVICE_AREA.maxLng
  )
}

const DECISIONS = {
  ACCEPT: {
    tag: 'ok',
    title: 'Accepted into the municipal queue',
    body: 'Confidence cleared the upper threshold, so no on-site verification was needed. A small verification credit has vested — the rest waits on the repair.',
  },
  VERIFY: {
    tag: 'warn',
    title: 'Sent to a nearby trusted user to verify on site',
    body: 'This is the usual outcome for an uncorroborated report, and it is the design rather than a problem: one stranger’s photo should not commission public work. Nothing has vested yet.',
  },
  REJECT: {
    tag: 'bad',
    title: 'Below the confidence floor',
    body: 'Nothing vested and nothing was queued. A report with no EXIF from an account with no history has very little going for it — which is exactly the case the floor exists to catch.',
  },
  CORROBORATED: {
    tag: 'ok',
    title: 'Recorded as corroboration of an existing issue',
    body: 'A fault was already open within 30 metres, so this attached to it instead of opening a duplicate. Duplicates are never discarded — they are evidence.',
  },
}

export default function Report({ onFiled }) {
  const [category, setCategory] = useState('POTHOLE')
  const [position, setPosition] = useState(DEFAULT)
  const [locating, setLocating] = useState(false)
  const [locationNote, setLocationNote] = useState('Using a default point in WARD-CENTRAL.')
  const [description, setDescription] = useState('')
  const [captured, setCaptured] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null)

  useEffect(() => () => captured && URL.revokeObjectURL(captured.url), [captured])

  function locate() {
    if (!navigator.geolocation) {
      setLocationNote('This browser does not offer location. Enter coordinates by hand.')
      return
    }
    setLocating(true)
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        const { latitude, longitude } = pos.coords
        setLocating(false)
        if (!inServiceArea(latitude, longitude)) {
          setLocationNote(
            `Your location (${fmt(latitude, 4)}, ${fmt(longitude, 4)}) is outside the serviced ` +
              'region, which the server refuses. Keeping the demo point instead.',
          )
          return
        }
        setPosition({ lat: latitude, lng: longitude })
        setLocationNote('Using your device location.')
      },
      (err) => {
        setLocating(false)
        setLocationNote(`Location unavailable (${err.message}). Keeping the demo point.`)
      },
      { enableHighAccuracy: true, timeout: 8000 },
    )
  }

  async function submit(event) {
    event.preventDefault()
    setError(null)
    setBusy(true)
    try {
      const response = await api.submitReport({
        category,
        lat: position.lat,
        lng: position.lng,
        description: description.trim() || null,
        photo: captured?.blob,
      })
      setResult(response)
      setDescription('')
      setCaptured(null)
      if (onFiled) onFiled()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  if (result) {
    const shape = DECISIONS[result.decision] || {
      tag: 'warn',
      title: result.decision,
      body: result.message,
    }
    const issue = result.issue
    return (
      <div className="panel">
        <h2>Report filed</h2>
        <p className={`banner ${shape.tag}`}>
          <strong>{shape.title}</strong>
          <span>{shape.body}</span>
        </p>

        <table>
          <tbody>
            <tr>
              <td>Decision</td>
              <td className="num">{result.decision}</td>
            </tr>
            <tr>
              <td>Confidence C(r)</td>
              <td className="num">{fmt(issue.confidence, 3)}</td>
            </tr>
            <tr>
              <td>Priority P(I)</td>
              <td className="num">{fmt(issue.priority, 3)}</td>
            </tr>
            <tr>
              <td>Equity E(&#8467;)</td>
              <td className="num">{fmt(issue.equity_multiplier, 2)}&times;</td>
            </tr>
            <tr>
              <td>Reward pool R&#8336;</td>
              <td className="num">{issue.reward_total} pts</td>
            </tr>
            <tr>
              <td>Your role</td>
              <td className="num">{result.your_role}</td>
            </tr>
            <tr>
              <td>Issue status</td>
              <td className="num">{issue.status}</td>
            </tr>
          </tbody>
        </table>

        <p className="muted small" style={{ marginTop: 12 }}>
          {result.message}
        </p>
        <div className="row" style={{ marginTop: 14 }}>
          <button className="btn primary" onClick={() => setResult(null)}>
            File another
          </button>
        </div>
      </div>
    )
  }

  return (
    <form className="panel" onSubmit={submit}>
      <h2>Report a fault</h2>
      <p className="muted small">
        Filing pays almost nothing. The bulk of the reward is released only once the fault is
        repaired and somebody independent photographs the repair on site.
      </p>

      <Err>{error}</Err>

      <div className="field">
        <label htmlFor="category">What is wrong</label>
        <select id="category" value={category} onChange={(e) => setCategory(e.target.value)}>
          {CATEGORIES.map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </div>

      <div className="field">
        <label>Where</label>
        <div className="row">
          <input
            id="lat"
            type="number"
            step="0.0001"
            value={position.lat}
            onChange={(e) => setPosition({ ...position, lat: Number(e.target.value) })}
            aria-label="Latitude"
          />
          <input
            id="lng"
            type="number"
            step="0.0001"
            value={position.lng}
            onChange={(e) => setPosition({ ...position, lng: Number(e.target.value) })}
            aria-label="Longitude"
          />
          <button type="button" className="btn" onClick={locate} disabled={locating}>
            {locating ? 'Locating…' : 'Use my location'}
          </button>
        </div>
        <p className="small muted">{locationNote}</p>
        {!inServiceArea(position.lat, position.lng) && (
          <p className="small warn-text">
            Outside the serviced region, which the server refuses with a 400. The demo region is
            roughly {SERVICE_AREA.minLat}–{SERVICE_AREA.maxLat} latitude,{' '}
            {SERVICE_AREA.minLng}–{SERVICE_AREA.maxLng} longitude.
          </p>
        )}
      </div>

      <div className="field">
        <label htmlFor="description">Anything worth adding</label>
        <textarea
          id="description"
          rows="2"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="Deep pothole in the right lane, opposite the school gate."
        />
      </div>

      <div className="field">
        <label>Photo</label>
        <Capture captured={captured} onCapture={setCaptured} />
      </div>

      <div className="row">
        <button className="btn primary" type="submit" disabled={busy || !inServiceArea(position.lat, position.lng)}>
          {busy ? 'Filing…' : 'File the report'}
        </button>
        {!captured && (
          <span className="small muted">
            A report with no photo is accepted, and scores considerably worse for it.
          </span>
        )}
      </div>
    </form>
  )
}
