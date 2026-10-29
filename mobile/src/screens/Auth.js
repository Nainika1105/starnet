import React, { useState } from 'react'
import { KeyboardAvoidingView, Platform, ScrollView, Text, TextInput, View } from 'react-native'
import { api, setToken } from '../lib/api'
import { theme } from '../theme'
import { Btn, s } from '../components/ui'

const WARDS = ['WARD-CENTRAL', 'WARD-NORTH', 'WARD-SOUTH', 'WARD-EAST', 'WARD-WEST']

export default function Auth({ onSignedIn }) {
  const [mode, setMode] = useState('login')
  const [email, setEmail] = useState('asha@starnet.demo')
  const [password, setPassword] = useState('starnet123')
  const [handle, setHandle] = useState('')
  const [ward, setWard] = useState('WARD-CENTRAL')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  async function submit() {
    setBusy(true)
    setError(null)
    try {
      const { access_token } =
        mode === 'login'
          ? await api.login(email, password)
          : await api.signup(email, password, handle, ward)
      setToken(access_token)
      onSignedIn(await api.me())
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <KeyboardAvoidingView style={s.screen} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
      <ScrollView contentContainerStyle={[s.body, { paddingTop: 72 }]}>
        <Text style={s.h1}>STAR-Net</Text>
        <Text style={[s.muted, { marginTop: 4, marginBottom: 24 }]}>
          Report a civic fault. Your reward is released when the repair is confirmed — not when
          you file.
        </Text>

        <View style={s.card}>
          <View style={[s.row, { marginBottom: 14 }]}>
            <Btn
              title="Sign in"
              kind={mode === 'login' ? 'primary' : 'default'}
              onPress={() => setMode('login')}
              style={{ flex: 1 }}
            />
            <Btn
              title="Sign up"
              kind={mode === 'signup' ? 'primary' : 'default'}
              onPress={() => setMode('signup')}
              style={{ flex: 1 }}
            />
          </View>

          <TextInput
            style={s.input}
            value={email}
            onChangeText={setEmail}
            placeholder="Email"
            placeholderTextColor={theme.muted}
            autoCapitalize="none"
            keyboardType="email-address"
          />
          <TextInput
            style={s.input}
            value={password}
            onChangeText={setPassword}
            placeholder="Password (min 8 characters)"
            placeholderTextColor={theme.muted}
            secureTextEntry
          />

          {mode === 'signup' && (
            <>
              <TextInput
                style={s.input}
                value={handle}
                onChangeText={setHandle}
                placeholder="Public handle"
                placeholderTextColor={theme.muted}
                autoCapitalize="none"
              />
              <Text style={s.label}>Home ward</Text>
              <View style={[s.row, { marginBottom: 12 }]}>
                {WARDS.map((w) => (
                  <Btn
                    key={w}
                    title={w.replace('WARD-', '')}
                    kind={ward === w ? 'primary' : 'default'}
                    onPress={() => setWard(w)}
                  />
                ))}
              </View>
            </>
          )}

          <Btn
            title={mode === 'login' ? 'Sign in' : 'Create account'}
            kind="primary"
            onPress={submit}
            loading={busy}
            disabled={!email || password.length < 8 || (mode === 'signup' && handle.length < 3)}
          />
          {error ? <Text style={s.err}>{error}</Text> : null}
        </View>

        <View style={s.note}>
          <Text style={s.muted}>
            Seeded demo accounts, all with password{' '}
            <Text style={{ color: theme.text }}>starnet123</Text>:{'\n'}
            asha@starnet.demo — a reporter{'\n'}
            meera@starnet.demo — trusted, receives fix-confirmation tasks{'\n'}
            newcomer@starnet.demo — new account, no task privileges
          </Text>
        </View>
      </ScrollView>
    </KeyboardAvoidingView>
  )
}
