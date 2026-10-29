import React from 'react'

export const fmt = (n, dp = 2) =>
  n == null || n === '' ? '—' : Number(n).toFixed(dp)

export const when = (iso) =>
  iso ? new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : '—'

export const ago = (iso) => {
  if (!iso) return '—'
  const hours = (Date.now() - new Date(iso).getTime()) / 3_600_000
  if (hours < 1) return `${Math.max(1, Math.round(hours * 60))}m`
  if (hours < 48) return `${Math.round(hours)}h`
  return `${Math.round(hours / 24)}d`
}

export const Pill = ({ status }) => <span className={`pill ${status}`}>{status}</span>

export function Err({ children }) {
  if (!children) return null
  return <p className="err">{String(children)}</p>
}

export function Empty({ children }) {
  return <p className="muted small">{children}</p>
}
