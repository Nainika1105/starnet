const BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000'

const TOKEN_KEY = 'starnet.admin.token'

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

async function request(path, { method = 'GET', body, auth = true } = {}) {
  const headers = {}
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (auth) {
    const token = getToken()
    if (token) headers.Authorization = `Bearer ${token}`
  }

  const res = await fetch(`${BASE}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
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
  issue: (id) => request(`/admin/issues/${id}`),
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
}

export const mediaUrl = (path) => (path ? `${BASE}${path}` : null)
