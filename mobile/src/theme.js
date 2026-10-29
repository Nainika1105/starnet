import { Appearance } from 'react-native'

const dark = {
  bg: '#101317',
  panel: '#191d23',
  border: '#2a3038',
  text: '#e9edf2',
  muted: '#9aa4b0',
  accent: '#6c9bff',
  accentSoft: '#1d2838',
  ok: '#5ec98c',
  warn: '#e0a93a',
  danger: '#ef7a72',
}

const light = {
  bg: '#f6f7f9',
  panel: '#ffffff',
  border: '#e3e6ea',
  text: '#16191d',
  muted: '#666f7a',
  accent: '#1d5fd0',
  accentSoft: '#eaf0fc',
  ok: '#1d7a4c',
  warn: '#9a6400',
  danger: '#b3261e',
}

export const theme = Appearance.getColorScheme() === 'light' ? light : dark

export const STATUS_COLOR = {
  REPORTED: theme.muted,
  VERIFIED: theme.accent,
  ASSIGNED: theme.warn,
  RESOLVED: theme.ok,
  CONFIRMED: theme.ok,
  REOPENED: theme.danger,
  REJECTED: theme.muted,
  CLOSED: theme.muted,
}

export const VESTING_LABEL = {
  PENDING_VERIFICATION: 'Awaiting verification',
  PENDING_RESOLUTION: 'Awaiting repair',
  PENDING_CONFIRMATION: 'Awaiting fix-confirmation',
  PAID: 'Paid',
  FORFEITED: 'Forfeited',
}
