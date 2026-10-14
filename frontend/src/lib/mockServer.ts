import type { Ack, BackupSource, Caregiver, Device, Household, HelpSite, IdentifyResult, MedicalProgram, Outage, Plan, StreamEvent } from './types'
import { BACKUP_CATALOG, DEVICE_CATALOG, templateFor } from './catalog'
import { computeMilestones, computePlan } from './planner'

// In-browser stand-in for the FastAPI backend. Same shapes, same rules, stored in localStorage,
// with events shared across tabs so an owner tab and a caregiver tab update each other live.

interface Db {
  households: Record<string, Household>
  outages: Record<string, Outage[]>
  tokens: Record<string, { household_id: string; role: 'owner' | 'caregiver'; caregiver_id?: string }>
}

const KEY = 'lifeline.mockdb'
const bc = typeof BroadcastChannel !== 'undefined' ? new BroadcastChannel('lifeline-events') : null
const local = new Set<(hh: string, e: StreamEvent) => void>()

const rid = (p: string) => `${p}_${Math.random().toString(36).slice(2, 7)}`
const now = () => new Date().toISOString()
const code = () => Array.from({ length: 6 }, () => 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'[Math.floor(Math.random() * 31)]).join('')

function load(): Db {
  try { const s = localStorage.getItem(KEY); if (s) return JSON.parse(s) } catch { /* ignore */ }
  return { households: {}, outages: {}, tokens: {} }
}
function save(db: Db) { try { localStorage.setItem(KEY, JSON.stringify(db)) } catch { /* ignore */ } }

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) { super(message) }
}

function auth(db: Db, token: string | null, hh: string, ownerOnly = false) {
  const t = token ? db.tokens[token] : null
  if (!t) throw new ApiError(401, 'unauthorized', 'Missing or unknown token')
  if (t.household_id !== hh) throw new ApiError(403, 'forbidden', 'Not your household')
  if (ownerOnly && t.role !== 'owner') throw new ApiError(403, 'forbidden', 'Owner only')
  if (!db.households[hh]) throw new ApiError(404, 'not_found', 'Household not found')
  return t
}

function emit(hh: string, e: StreamEvent) {
  local.forEach((f) => f(hh, e))
  bc?.postMessage({ hh, e })
}
bc?.addEventListener('message', (m) => local.forEach((f) => f(m.data.hh, m.data.e)))

function current(db: Db, hh: string) { return (db.outages[hh] ?? []).find((o) => o.status === 'active') ?? null }

function withMilestones(h: Household, o: Outage): Outage {
  const site = mockSites(h.lat ?? 38.8899, h.lon ?? -77.0091).find((s) => s.likely_has_power)
  return { ...o, milestones: computeMilestones(o.plan, new Date(o.started_at).getTime(), o.speed, site?.travel_minutes, site?.name) }
}

function touch(db: Db, h: Household) {
  db.households[h.id] = h
  save(db)
  emit(h.id, { event: 'household_updated', data: { household: h } })
  return h
}

