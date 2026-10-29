import React, { useState } from 'react'
import { Pressable, Text, View } from 'react-native'
import { StatusBar } from 'expo-status-bar'
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context'
import { Appearance } from 'react-native'

import { api, setToken } from './src/lib/api'
import { theme } from './src/theme'
import Auth from './src/screens/Auth'
import Report from './src/screens/Report'
import Feed from './src/screens/Feed'
import IssueDetail from './src/screens/IssueDetail'
import Tasks from './src/screens/Tasks'
import Wallet from './src/screens/Wallet'

const TABS = [
  ['feed', 'Nearby'],
  ['report', 'Report'],
  ['tasks', 'Tasks'],
  ['wallet', 'Wallet'],
]

export default function App() {
  const [me, setMe] = useState(null)
  const [tab, setTab] = useState('feed')
  const [openIssueId, setOpenIssueId] = useState(null)
  const [reloadKey, setReloadKey] = useState(0)

  // Refetching `me` keeps the tier current: confirmed reports raise it, and the
  // tier is what unlocks verification tasks.
  const refreshMe = () => api.me().then(setMe).catch(() => {})

  if (!me) {
    return (
      <SafeAreaProvider>
        <StatusBar style={Appearance.getColorScheme() === 'light' ? 'dark' : 'light'} />
        <SafeAreaView style={{ flex: 1, backgroundColor: theme.bg }}>
          <Auth onSignedIn={setMe} />
        </SafeAreaView>
      </SafeAreaProvider>
    )
  }

  return (
    <SafeAreaProvider>
      <StatusBar style={Appearance.getColorScheme() === 'light' ? 'dark' : 'light'} />
      <SafeAreaView style={{ flex: 1, backgroundColor: theme.bg }}>
        <View style={{ flex: 1 }}>
          {openIssueId ? (
            <IssueDetail issueId={openIssueId} onBack={() => setOpenIssueId(null)} />
          ) : (
            <>
              {tab === 'feed' && <Feed key={reloadKey} onOpenIssue={setOpenIssueId} />}
              {tab === 'report' && (
                <Report
                  onReported={() => {
                    setReloadKey((k) => k + 1)
                    refreshMe()
                  }}
                />
              )}
              {tab === 'tasks' && (
                <Tasks
                  me={me}
                  onDone={() => {
                    setReloadKey((k) => k + 1)
                    refreshMe()
                  }}
                />
              )}
              {tab === 'wallet' && <Wallet key={reloadKey} me={me} />}
            </>
          )}
        </View>

        <View
          style={{
            flexDirection: 'row',
            borderTopWidth: 1,
            borderTopColor: theme.border,
            backgroundColor: theme.panel,
          }}
        >
          {TABS.map(([key, label]) => {
            const active = tab === key && !openIssueId
            return (
              <Pressable
                key={key}
                onPress={() => {
                  setOpenIssueId(null)
                  setTab(key)
                }}
                style={{ flex: 1, paddingVertical: 12, alignItems: 'center' }}
              >
                <Text
                  style={{
                    color: active ? theme.accent : theme.muted,
                    fontWeight: active ? '700' : '500',
                    fontSize: 13,
                  }}
                >
                  {label}
                </Text>
              </Pressable>
            )
          })}
          <Pressable
            onPress={() => {
              setToken(null)
              setMe(null)
            }}
            style={{ paddingVertical: 12, paddingHorizontal: 14, alignItems: 'center' }}
          >
            <Text style={{ color: theme.muted, fontSize: 13 }}>Exit</Text>
          </Pressable>
        </View>
      </SafeAreaView>
    </SafeAreaProvider>
  )
}
