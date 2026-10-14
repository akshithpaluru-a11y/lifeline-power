import type { BackupSource, Caregiver, Device, Household, HelpSite, IdentifyResult, MedicalProgram, Outage, Plan, StreamEvent } from './types'
import { ApiError, mock } from './mockServer'

export { ApiError }

// One client for docs/API.md. Uses the real server when /api/health answers, otherwise the
// in-browser mock, so the app always works (offline, in demos, before the backend ships).

let mode: Promise<'live' | 'mock'> | null = null
export function apiMode() {
  mode ??= fetch('/api/health', { signal: AbortSignal.timeout(1500) })
    .then(async (r) => (r.ok && (await r.json()).ok ? 'live' as const : 'mock' as const))
    .catch(() => 'mock' as const)
  return mode
}

async function http<T>(method: string, path: string, token: string | null, body?: unknown): Promise<T> {
  const r = await fetch(`/api${path}`, {
    method,
    headers: { ...(body !== undefined ? { 'content-type': 'application/json' } : {}), ...(token ? { authorization: `Bearer ${token}` } : {}) },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (r.status === 204) return undefined as T
  const j = await r.json().catch(() => ({}))
  if (!r.ok) throw new ApiError(r.status, j?.error?.code ?? 'error', j?.error?.message ?? r.statusText)
  return j as T
}

const live = {
  catalogDevices: () => http<{ devices: import('./types').DeviceTemplate[] }>('GET', '/catalog/devices', null),
  catalogBackups: () => http<{ backups: import('./types').BackupTemplate[] }>('GET', '/catalog/backups', null),
  createHousehold: (b: Partial<Household>) => http<{ household: Household; owner_token: string }>('POST', '/households', null, b),
  getHousehold: (id: string, t: string | null) => http<{ household: Household }>('GET', `/households/${id}`, t),
  patchHousehold: (id: string, t: string | null, b: Partial<Household>) => http<{ household: Household }>('PATCH', `/households/${id}`, t, b),
  addDevice: (id: string, t: string | null, b: Partial<Device> & { kind: string }) => http<{ device: Device }>('POST', `/households/${id}/devices`, t, b),
  patchDevice: (id: string, t: string | null, d: string, b: Partial<Device>) => http<{ device: Device }>('PATCH', `/households/${id}/devices/${d}`, t, b),
  deleteDevice: (id: string, t: string | null, d: string) => http<void>('DELETE', `/households/${id}/devices/${d}`, t),
  addBackup: (id: string, t: string | null, b: Partial<BackupSource> & { type: BackupSource['type'] }) => http<{ backup: BackupSource }>('POST', `/households/${id}/backups`, t, b),
  patchBackup: (id: string, t: string | null, k: string, b: Partial<BackupSource>) => http<{ backup: BackupSource }>('PATCH', `/households/${id}/backups/${k}`, t, b),
  deleteBackup: (id: string, t: string | null, k: string) => http<void>('DELETE', `/households/${id}/backups/${k}`, t),
  plan: (id: string, t: string | null, saver = false) => http<{ plan: Plan }>('GET', `/households/${id}/plan?saver=${saver}`, t),
  startOutage: (id: string, t: string | null, source: Outage['source'], speed?: number) => http<{ outage: Outage }>('POST', `/households/${id}/outages`, t, { source, ...(speed ? { speed } : {}) }),
  currentOutage: (id: string, t: string | null) => http<{ outage: Outage | null }>('GET', `/households/${id}/outages/current`, t),
  checkIn: (id: string, t: string | null) => http<{ outage: Outage }>('POST', `/households/${id}/outages/current/check-in`, t, { ok: true }),
  ack: (id: string, t: string | null, b: { message?: string; eta_minutes?: number }) => http<{ outage: Outage }>('POST', `/households/${id}/outages/current/ack`, t, b),
  endOutage: (id: string, t: string | null) => http<{ outage: Outage }>('POST', `/households/${id}/outages/current/end`, t),
  join: (join_code: string, name: string, relationship?: string) => http<{ caregiver: Caregiver; caregiver_token: string; household: Household }>('POST', '/caregivers/join', null, { join_code, name, relationship }),
  removeCaregiver: (id: string, t: string | null, c: string) => http<void>('DELETE', `/households/${id}/caregivers/${c}`, t),
  helpSites: (id: string, t: string | null, lat?: number, lon?: number) =>
    http<{ sites: HelpSite[]; source: string; note?: string }>('GET', `/households/${id}/help-sites${lat != null ? `?lat=${lat}&lon=${lon}` : ''}`, t),
  medicalPrograms: (state?: string | null, utility?: string | null) =>
    http<{ programs: MedicalProgram[]; generic_advice: string[] }>('GET', `/utilities/medical-programs?state=${encodeURIComponent(state ?? '')}&utility=${encodeURIComponent(utility ?? '')}`, null),
  medicalLetter: (id: string, t: string | null) => http<{ subject: string; body: string }>('GET', `/households/${id}/medical-letter`, t),
  identifyDevice: (text: string) => http<IdentifyResult>('POST', '/ai/identify-device', null, { text }),
  async identifyPhoto(file: File): Promise<IdentifyResult> {
    const f = new FormData(); f.append('image', file)
    const r = await fetch('/api/ai/identify-device', { method: 'POST', body: f })
    if (!r.ok) throw new ApiError(r.status, 'error', 'Could not read the label')
    return r.json()
  },
  stream(id: string, token: string | null, cb: (e: StreamEvent) => void) {
    const es = new EventSource(`/api/households/${id}/stream?token=${encodeURIComponent(token ?? '')}`)
    const names: StreamEvent['event'][] = ['snapshot', 'outage_started', 'milestone', 'check_in', 'missed_check_in', 'ack', 'outage_ended', 'household_updated']
    names.forEach((n) => es.addEventListener(n, (m) => cb({ event: n, data: JSON.parse((m as MessageEvent).data) } as StreamEvent)))
    return () => es.close()
  },
}

type Live = typeof live
type Mocked = Omit<Live, 'identifyPhoto'> & { identifyPhoto?: Live['identifyPhoto'] }

// Every call goes through the right backend after the first health check resolves.
export const api = new Proxy({} as Live, {
  get(_, key: keyof Live) {
    if (key === 'stream') {
      return (id: string, t: string | null, cb: (e: StreamEvent) => void) => {
        let stop: (() => void) | null = null, dead = false
        apiMode().then((m) => { if (!dead) stop = (m === 'live' ? live : (mock as unknown as Mocked)).stream(id, t, cb) })
        return () => { dead = true; stop?.() }
      }
    }
    return async (...args: unknown[]) => {
      const m = await apiMode()
      const impl = (m === 'live' ? live : (mock as unknown as Mocked))[key] as ((...a: unknown[]) => unknown) | undefined
      if (!impl) throw new ApiError(501, 'unavailable', 'Needs the server')
      return impl(...args)
    }
  },
})
