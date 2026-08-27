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

export async function createRun(token: string, controller = 'mpc'): Promise<Snapshot> {
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

export async function rewindStory(token: string, runId: string, checkpointId: string, autoplay = true): Promise<Snapshot> {
  return request(`/api/v1/runs/${runId}/story/rewind`, {
    method: 'POST', body: JSON.stringify({ checkpoint_id: checkpointId, autoplay }),
  }, token)
}

export async function getCdotLiveSnapshot(token: string) {
  return request<any>('/api/v1/cdot-live/snapshot', {}, token)
}

export async function preloadCdotLive(token: string, hours = 3) {
  return request<any>(`/api/v1/cdot-live/preload?hours=${hours}`, { method: 'POST' }, token)
}

export async function setCdotLiveAct(token: string, act: string) {
  return request<any>(`/api/v1/cdot-live/act?act=${encodeURIComponent(act)}`, { method: 'POST' }, token)
}

export async function evaluateCdotLive(token: string) {
  return request<any>('/api/v1/cdot-live/evaluate', { method: 'POST' }, token)
}

export async function applyCdotLive(token: string, proposalId: string, expectedHash: string, confirmation: boolean) {
  return request<any>('/api/v1/cdot-live/apply', {
    method: 'POST', body: JSON.stringify({ proposal_id: proposalId, expected_smf_state_hash: expectedHash, confirmation }),
  }, token)
}

export async function rollbackCdotLive(token: string, applicationId: string, expectedHash: string, confirmation: boolean) {
  return request<any>('/api/v1/cdot-live/rollback', {
    method: 'POST', body: JSON.stringify({ application_id: applicationId, expected_smf_state_hash: expectedHash, confirmation }),
  }, token)
}
