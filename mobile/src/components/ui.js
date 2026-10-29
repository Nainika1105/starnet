import React from 'react'
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native'
import { STATUS_COLOR, theme } from '../theme'

export const s = StyleSheet.create({
  screen: { flex: 1, backgroundColor: theme.bg },
  body: { padding: 16, paddingBottom: 48 },
  h1: { color: theme.text, fontSize: 24, fontWeight: '700', letterSpacing: -0.3 },
  h2: { color: theme.text, fontSize: 17, fontWeight: '600' },
  label: {
    color: theme.muted,
    fontSize: 11,
    fontWeight: '700',
    letterSpacing: 0.8,
    textTransform: 'uppercase',
    marginBottom: 8,
  },
  text: { color: theme.text, fontSize: 15, lineHeight: 21 },
  muted: { color: theme.muted, fontSize: 13, lineHeight: 19 },
  card: {
    backgroundColor: theme.panel,
    borderColor: theme.border,
    borderWidth: 1,
    borderRadius: 12,
    padding: 14,
    marginBottom: 12,
  },
  input: {
    backgroundColor: theme.bg,
    borderColor: theme.border,
    borderWidth: 1,
    borderRadius: 9,
    paddingHorizontal: 12,
    paddingVertical: 11,
    color: theme.text,
    fontSize: 15,
    marginBottom: 10,
  },
  row: { flexDirection: 'row', alignItems: 'center', gap: 8, flexWrap: 'wrap' },
  between: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  note: { backgroundColor: theme.accentSoft, borderRadius: 9, padding: 11, marginBottom: 12 },
  err: { color: theme.danger, fontSize: 13, marginTop: 6 },
  big: { color: theme.text, fontSize: 34, fontWeight: '700', letterSpacing: -0.5 },
  divider: { height: 1, backgroundColor: theme.border, marginVertical: 12 },
})

export function Btn({ title, onPress, kind = 'default', disabled, loading, style }) {
  const tone =
    kind === 'primary'
      ? { backgroundColor: theme.accent, borderColor: theme.accent }
      : kind === 'danger'
        ? { backgroundColor: 'transparent', borderColor: theme.danger }
        : { backgroundColor: theme.panel, borderColor: theme.border }

  const textColor = kind === 'primary' ? '#fff' : kind === 'danger' ? theme.danger : theme.text

  return (
    <Pressable
      onPress={onPress}
      disabled={disabled || loading}
      style={({ pressed }) => [
        {
          borderWidth: 1,
          borderRadius: 9,
          paddingVertical: 12,
          paddingHorizontal: 16,
          alignItems: 'center',
          opacity: disabled || loading ? 0.45 : pressed ? 0.75 : 1,
        },
        tone,
        style,
      ]}
    >
      {loading ? (
        <ActivityIndicator color={textColor} />
      ) : (
        <Text style={{ color: textColor, fontWeight: '600', fontSize: 15 }}>{title}</Text>
      )}
    </Pressable>
  )
}

export function Pill({ label, color }) {
  const tone = color || STATUS_COLOR[label] || theme.muted
  return (
    <View
      style={{
        borderRadius: 999,
        paddingHorizontal: 9,
        paddingVertical: 3,
        backgroundColor: tone + '22',
      }}
    >
      <Text style={{ color: tone, fontSize: 11, fontWeight: '700', letterSpacing: 0.3 }}>
        {label}
      </Text>
    </View>
  )
}

export function KV({ k, v }) {
  return (
    <View style={[s.between, { paddingVertical: 4 }]}>
      <Text style={s.muted}>{k}</Text>
      <Text style={[s.text, { fontVariant: ['tabular-nums'] }]}>{v}</Text>
    </View>
  )
}

export const pts = (n, dp = 2) => (n == null ? '—' : `${Number(n).toFixed(dp)} pts`)

export const ago = (iso) => {
  if (!iso) return ''
  const hours = (Date.now() - new Date(iso).getTime()) / 3_600_000
  if (hours < 1) return `${Math.max(1, Math.round(hours * 60))}m ago`
  if (hours < 48) return `${Math.round(hours)}h ago`
  return `${Math.round(hours / 24)}d ago`
}
