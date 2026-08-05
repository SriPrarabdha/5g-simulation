import type { Snapshot } from './types'

const JSON_HEADERS = { 'Content-Type': 'application/json' }

async function request<T>(path: string, init: RequestInit = {}, token?: string): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { ...JSON_HEADERS, ...(token ? { Authorization: `Bearer ${token}` } : {}), ...init.headers },
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }))
    throw new Error(body.detail ?? 'Request failed')
  }
  return response.json() as Promise<T>
}

export async function login(username: string, password: string) {
  return request<{ access_token: string; role: string }>('/api/v1/auth/login', {
    method: 'POST', body: JSON.stringify({ username, password }),
  })
}

export async function createRun(token: string, controller = 'predictive'): Promise<Snapshot> {
  return request('/api/v1/runs', {
    method: 'POST', body: JSON.stringify({ scenario_id: 'demo-three-upf-two-zone', controller }),
  }, token)
}

export async function runAction(token: string, runId: string, action: string): Promise<Snapshot> {
  return request(`/api/v1/runs/${runId}/${action}`, { method: 'POST' }, token)
}

export async function getRun(token: string, runId: string): Promise<Snapshot> {
  return request(`/api/v1/runs/${runId}`, {}, token)
}

export async function setControls(token: string, runId: string, body: Record<string, unknown>): Promise<Snapshot> {
  return request(`/api/v1/runs/${runId}/controls`, { method: 'PATCH', body: JSON.stringify(body) }, token)
}