export const mock = {
  async health() { return { ok: true, version: 'mock' } },
  async catalogDevices() { return { devices: DEVICE_CATALOG } },
  async catalogBackups() { return { backups: BACKUP_CATALOG } },

  async createHousehold(body: Partial<Household>) {
    const db = load()
    const h: Household = {
      id: rid('hh'), name: body.name ?? 'Home', patient_first_name: body.patient_first_name ?? '',
      address: body.address ?? null, lat: body.lat ?? null, lon: body.lon ?? null, state: body.state ?? null,
      utility_name: body.utility_name ?? null, medical_program_registered: false, join_code: code(), armed: true,
      devices: [], backups: [], caregivers: [], created_at: now(),
    }
    const owner_token = rid('own') + rid('t')
    db.tokens[owner_token] = { household_id: h.id, role: 'owner' }
    db.households[h.id] = h
    save(db)
    return { household: h, owner_token }
  },
  async getHousehold(id: string, token: string | null) { const db = load(); auth(db, token, id); return { household: db.households[id] } },
  async patchHousehold(id: string, token: string | null, patch: Partial<Household>) {
    const db = load(); auth(db, token, id, true)
    return { household: touch(db, { ...db.households[id], ...patch }) }
  },

  async addDevice(id: string, token: string | null, body: Partial<Device> & { kind: string }) {
    const db = load(); auth(db, token, id, true)
    const t = templateFor(body.kind)
    const d: Device = {
      id: rid('dv'), label: t.name, icon: t.icon, life_sustaining: t.life_sustaining_default,
      draw_watts: t.typical_draw_watts, saver_draw_watts: t.saver_draw_watts, saver_enabled: false, duty_cycle: t.duty_cycle,
      ac_powered: t.ac_powered, internal_battery_wh: t.internal_battery_wh, spare_batteries_wh: 0, charge_percent: 100, notes: '',
      ...body,
    }
    const h = db.households[id]
    touch(db, { ...h, devices: [...h.devices, d] })
    return { device: d }
  },
  async patchDevice(id: string, token: string | null, deviceId: string, patch: Partial<Device>) {
    const db = load(); auth(db, token, id, true)
    const h = db.households[id]
    const d = { ...h.devices.find((x) => x.id === deviceId)!, ...patch, id: deviceId }
    touch(db, { ...h, devices: h.devices.map((x) => (x.id === deviceId ? d : x)) })
    return { device: d }
  },
  async deleteDevice(id: string, token: string | null, deviceId: string) {
    const db = load(); auth(db, token, id, true)
    const h = db.households[id]
    touch(db, { ...h, devices: h.devices.filter((x) => x.id !== deviceId) })
  },
  async addBackup(id: string, token: string | null, body: Partial<BackupSource> & { type: BackupSource['type'] }) {
    const db = load(); auth(db, token, id, true)
    const t = BACKUP_CATALOG.find((b) => b.type === body.type)!
    const b: BackupSource = {
      id: rid('bk'), label: t.name, capacity_wh: t.typical_capacity_wh, max_output_watts: t.typical_max_output_watts,
      has_ac_outlet: true, charge_percent: t.typical_capacity_wh ? 100 : null,
      fuel_hours: t.typical_capacity_wh ? null : t.type === 'generator' ? 8 : 12, ...body,
    }
    const h = db.households[id]
    touch(db, { ...h, backups: [...h.backups, b] })
    return { backup: b }
  },
  async patchBackup(id: string, token: string | null, backupId: string, patch: Partial<BackupSource>) {
    const db = load(); auth(db, token, id, true)
    const h = db.households[id]
    const b = { ...h.backups.find((x) => x.id === backupId)!, ...patch, id: backupId }
    touch(db, { ...h, backups: h.backups.map((x) => (x.id === backupId ? b : x)) })
    return { backup: b }
  },
  async deleteBackup(id: string, token: string | null, backupId: string) {
    const db = load(); auth(db, token, id, true)
    const h = db.households[id]
    touch(db, { ...h, backups: h.backups.filter((x) => x.id !== backupId) })
  },

  async plan(id: string, token: string | null, saver = false): Promise<{ plan: Plan }> {
    const db = load(); auth(db, token, id)
    return { plan: computePlan(db.households[id], saver) }
  },

  async startOutage(id: string, token: string | null, source: Outage['source'], speed = 1) {
    const db = load(); auth(db, token, id, true)
    if (current(db, id)) throw new ApiError(409, 'conflict', 'An outage is already active')
    const h = db.households[id]
    const o = withMilestones(h, {
      id: rid('og'), status: 'active', source, started_at: now(), ended_at: null, plan: computePlan(h),
      milestones: [], acks: [], last_check_in_at: now(), drill: source === 'drill', speed: source === 'drill' ? Math.min(600, Math.max(1, speed)) : 1,
    })
    db.outages[id] = [o, ...(db.outages[id] ?? [])]
    save(db)
    emit(id, { event: 'outage_started', data: { outage: o, drill: source === 'drill' } })
    return { outage: o }
  },
  async currentOutage(id: string, token: string | null) {
    const db = load(); auth(db, token, id)
    const o = current(db, id)
    return { outage: o ? withMilestones(db.households[id], o) : null }
  },
  async checkIn(id: string, token: string | null) {
    const db = load(); auth(db, token, id, true)
    const o = current(db, id)
    if (!o) throw new ApiError(404, 'not_found', 'No active outage')
    const next = { ...o, last_check_in_at: now() }
    db.outages[id] = db.outages[id].map((x) => (x.id === o.id ? next : x)); save(db)
    emit(id, { event: 'check_in', data: { outage: next } })
    return { outage: next }
  },
  async ack(id: string, token: string | null, body: { message?: string; eta_minutes?: number }) {
    const db = load(); const t = auth(db, token, id)
    if (t.role !== 'caregiver') throw new ApiError(403, 'forbidden', 'Caregivers only')
    const o = current(db, id)
    if (!o) throw new ApiError(404, 'not_found', 'No active outage')
    const cg = db.households[id].caregivers.find((c) => c.id === t.caregiver_id)!
    const ack: Ack = { caregiver_id: cg.id, name: cg.name, message: body.message ?? null, eta_minutes: body.eta_minutes ?? null, at: now() }
    const next = { ...o, acks: [...o.acks.filter((a) => a.caregiver_id !== cg.id), ack] }
    db.outages[id] = db.outages[id].map((x) => (x.id === o.id ? next : x)); save(db)
    emit(id, { event: 'ack', data: { outage_id: o.id, ack } })
    return { outage: next }
  },
  async endOutage(id: string, token: string | null) {
    const db = load(); auth(db, token, id, true)
    const o = current(db, id)
    if (!o) throw new ApiError(404, 'not_found', 'No active outage')
    const next: Outage = { ...o, status: 'ended', ended_at: now() }
    db.outages[id] = db.outages[id].map((x) => (x.id === o.id ? next : x)); save(db)
    emit(id, { event: 'outage_ended', data: { outage: next, drill: o.source === 'drill' } })
    return { outage: next }
  },

  async join(join_code: string, name: string, relationship?: string) {
    const db = load()
    const h = Object.values(db.households).find((x) => x.join_code === join_code.toUpperCase().trim())
    if (!h) throw new ApiError(404, 'not_found', 'That code doesn’t match a home')
    const caregiver: Caregiver = { id: rid('cg'), name, relationship: relationship ?? '', joined_at: now() }
    const caregiver_token = rid('cgt') + rid('t')
    db.tokens[caregiver_token] = { household_id: h.id, role: 'caregiver', caregiver_id: caregiver.id }
    const next = touch(db, { ...h, caregivers: [...h.caregivers, caregiver] })
    return { caregiver, caregiver_token, household: next }
  },
  async removeCaregiver(id: string, token: string | null, cgId: string) {
    const db = load(); auth(db, token, id, true)
    const h = db.households[id]
    // Like the server: their token stops working and the old code is replaced.
    for (const [k, v] of Object.entries(db.tokens)) if (v.caregiver_id === cgId) delete db.tokens[k]
    touch(db, { ...h, caregivers: h.caregivers.filter((c) => c.id !== cgId), join_code: code() })
  },

  stream(id: string, token: string | null, cb: (e: StreamEvent) => void, onRevoked?: () => void) {
    const f = (hh: string, e: StreamEvent) => {
      if (hh !== id) return
      if (token && !load().tokens[token]) { local.delete(f); onRevoked?.(); return }
      cb(e)
    }
    local.add(f)
    const db = load()
    try {
      auth(db, token, id)
      const o = current(db, id)
      setTimeout(() => cb({ event: 'snapshot', data: { household: db.households[id], outage: o ? withMilestones(db.households[id], o) : null } }), 0)
    } catch { setTimeout(() => onRevoked?.(), 0) }
    return () => { local.delete(f) }
  },

  async helpSites(id: string, token: string | null, lat?: number, lon?: number) {
    const db = load(); auth(db, token, id)
    const h = db.households[id]
    return { sites: mockSites(lat ?? h.lat ?? 38.8899, lon ?? h.lon ?? -77.0091), source: 'fallback' as const, note: 'Sample places. Connect the server for real ones from OpenStreetMap.' }
  },
  async medicalPrograms(): Promise<{ programs: MedicalProgram[]; generic_advice: string[] }> {
    return { programs: [], generic_advice: [
      'Call your power company and ask for its medical, life-support or critical-care customer program.',
      'Most programs need a short form signed by a doctor or nurse practitioner.',
      'Being on the list gets you warnings before planned shutoffs and extra time before disconnection.',
    ] }
  },
  async medicalLetter(id: string, token: string | null) {
    const db = load(); auth(db, token, id, true)
    const h = db.households[id]
    const devs = h.devices.filter((d) => d.life_sustaining).map((d) => d.label).join(', ') || 'powered medical equipment'
    return {
      subject: `Request to register as a medical / life-support customer`,
      body: `To ${h.utility_name || 'my electric utility'},\n\nI am writing to register ${h.patient_first_name} at ${h.address || '[address]'} as a medical or life-support customer. ${h.patient_first_name} depends on ${devs}, which need electricity to work.\n\nPlease add this address to your priority list so we are notified before planned outages and shutoffs.\n\nClinician name: ____________________\nSignature: ____________________   Date: __________\n\nThank you.`,
    }
  },
  async identifyDevice(text: string): Promise<IdentifyResult> {
    const q = text.toLowerCase()
    const rules: [RegExp, string][] = [
      [/inogen|oxygo|simplygo|portable o|poc|rove|eclipse/, 'oxygen_concentrator_portable'],
      [/oxygen|concentrator|everflo|perfecto|newlife/, 'oxygen_concentrator_home'],
      [/trilogy|astral|vivo|ventilator|vent\b|lts/, 'home_ventilator'],
      [/cpap|bipap|airsense|dreamstation|resmed|apap/, 'cpap_bipap'],
      [/suction|aspirator/, 'suction_machine'],
      [/kangaroo|enteral|feeding|joey|infinity/, 'enteral_feeding_pump'],
      [/infusion|iv pump|cadd/, 'infusion_pump'],
      [/wheelchair|permobil|quantum|power chair/, 'power_wheelchair'],
      [/nebuli/, 'nebulizer'],
      [/fridge|insulin|refrigerat/, 'medication_fridge'],
      [/dialysis|nxstage/, 'home_dialysis'],
      [/oximeter|monitor/, 'pulse_oximeter_monitor'],
      [/mattress|air bed/, 'pressure_mattress_pump'],
    ]
    const hit = rules.find(([r]) => r.test(q))
    const t = templateFor(hit?.[1] ?? 'custom')
    return {
      match: { kind: t.kind, label: text.trim() || t.name, draw_watts: t.typical_draw_watts, internal_battery_wh: t.internal_battery_wh },
      confidence: hit ? 'medium' : 'low', source: 'catalog',
      explanation: hit ? `Matched “${text.trim()}” to ${t.name.toLowerCase()} typical figures.` : 'No match. Using generic figures; check the label.',
    }
  },
}

