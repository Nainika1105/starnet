import React, { useCallback, useEffect, useState } from 'react'
import { Image, ScrollView, Text, View } from 'react-native'
import * as Location from 'expo-location'
import { api, mediaUrl } from '../lib/api'
import { theme } from '../theme'
import { Btn, KV, Pill, ago, s } from '../components/ui'

const STAGES = ['Reported', 'Verified', 'Assigned', 'Resolved', 'Confirmed']

const STAGE_FOR_STATUS = {
  REPORTED: 0,
  VERIFIED: 1,
  ASSIGNED: 2,
  RESOLVED: 3,
  CONFIRMED: 4,
  CLOSED: 4,
  REOPENED: 1,
  REJECTED: 0,
}

export default function IssueDetail({ issueId, onBack }) {
  const [issue, setIssue] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState(null)

  const load = useCallback(async () => {
    try {
      setIssue(await api.issue(issueId))
    } catch (e) {
      setError(e.message)
    }
  }, [issueId])

  useEffect(() => {
    load()
  }, [load])

  async function corroborate() {
    setBusy(true)
    setError(null)
    try {
      const { status } = await Location.getForegroundPermissionsAsync()
      if (status !== 'granted') throw new Error('Location permission is needed to corroborate.')
      const fix = await Location.getCurrentPositionAsync({})
      const response = await api.corroborate(
        issueId,
        fix.coords.latitude,
        fix.coords.longitude,
        'I see this too.',
      )
      setMessage(response.message)
      await load()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  if (!issue) {
    return (
      <ScrollView style={s.screen} contentContainerStyle={s.body}>
        <Btn title="Back" onPress={onBack} style={{ alignSelf: 'flex-start' }} />
        <Text style={[s.muted, { marginTop: 16 }]}>{error || 'Loading…'}</Text>
      </ScrollView>
    )
  }

  const reached = STAGE_FOR_STATUS[issue.status] ?? 0
  const mine = issue.my_roles.length > 0

  return (
    <ScrollView style={s.screen} contentContainerStyle={s.body}>
      <Btn title="Back" onPress={onBack} style={{ alignSelf: 'flex-start', marginBottom: 14 }} />

      <View style={s.between}>
        <Text style={s.h1}>{issue.category.replace('_', ' ')}</Text>
        <Pill label={issue.status} />
      </View>
      <Text style={[s.muted, { marginTop: 4, marginBottom: 14 }]}>
        {issue.ward.replace('WARD-', '')} · reported by {issue.discoverer_handle} ·{' '}
        {ago(issue.created_at)}
      </Text>

      {issue.photo_url ? (
        <Image
          source={{ uri: mediaUrl(issue.photo_url) }}
          style={{ width: '100%', height: 220, borderRadius: 10, marginBottom: 14 }}
        />
      ) : null}

      <Text style={s.label}>Progress</Text>
      <View style={s.card}>
        {STAGES.map((stage, index) => {
          const done = index <= reached
          return (
            <View key={stage} style={[s.row, { paddingVertical: 5 }]}>
              <View
                style={{
                  width: 9,
                  height: 9,
                  borderRadius: 5,
                  backgroundColor: done ? theme.accent : theme.border,
                }}
              />
              <Text style={[s.text, { color: done ? theme.text : theme.muted }]}>{stage}</Text>
              {index === 4 && done ? (
                <Text style={[s.muted, { color: theme.ok }]}>— reward fully released</Text>
              ) : null}
            </View>
          )
        })}
        {issue.reopen_count > 0 ? (
          <Text style={[s.muted, { color: theme.danger, marginTop: 8 }]}>
            Reopened {issue.reopen_count}× — a repair that did not hold.
          </Text>
        ) : null}
      </View>

      <Text style={s.label}>Details</Text>
      <View style={s.card}>
        <KV k="Priority" v={issue.priority.toFixed(3)} />
        <KV k="Equity multiplier" v={`${issue.equity_multiplier.toFixed(2)}x`} />
        <KV k="Confidence" v={issue.confidence.toFixed(3)} />
        <KV k="Reward pool" v={`${Number(issue.reward_total).toFixed(2)} pts`} />
        <KV k="Corroborations" v={String(issue.corroboration_count)} />
        {issue.assignee_label ? <KV k="Assigned to" v={issue.assignee_label} /> : null}
      </View>

      {mine ? (
        <View style={s.note}>
          <Text style={s.muted}>
            Your role here: {issue.my_roles.join(', ').toLowerCase()}. Track what has vested in
            your wallet.
          </Text>
        </View>
      ) : (
        <>
          <Btn
            title="I see this too"
            kind="primary"
            onPress={corroborate}
            loading={busy}
            disabled={!['REPORTED', 'VERIFIED', 'ASSIGNED', 'RESOLVED', 'REOPENED'].includes(issue.status)}
          />
          <Text style={[s.muted, { marginTop: 8 }]}>
            Corroborating raises the issue's confidence and earns you a share of its pool — paid,
            like everyone else's, only once the repair is confirmed.
          </Text>
        </>
      )}
      {message ? <Text style={[s.muted, { marginTop: 8, color: theme.ok }]}>{message}</Text> : null}
      {error ? <Text style={s.err}>{error}</Text> : null}

      <Text style={[s.label, { marginTop: 20 }]}>Reports ({issue.reports.length})</Text>
      {issue.reports.map((report) => (
        <View key={report.id} style={s.card}>
          <View style={s.between}>
            <Text style={s.text}>{report.reporter_handle}</Text>
            <Pill label={report.role} color={theme.muted} />
          </View>
          {report.description ? (
            <Text style={[s.muted, { marginTop: 6 }]}>{report.description}</Text>
          ) : null}
          <Text style={[s.muted, { marginTop: 6 }]}>{ago(report.submitted_at)}</Text>
        </View>
      ))}
    </ScrollView>
  )
}
