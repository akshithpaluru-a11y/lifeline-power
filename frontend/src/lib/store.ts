import { upsertMilestone } from './time'
import { useSyncExternalStore } from 'react'
import type { BackupSource, Device, Household, Outage, Plan, StreamEvent } from './types'
import { api } from './api'

interface Session { household_id: string; token: string }

interface State {
  ready: boolean
  session: Session | null
  household: Household | null
  plan: Plan | null
  outage: Outage | null
  /** Last missed-check-in warning from the server, if any. */
  missed: number | null
}

const SESSION_KEY = 'lifeline.owner'
const read = <T,>(k: string): T | null => { try { const s = localStorage.getItem(k); return s ? JSON.parse(s) : null } catch { return null } }
const write = (k: string, v: unknown) => { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, JSON.stringify(v)) } catch { /* ignore */ } }

let state: State = { ready: false, session: read<Session>(SESSION_KEY), household: null, plan: null, outage: null, missed: null }
const subs = new Set<() => void>()

function set(patch: Partial<State>) {
  state = { ...state, ...patch }
  subs.forEach((f) => f())
}

export const getState = () => state

export function useStore<T>(pick: (s: State) => T): T {
  return useSyncExternalStore((cb) => { subs.add(cb); return () => subs.delete(cb) }, () => pick(state))
}

// ---- syncing ---------------------------------------------------------------

let unstream: (() => void) | null = null
let planTimer: number | null = null

function schedulePlan() {
  if (planTimer) clearTimeout(planTimer)
  planTimer = window.setTimeout(refreshPlan, 120)
}

export async function refreshPlan() {
  const s = state.session
  if (!s) return
  const { plan } = await api.plan(s.household_id, s.token)
  set({ plan })
}

function onEvent(e: StreamEvent) {
  switch (e.event) {
    case 'snapshot': set({ household: e.data.household, outage: e.data.outage }); schedulePlan(); break
    case 'household_updated': set({ household: e.data.household }); schedulePlan(); break
    case 'outage_started': case 'check_in': set({ outage: e.data.outage, missed: null }); break
    case 'outage_ended': set({ outage: null, missed: null }); break
    case 'ack': if (state.outage?.id === e.data.outage_id) set({ outage: { ...state.outage, acks: [...state.outage.acks.filter((a) => a.caregiver_id !== e.data.ack.caregiver_id), e.data.ack] } }); break
    case 'milestone': if (state.outage?.id === e.data.outage_id) set({ outage: { ...state.outage, milestones: upsertMilestone(state.outage.milestones, e.data.milestone) } }); break
    case 'missed_check_in': set({ missed: e.data.minutes_overdue }); break
  }
}

export async function boot() {
  const s = state.session
  if (!s) { set({ ready: true }); return }
  try {
    const [{ household }, { outage }] = await Promise.all([api.getHousehold(s.household_id, s.token), api.currentOutage(s.household_id, s.token)])
    set({ household, outage, ready: true })
    unstream?.()
    unstream = api.stream(s.household_id, s.token, onEvent)
    await refreshPlan()
  } catch {
    // Token no longer valid (e.g. server reset): start over.
    write(SESSION_KEY, null)
    set({ session: null, household: null, plan: null, outage: null, ready: true })
  }
}

function adopt(household: Household, token: string) {
  const session = { household_id: household.id, token }
  write(SESSION_KEY, session)
  set({ session, household })
}

const sess = () => { if (!state.session) throw new Error('No household yet'); return state.session }

// ---- actions ---------------------------------------------------------------

export interface NewHome {
  household: Partial<Household>
  devices: (Partial<Device> & { kind: string })[]
  backups: (Partial<BackupSource> & { type: BackupSource['type'] })[]
}

export async function createHome(n: NewHome) {
  const { household, owner_token } = await api.createHousehold(n.household)
  for (const d of n.devices) await api.addDevice(household.id, owner_token, d)
  for (const b of n.backups) await api.addBackup(household.id, owner_token, b)
  adopt(household, owner_token)
  await boot()
  return state.household!
}