const SEED: { name: string; kind: HelpSite['kind']; dx: number; dy: number; power: boolean; street: string }[] = [
  { name: 'Mercy General Hospital', kind: 'hospital', dx: 0.018, dy: 0.011, power: true, street: 'Hospital Dr' },
  { name: 'Engine Company 18', kind: 'fire_station', dx: -0.006, dy: 0.007, power: true, street: '8th St' },
  { name: 'Eastside Community Center', kind: 'community_centre', dx: 0.009, dy: -0.012, power: false, street: 'Park Rd' },
  { name: 'Northeast Library', kind: 'library', dx: -0.013, dy: -0.004, power: false, street: 'Library Pl' },
  { name: 'Red Cross Shelter, St. Anne Hall', kind: 'shelter', dx: 0.004, dy: 0.019, power: true, street: 'Church St' },
]

export function mockSites(lat: number, lon: number): HelpSite[] {
  const R = 6371, r = Math.PI / 180
  return SEED.map((s, i) => {
    const pLat = lat + s.dy, pLon = lon + s.dx
    const dLat = (pLat - lat) * r, dLon = (pLon - lon) * r
    const x = Math.sin(dLat / 2) ** 2 + Math.cos(lat * r) * Math.cos(pLat * r) * Math.sin(dLon / 2) ** 2
    const km = 2 * R * Math.asin(Math.sqrt(x))
    return { id: `mock_${i}`, name: s.name, kind: s.kind, lat: pLat, lon: pLon, distance_km: km, travel_minutes: Math.max(3, Math.round(km * 2.4 + 2)), likely_has_power: s.power, address: `${200 + i * 137} ${s.street}` }
  }).sort((a, b) => a.distance_km - b.distance_km)
}
