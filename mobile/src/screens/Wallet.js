import React, { useCallback, useEffect, useState } from 'react'
import { RefreshControl, ScrollView, Text, View } from 'react-native'
import { api } from '../lib/api'
import { VESTING_LABEL, theme } from '../theme'
import { Btn, KV, Pill, ago, pts, s } from '../components/ui'

const STATE_COLOR = {
  PENDING_VERIFICATION: theme.muted,
  PENDING_RESOLUTION: theme.warn,
  PENDING_CONFIRMATION: theme.accent,
  PAID: theme.ok,
  FORFEITED: theme.danger,
}

const randomNonce = () =>
  `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`

export default function Wallet({ me }) {
  const [wallet, setWallet] = useState(null)
  const [catalog, setCatalog] = useState([])
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState(null)
  const [message, setMessage] = useState(null)
  const [busySku, setBusySku] = useState(null)

  const load = useCallback(async () => {
    setRefreshing(true)
    setError(null)
    try {
      const [w, c] = await Promise.all([api.wallet(), api.catalog()])
      setWallet(w)
      setCatalog(c)
    } catch (e) {
      setError(e.message)
    } finally {
      setRefreshing(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  async function redeem(item) {
    setBusySku(item.sku)
    setError(null)
    try {
      // The nonce is generated once per tap, so a double tap cannot double charge.
      const response = await api.redeem(item.sku, randomNonce())
      setMessage(response.message)
      await load()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusySku(null)
    }
  }

  const pending = wallet?.pending_by_state || {}
  const pendingTotal = Object.values(pending).reduce((sum, v) => sum + Number(v), 0)

  return (
    <ScrollView
      style={s.screen}
      contentContainerStyle={s.body}
      refreshControl={
        <RefreshControl refreshing={refreshing} onRefresh={load} tintColor={theme.accent} />
      }
    >
      <Text style={s.h1}>Wallet</Text>
      <Text style={[s.muted, { marginTop: 4, marginBottom: 14 }]}>
        Your tier is {me?.tier?.replace('_', ' ').toLowerCase()}. Points are released in stages
        as each issue you touched moves toward a confirmed repair.
      </Text>

      <View style={s.card}>
        <Text style={s.label}>Available</Text>
        <Text style={s.big}>{wallet ? Number(wallet.balance).toFixed(2) : '—'}</Text>
        <Text style={s.muted}>
          {pendingTotal > 0
            ? `${pendingTotal.toFixed(2)} pts still vesting`
            : 'nothing currently vesting'}
        </Text>
      </View>

      {Object.keys(pending).length > 0 ? (
        <>
          <Text style={s.label}>Vesting</Text>
          <View style={s.card}>
            {Object.entries(pending).map(([state, amount]) => (
              <KV key={state} k={VESTING_LABEL[state] || state} v={pts(amount)} />
            ))}
            <Text style={[s.muted, { marginTop: 8 }]}>
              Nothing here is lost — it is waiting on the city to repair the fault and on a
              neighbour to confirm it.
            </Text>
          </View>
        </>
      ) : null}

      {wallet?.contributions?.length ? (
        <>
          <Text style={s.label}>Your contributions</Text>
          {wallet.contributions.map((c) => (
            <View key={`${c.issue_id}-${c.role}`} style={s.card}>
              <View style={s.between}>
                <Text style={s.h2}>{c.category.replace('_', ' ')}</Text>
                <Pill label={c.vesting_state.replace(/_/g, ' ')} color={STATE_COLOR[c.vesting_state]} />
              </View>
              <Text style={[s.muted, { marginTop: 4, marginBottom: 8 }]}>
                as {c.role.toLowerCase()} · issue is {c.issue_status.toLowerCase()}
              </Text>
              <KV k="Entitlement" v={pts(c.entitlement)} />
              <KV k="Vested so far" v={pts(c.vested_so_far)} />
            </View>
          ))}
        </>
      ) : null}

      <Text style={s.label}>Redeem</Text>
      {message ? (
        <View style={s.note}>
          <Text style={[s.muted, { color: theme.ok }]}>{message}</Text>
        </View>
      ) : null}
      {error ? <Text style={s.err}>{error}</Text> : null}
      {catalog.map((item) => {
        const affordable = wallet && Number(wallet.balance) >= Number(item.cost)
        return (
          <View key={item.sku} style={s.card}>
            <View style={s.between}>
              <Text style={s.h2}>{item.name}</Text>
              <Text style={s.text}>{Number(item.cost).toFixed(0)}</Text>
            </View>
            <Text style={[s.muted, { marginTop: 4, marginBottom: 10 }]}>{item.description}</Text>
            <Btn
              title={affordable ? 'Redeem' : 'Not enough points'}
              kind={affordable ? 'primary' : 'default'}
              onPress={() => redeem(item)}
              disabled={!affordable}
              loading={busySku === item.sku}
            />
          </View>
        )
      })}

      <Text style={[s.label, { marginTop: 20 }]}>Ledger</Text>
      <Text style={[s.muted, { marginBottom: 10 }]}>
        Append-only. Every entry below is permanent.
      </Text>
      {wallet?.ledger?.length ? (
        wallet.ledger.map((entry) => (
          <View key={entry.id} style={[s.card, { paddingVertical: 10 }]}>
            <View style={s.between}>
              <Text style={s.text}>
                {entry.stage === 'NONE' ? entry.entry_type : entry.stage.toLowerCase()}
              </Text>
              <Text
                style={[
                  s.text,
                  { color: Number(entry.amount) < 0 ? theme.danger : theme.ok, fontWeight: '600' },
                ]}
              >
                {Number(entry.amount) > 0 ? '+' : ''}
                {Number(entry.amount).toFixed(2)}
              </Text>
            </View>
            <Text style={[s.muted, { marginTop: 2 }]}>
              {entry.role ? `${entry.role.toLowerCase()} · ` : ''}
              {ago(entry.created_at)}
              {entry.note ? ` · ${entry.note}` : ''}
            </Text>
          </View>
        ))
      ) : (
        <Text style={s.muted}>No entries yet.</Text>
      )}
    </ScrollView>
  )
}
