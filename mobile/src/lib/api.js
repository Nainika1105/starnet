import Constants from 'expo-constants'

// Point this at your machine's LAN IP when running on a physical device --
// localhost inside the app means the phone itself.
const BASE =
  Constants.expoConfig?.extra?.apiBase ||
  Constants.manifest?.extra?.apiBase ||
  'http://localhost:8000'

let token = null

export const setToken = (value) => {
  token = value
}
export const getToken = () => token
export const apiBase = BASE

async function request(path, { method = 'GET', body, form } = {}) {
  const headers = {}
  if (token) headers.Authorization = `Bearer ${token}`
  if (body !== undefined) headers['Content-Type'] = 'application/json'

  const res = await fetch(`${BASE}${path}`, {
    method,
    headers,
    body: form ?? (body === undefined ? undefined : JSON.stringify(body)),
  })

  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`
    try {
      const payload = await res.json()
      if (payload?.detail) detail = payload.detail
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail)
  }
  return res.status === 204 ? null : res.json()
}

export const api = {
  signup: (email, password, handle, home_ward) =>
    request('/auth/signup', { method: 'POST', body: { email, password, handle, home_ward } }),
  login: (email, password) => request('/auth/login', { method: 'POST', body: { email, password } }),
  me: () => request('/auth/me'),
  mechanism: () => request('/config/mechanism'),

  nearbyIssues: (lat, lng, radius = 4000) =>
    request(`/issues?near=${lat},${lng}&radius_m=${radius}`),
  allIssues: () => request('/issues?limit=200'),
  issue: (id) => request(`/issues/${id}`),
  corroborate: (id, lat, lng, description) =>
    request(`/issues/${id}/corroborate`, { method: 'POST', body: { lat, lng, description } }),

  submitReport: ({ category, lat, lng, description, photoUri }) => {
    const form = new FormData()
    form.append('category', category)
    form.append('lat', String(lat))
    form.append('lng', String(lng))
    if (description) form.append('description', description)
    if (photoUri) {
      form.append('photo', { uri: photoUri, name: 'report.jpg', type: 'image/jpeg' })
    }
    return request('/reports', { method: 'POST', form })
  },

  myTasks: () => request('/tasks/me'),
  completeTask: (id, verdict, photoUri) => {
    const form = new FormData()
    form.append('verdict', verdict)
    if (photoUri) {
      form.append('photo', { uri: photoUri, name: 'confirm.jpg', type: 'image/jpeg' })
    }
    return request(`/tasks/${id}/complete`, { method: 'POST', form })
  },

  wallet: () => request('/wallet/me'),
  catalog: () => request('/wallet/catalog'),
  redeem: (sku, nonce) => request('/wallet/redeem', { method: 'POST', body: { sku, nonce } }),
}

export const mediaUrl = (path) => (path ? `${BASE}${path}` : null)