/** A realistic sample home, so judges can try everything in one click. */
export async function createDemoHome() {
  const h = await createHome({
    household: { name: 'The Martínez home', patient_first_name: 'Rosa', address: '1420 Maple Ave, Apt 3B, Washington, DC', lat: 38.8899, lon: -77.0091, state: 'DC', utility_name: 'Pepco' },
    devices: [
      { kind: 'oxygen_concentrator_portable', label: 'Inogen One G5', draw_watts: 40, internal_battery_wh: 99, spare_batteries_wh: 99, charge_percent: 92 },
      { kind: 'cpap_bipap', label: 'ResMed AirSense 11', internal_battery_wh: 0 },
      { kind: 'enteral_feeding_pump', label: 'Kangaroo Joey pump', charge_percent: 64 },
    ],
    backups: [{ type: 'power_station', label: 'Jackery 500', capacity_wh: 518, max_output_watts: 500, charge_percent: 100 }],
  })
  // Two family members join with the code, the same way they would from their own phones.
  for (const [name, rel] of [['Maya', 'Daughter'], ['Leo', 'Grandson']]) await api.join(h.join_code, name, rel)
  await boot()
}

export async function updateHousehold(patch: Partial<Household>) {
  const s = sess()
  if (state.household) set({ household: { ...state.household, ...patch } })
  const { household } = await api.patchHousehold(s.household_id, s.token, patch)
  set({ household }); schedulePlan()
}

export async function saveDevice(d: Partial<Device> & { kind: string; id?: string }) {
  const s = sess()
  if (d.id && state.household?.devices.some((x) => x.id === d.id)) {
    const { id, kind: _k, ...rest } = d
    await api.patchDevice(s.household_id, s.token, id, rest)
  } else {
    const { id: _i, ...rest } = d
    await api.addDevice(s.household_id, s.token, rest)
  }
  await reloadHousehold()
}
export async function deleteDevice(id: string) { const s = sess(); await api.deleteDevice(s.household_id, s.token, id); await reloadHousehold() }

export async function saveBackup(b: Partial<BackupSource> & { type: BackupSource['type']; id?: string }) {
  const s = sess()
  if (b.id && state.household?.backups.some((x) => x.id === b.id)) {
    const { id, ...rest } = b
    await api.patchBackup(s.household_id, s.token, id, rest)
  } else {
    const { id: _i, ...rest } = b
    await api.addBackup(s.household_id, s.token, rest)
  }
  await reloadHousehold()
}
export async function deleteBackup(id: string) { const s = sess(); await api.deleteBackup(s.household_id, s.token, id); await reloadHousehold() }
export async function removeCaregiver(id: string) { const s = sess(); await api.removeCaregiver(s.household_id, s.token, id); await reloadHousehold() }

async function reloadHousehold() {
  const s = sess()
  const { household } = await api.getHousehold(s.household_id, s.token)
  set({ household }); schedulePlan()
}

export async function startOutage(source: Outage['source'], speed?: number) {
  const s = sess()
  if (state.outage?.status === 'active') return state.outage
  try {
    const { outage } = await api.startOutage(s.household_id, s.token, source, speed)
    set({ outage })
    return outage
  } catch {
    const { outage } = await api.currentOutage(s.household_id, s.token)
    set({ outage })
    return outage
  }
}
export async function checkIn() { const s = sess(); const { outage } = await api.checkIn(s.household_id, s.token); set({ outage, missed: null }) }
export async function endOutage() { const s = sess(); await api.endOutage(s.household_id, s.token); set({ outage: null, missed: null }) }

export function signOut() { unstream?.(); write(SESSION_KEY, null); set({ session: null, household: null, plan: null, outage: null }) }

if (typeof window !== 'undefined') {
  // Another tab created or switched the home.
  window.addEventListener('storage', (e) => { if (e.key === SESSION_KEY) { state.session = read<Session>(SESSION_KEY); boot() } })
}
