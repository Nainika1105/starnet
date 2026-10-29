import React, { useCallback, useEffect, useState } from 'react'
import { Image, RefreshControl, ScrollView, Text, View } from 'react-native'
import { api, mediaUrl } from '../lib/api'
import { theme } from '../theme'
import { Btn, KV, Pill, ago, s } from '../components/ui'
import LiveCamera from '../components/LiveCamera'

export default function Tasks({ me, onDone }) {
  const [tasks, setTasks] = useState([])
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState(null)
  const [activeTask, setActiveTask] = useState(null)
  const [photoByTask, setPhotoByTask] = useState({})
  const [busyId, setBusyId] = useState(null)
  const [message, setMessage] = useState(null)

  const load = useCallback(async () => {
    setRefreshing(true)
    setError(null)
    try {
      setTasks(await api.myTasks())
    } catch (e) {
      setError(e.message)
    } finally {
      setRefreshing(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  async function complete(task, verdict) {
    setBusyId(task.id)
    setError(null)
    try {
      const response = await api.completeTask(task.id, verdict, photoByTask[task.id])
      setMessage(response.message)
      setPhotoByTask((prev) => ({ ...prev, [task.id]: undefined }))
      await load()
      onDone?.()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusyId(null)
    }
  }

  return (
    <ScrollView
      style={s.screen}
      contentContainerStyle={s.body}
      refreshControl={
        <RefreshControl refreshing={refreshing} onRefresh={load} tintColor={theme.accent} />
      }
    >
      <Text style={s.h1}>Your tasks</Text>
      <Text style={[s.muted, { marginTop: 4, marginBottom: 14 }]}>
        The system picks who verifies and who confirms — never the reporter, never the crew.
        Tasks only reach accounts that have earned the trust tier for them.
      </Text>

      {me?.tier === 'NEW' ? (
        <View style={s.note}>
          <Text style={s.muted}>
            Your account is still at the New tier, so no verification tasks are routed to you
            yet. File accurate reports that get confirmed and your tier rises.
          </Text>
        </View>
      ) : null}

      {message ? (
        <View style={s.note}>
          <Text style={[s.muted, { color: theme.ok }]}>{message}</Text>
        </View>
      ) : null}
      {error ? <Text style={s.err}>{error}</Text> : null}

      {tasks.length === 0 ? (
        <Text style={s.muted}>
          {refreshing ? 'Loading…' : 'Nothing waiting on you right now.'}
        </Text>
      ) : null}

      {tasks.map((task) => {
        const photo = photoByTask[task.id]
        const isConfirm = task.type === 'CONFIRM'
        return (
          <View key={task.id} style={s.card}>
            <View style={s.between}>
              <Text style={s.h2}>
                {isConfirm ? 'Confirm a repair' : 'Verify a report'}
              </Text>
              <Pill label={task.type} color={isConfirm ? theme.ok : theme.accent} />
            </View>

            <Text style={[s.muted, { marginTop: 6, marginBottom: 10 }]}>
              {isConfirm
                ? 'Go to the location and photograph the repair. Your photo releases the final 45% of everyone’s reward — or reopens the issue if the fault is still there.'
                : 'This report needs an independent look. Confirm the fault exists, or reject it.'}
            </Text>

            {task.issue.photo_url ? (
              <Image
                source={{ uri: mediaUrl(task.issue.photo_url) }}
                style={{ width: '100%', height: 170, borderRadius: 8, marginBottom: 10 }}
              />
            ) : null}

            <KV k="Category" v={task.issue.category.replace('_', ' ')} />
            <KV k="Ward" v={task.issue.ward.replace('WARD-', '')} />
            <KV k="Location" v={`${task.issue.lat.toFixed(5)}, ${task.issue.lng.toFixed(5)}`} />
            <KV k="Opened" v={ago(task.created_at)} />
            {task.expires_at ? <KV k="Expires" v={new Date(task.expires_at).toLocaleString()} /> : null}

            <View style={s.divider} />

            {photo ? (
              <Image
                source={{ uri: photo }}
                style={{ width: '100%', height: 170, borderRadius: 8, marginBottom: 10 }}
              />
            ) : null}

            <Btn
              title={photo ? 'Retake photo' : 'Take photo on site'}
              onPress={() => setActiveTask(task)}
              style={{ marginBottom: 8 }}
            />
            <Btn
              title={isConfirm ? 'Repair looks good' : 'Fault is real'}
              kind="primary"
              onPress={() => complete(task, 'CONFIRMED')}
              loading={busyId === task.id}
              disabled={!photo}
              style={{ marginBottom: 8 }}
            />
            <Btn
              title={isConfirm ? 'Still broken' : 'No fault here'}
              kind="danger"
              onPress={() => complete(task, 'REJECTED')}
              loading={busyId === task.id}
            />
            {!photo ? (
              <Text style={[s.muted, { marginTop: 8 }]}>
                A timestamped photo is required before you can confirm.
              </Text>
            ) : null}
          </View>
        )
      })}

      <LiveCamera
        visible={activeTask != null}
        onClose={() => setActiveTask(null)}
        onCapture={(uri) =>
          setPhotoByTask((prev) => ({ ...prev, [activeTask.id]: uri }))
        }
        prompt={
          activeTask?.type === 'CONFIRM'
            ? 'Photograph the repaired spot.'
            : 'Photograph the fault as you found it.'
        }
      />
    </ScrollView>
  )
}
