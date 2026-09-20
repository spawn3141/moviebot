// Thin wrapper around the REST API (see /docs on the server).

function queryString(params) {
  const sp = new URLSearchParams()
  for (const [key, value] of Object.entries(params || {})) {
    if (value === undefined || value === null || value === '' || value === false) continue
    if (Array.isArray(value)) value.forEach((v) => sp.append(key, v))
    else sp.append(key, String(value))
  }
  const s = sp.toString()
  return s ? `?${s}` : ''
}

async function request(method, path, { params, body } = {}) {
  const res = await fetch(path + queryString(params), {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  })
  if (res.status === 204) return null
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`
    try {
      const data = await res.json()
      message = typeof data.detail === 'string' ? data.detail : message
    } catch {
      // keep the status text
    }
    throw new Error(message)
  }
  return res.json()
}

export const api = {
  titles: (params) => request('GET', '/api/titles', { params }),
  title: (id) => request('GET', `/api/titles/${id}`),
  setState: (id, body) => request('PUT', `/api/titles/${id}/state`, { body }),
  genres: (params) => request('GET', '/api/genres', { params }),
  services: () => request('GET', '/api/services'),
  setSubscribed: (key, subscribed) => request('PUT', `/api/services/${key}`, { body: { subscribed } }),
  settings: () => request('GET', '/api/settings'),
  updateSettings: (body) => request('PUT', '/api/settings', { body }),
  status: () => request('GET', '/api/status'),
  startSnapshot: () => request('POST', '/api/snapshot'),
  search: (q) => request('GET', '/api/search', { params: { q } }),
  importTitle: (media_type, tmdb_id) =>
    request('POST', '/api/titles/import', { body: { media_type, tmdb_id } }),
  dropImport: (id) => request('DELETE', `/api/titles/${id}/import`),
  lists: () => request('GET', '/api/lists'),
  createList: (name) => request('POST', '/api/lists', { body: { name } }),
  savedFilters: () => request('GET', '/api/filters'),
  createSavedFilter: (name, filters) => request('POST', '/api/filters', { body: { name, filters } }),
  updateSavedFilter: (id, body) => request('PATCH', `/api/filters/${id}`, { body }),
  deleteSavedFilter: (id) => request('DELETE', `/api/filters/${id}`),
  updateList: (id, body) => request('PATCH', `/api/lists/${id}`, { body }),
  deleteList: (id) => request('DELETE', `/api/lists/${id}`),
  setTitleLists: (id, listIds) => request('PUT', `/api/titles/${id}/lists`, { body: { list_ids: listIds } }),
}
