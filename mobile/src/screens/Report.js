import React, { useEffect, useState } from 'react'
import { Image, ScrollView, Text, TextInput, View } from 'react-native'
import * as Location from 'expo-location'
import { api } from '../lib/api'
import { theme } from '../theme'
import { Btn, KV, s } from '../components/ui'
import LiveCamera from '../components/LiveCamera'

const CATEGORIES = [
  ['POTHOLE', 'Pothole'],
  ['STREETLIGHT', 'Streetlight'],
  ['WATER_LEAK', 'Water leak'],
  ['GARBAGE', 'Garbage'],
  ['TREE', 'Fallen tree'],
  ['FOOTPATH', 'Footpath'],
]

export default function Report({ onReported }) {
  const [category, setCategory] = useState('POTHOLE')
  const [description, setDescription] = useState('')
  const [photoUri, setPhotoUri] = useState(null)
  const [cameraOpen, setCameraOpen] = useState(false)
  const [coords, setCoords] = useState(null)
  const [locError, setLocError] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)

  useEffect(() => {
    ;(async () => {
      const { status } = await Location.requestForegroundPermissionsAsync()
      if (status !== 'granted') {
        setLocError('Location permission denied. A report needs a location to be actionable.')
        return
      }
      try {
        const position = await Location.getCurrentPositionAsync({
          accuracy: Location.Accuracy.High,
        })
        setCoords({ lat: position.coords.latitude, lng: position.coords.longitude })
      } catch {
        setLocError('Could not get a location fix.')
      }
    })()
  }, [])

  async function submit() {
    setBusy(true)
    setError(null)
    try {
      const response = await api.submitReport({
        category,
        lat: coords.lat,
        lng: coords.lng,
        description,
        photoUri,
      })
      setResult(response)
      setPhotoUri(null)
      setDescription('')
      onReported?.()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  if (result) {
    const issue = result.issue
    return (
      <ScrollView style={s.screen} contentContainerStyle={s.body}>
        <Text style={s.h1}>Report filed</Text>
        <Text style={[s.muted, { marginTop: 4, marginBottom: 16 }]}>{result.message}</Text>

        <View style={s.card}>
          <KV k="Status" v={issue.status} />
          <KV k="Confidence" v={issue.confidence.toFixed(3)} />
          <KV k="Priority" v={issue.priority.toFixed(3)} />
          <KV k="Equity multiplier" v={`${issue.equity_multiplier.toFixed(2)}x`} />
          <KV k="Reward pool" v={`${Number(issue.reward_total).toFixed(2)} pts`} />
          {result.joined_existing_issue ? <KV k="Your role" v="Corroborator" /> : null}
        </View>

        <View style={s.note}>
          <Text style={s.muted}>
            Only a small credit has vested so far. The rest is released when the fault is
            repaired and an independent neighbour photographs the repair — which is why a
            fabricated report can never pay.
          </Text>
        </View>

        <Btn title="File another report" kind="primary" onPress={() => setResult(null)} />
      </ScrollView>
    )
  }

  return (
    <ScrollView style={s.screen} contentContainerStyle={s.body}>
      <Text style={s.h1}>Report a fault</Text>
      <Text style={[s.muted, { marginTop: 4, marginBottom: 16 }]}>
        Photos must be taken here and now. There is no gallery upload.
      </Text>

      <Text style={s.label}>Category</Text>
      <View style={[s.row, { marginBottom: 16 }]}>
        {CATEGORIES.map(([value, label]) => (
          <Btn
            key={value}
            title={label}
            kind={category === value ? 'primary' : 'default'}
            onPress={() => setCategory(value)}
          />
        ))}
      </View>

      <Text style={s.label}>Photo</Text>
      {photoUri ? (
        <View style={{ marginBottom: 12 }}>
          <Image
            source={{ uri: photoUri }}
            style={{ width: '100%', height: 220, borderRadius: 10, marginBottom: 8 }}
          />
          <Btn title="Retake" onPress={() => setCameraOpen(true)} />
        </View>
      ) : (
        <Btn
          title="Open camera"
          kind="primary"
          onPress={() => setCameraOpen(true)}
          style={{ marginBottom: 16 }}
        />
      )}

      <Text style={s.label}>Location</Text>
      <View style={s.card}>
        {coords ? (
          <>
            <KV k="Latitude" v={coords.lat.toFixed(5)} />
            <KV k="Longitude" v={coords.lng.toFixed(5)} />
          </>
        ) : (
          <Text style={s.muted}>{locError || 'Getting a location fix…'}</Text>
        )}
      </View>

      <Text style={s.label}>Description (optional)</Text>
      <TextInput
        style={[s.input, { height: 88, textAlignVertical: 'top' }]}
        value={description}
        onChangeText={setDescription}
        placeholder="What is wrong, and how bad is it?"
        placeholderTextColor={theme.muted}
        multiline
      />

      <Btn
        title="Submit report"
        kind="primary"
        onPress={submit}
        loading={busy}
        disabled={!coords || !photoUri}
      />
      {!photoUri ? (
        <Text style={[s.muted, { marginTop: 8 }]}>
          A photo is required — without one the report cannot clear verification on its own.
        </Text>
      ) : null}
      {error ? <Text style={s.err}>{error}</Text> : null}

      <LiveCamera
        visible={cameraOpen}
        onClose={() => setCameraOpen(false)}
        onCapture={setPhotoUri}
        prompt="Frame the fault clearly, with some surroundings for context."
      />
    </ScrollView>
  )
}
