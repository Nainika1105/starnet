import React, { useCallback, useEffect, useState } from 'react'
import {
  FlatList,
  Image,
  Pressable,
  RefreshControl,
  Text,
  View,
} from 'react-native'
import MapView, { Marker } from 'react-native-maps'
import * as Location from 'expo-location'
import { api, mediaUrl } from '../lib/api'
import { STATUS_COLOR, theme } from '../theme'
import { Btn, Pill, ago, s } from '../components/ui'

export default function Feed({ onOpenIssue }) {
  const [view, setView] = useState('list')
  const [issues, setIssues] = useState([])
  const [coords, setCoords] = useState(null)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState(null)

  const load = useCallback(async () => {
    setRefreshing(true)
    setError(null)
    try {
      let position = coords
      if (!position) {
        const { status } = await Location.getForegroundPermissionsAsync()
        if (status === 'granted') {
          const fix = await Location.getCurrentPositionAsync({})
          position = { lat: fix.coords.latitude, lng: fix.coords.longitude }
          setCoords(position)
        }
      }
      setIssues(
        position ? await api.nearbyIssues(position.lat, position.lng, 8000) : await api.allIssues(),
      )
    } catch (e) {
      setError(e.message)
    } finally {
      setRefreshing(false)
    }
  }, [coords])

  useEffect(() => {
    load()
  }, [load])

  const centre = coords || (issues[0] ? { lat: issues[0].lat, lng: issues[0].lng } : { lat: 12.9719, lng: 77.5937 })

  return (
    <View style={s.screen}>
      <View style={[s.body, { paddingBottom: 0 }]}>
        <View style={s.between}>
          <Text style={s.h1}>Nearby</Text>
          <View style={s.row}>
            <Btn
              title="List"
              kind={view === 'list' ? 'primary' : 'default'}
              onPress={() => setView('list')}
            />
            <Btn
              title="Map"
              kind={view === 'map' ? 'primary' : 'default'}
              onPress={() => setView('map')}
            />
          </View>
        </View>
        <Text style={[s.muted, { marginTop: 4, marginBottom: 12 }]}>
          {issues.length} open issue{issues.length === 1 ? '' : 's'} around you. Seen one
          yourself? Open it and corroborate — duplicates are evidence, not noise.
        </Text>
        {error ? <Text style={s.err}>{error}</Text> : null}
      </View>

      {view === 'map' ? (
        <MapView
          style={{ flex: 1 }}
          initialRegion={{
            latitude: centre.lat,
            longitude: centre.lng,
            latitudeDelta: 0.06,
            longitudeDelta: 0.06,
          }}
        >
          {issues.map((issue) => (
            <Marker
              key={issue.id}
              coordinate={{ latitude: issue.lat, longitude: issue.lng }}
              title={issue.category}
              description={`${issue.status} · priority ${issue.priority.toFixed(2)}`}
              pinColor={STATUS_COLOR[issue.status]}
              onCalloutPress={() => onOpenIssue(issue.id)}
            />
          ))}
        </MapView>
      ) : (
        <FlatList
          data={issues}
          keyExtractor={(item) => item.id}
          contentContainerStyle={s.body}
          refreshControl={
            <RefreshControl refreshing={refreshing} onRefresh={load} tintColor={theme.accent} />
          }
          ListEmptyComponent={
            <Text style={s.muted}>
              {refreshing ? 'Loading…' : 'No open issues nearby. Be the first to report one.'}
            </Text>
          }
          renderItem={({ item }) => (
            <Pressable onPress={() => onOpenIssue(item.id)}>
              {({ pressed }) => (
                <View style={[s.card, { opacity: pressed ? 0.7 : 1, flexDirection: 'row', gap: 12 }]}>
                  {item.photo_url ? (
                    <Image
                      source={{ uri: mediaUrl(item.photo_url) }}
                      style={{ width: 72, height: 72, borderRadius: 8 }}
                    />
                  ) : null}
                  <View style={{ flex: 1 }}>
                    <View style={s.between}>
                      <Text style={s.h2}>{item.category.replace('_', ' ')}</Text>
                      <Pill label={item.status} />
                    </View>
                    <Text style={[s.muted, { marginTop: 4 }]}>
                      {item.ward.replace('WARD-', '')} · priority {item.priority.toFixed(2)} ·{' '}
                      {ago(item.created_at)}
                    </Text>
                    <Text style={[s.muted, { marginTop: 2 }]}>
                      {item.corroboration_count} corroboration
                      {item.corroboration_count === 1 ? '' : 's'} ·{' '}
                      {Number(item.reward_total).toFixed(0)} pt pool
                    </Text>
                  </View>
                </View>
              )}
            </Pressable>
          )}
        />
      )}
    </View>
  )
}
