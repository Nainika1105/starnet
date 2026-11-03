// An empty VITE_API_BASE means "same origin as this page", which is how the
// API serves the built dashboard: one host, one link, no CORS to configure.
// Unset (undefined) keeps the local dev default, where Vite serves the
// dashboard on 5173 and the API runs separately on 8000.
const RAW = import.meta.env.VITE_API_BASE
const BASE = RAW === undefined ? 'http://localhost:8000' : RAW

// Both the municipal and citizen views share this app and this token.
const TOKEN_KEY = 'starnet.token'

export function getToken() {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function setToken(token) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {
    /* private window: the session just won't persist */
  }
}

async function request(path, { method = 'GET', body, form, auth = true } = {}) {
  const headers = {}
  // FormData sets its own Content-Type with the multipart boundary; setting it
  // by hand produces a request the server cannot parse.
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (auth) {
    const token = getToken()
    if (token) headers.Authorization = `Bearer ${token}`
  }

  const res = await fetch(`${BASE}${path}`, {
    method,
    headers,
    body: form !== undefined ? form : body === undefined ? undefined : JSON.stringify(body),
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
  base: BASE,
  login: (email, password) =>
    request('/auth/login', { method: 'POST', body: { email, password }, auth: false }),
  me: () => request('/auth/me'),
  mechanism: () => request('/config/mechanism', { auth: false }),
  queue: (params = {}) => {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== '' && v != null),
    ).toString()
    return request(`/admin/queue${qs ? `?${qs}` : ''}`)
  },
  adminIssue: (id) => request(`/admin/issues/${id}`),
  assign: (id, assignee_label) =>
    request(`/admin/issues/${id}/assign`, { method: 'POST', body: { assignee_label } }),
  resolve: (id) => request(`/admin/issues/${id}/resolve`, { method: 'POST' }),
  reject: (id, reason) =>
    request(`/admin/issues/${id}/reject`, { method: 'POST', body: { reason } }),
  reopen: (id, reason) =>
    request(`/admin/issues/${id}/reopen`, { method: 'POST', body: { reason } }),
  closeOffApp: (id, reason) =>
    request(`/admin/issues/${id}/close-offapp`, { method: 'POST', body: { reason } }),
  metrics: () => request('/admin/metrics'),
  ledger: () => request('/admin/ledger'),
  allIssues: () => request('/issues?limit=500', { auth: false }),

  // --- citizen side ------------------------------------------------------
  signup: (email, password, handle, home_ward) =>
    request('/auth/signup', {
      method: 'POST',
      body: { email, password, handle, home_ward },
      auth: false,
    }),

  issue: (id) => request(`/issues/${id}`),

  submitReport: ({ category, lat, lng, description, photo }) => {
    const form = new FormData()
    form.append('category', category)
    form.append('lat', String(lat))
    form.append('lng', String(lng))
    if (description) form.append('description', description)
    if (photo) form.append('photo', photo, 'report.jpg')
    return request('/reports', { method: 'POST', form })
  },

  corroborate: (id, lat, lng, description) =>
    request(`/issues/${id}/corroborate`, {
      method: 'POST',
      body: { lat, lng, description },
    }),

  myTasks: () => request('/tasks/me'),

  completeTask: (id, verdict, photo) => {
    const form = new FormData()
    form.append('verdict', verdict)
    if (photo) form.append('photo', photo, 'confirm.jpg')
    return request(`/tasks/${id}/complete`, { method: 'POST', form })
  },

  wallet: () => request('/wallet/me'),
  catalog: () => request('/wallet/catalog'),
  redeem: (sku, nonce) => request('/wallet/redeem', { method: 'POST', body: { sku, nonce } }),
}

export const mediaUrl = (path) => (path ? `${BASE}${path}` : null)
