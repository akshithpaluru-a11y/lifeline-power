// Mirrors docs/API.md (v1). Keys are snake_case to match the backend exactly.

export type IconKey =
  | 'oxygen' | 'oxygen_portable' | 'ventilator' | 'cpap' | 'suction' | 'feeding_pump' | 'infusion_pump'
  | 'wheelchair' | 'nebulizer' | 'fridge' | 'dialysis' | 'monitor' | 'bed' | 'device'

export type Category = 'breathing' | 'feeding' | 'mobility' | 'monitoring' | 'therapy' | 'cooling' | 'other'

export interface DeviceTemplate {
  kind: string
  name: string
  category: Category
  icon: IconKey
  life_sustaining_default: boolean
  typical_draw_watts: number
  saver_draw_watts: number | null
  saver_tip: string | null
  internal_battery_wh: number
  duty_cycle: number
  ac_powered: boolean
  description: string
}

export interface Device {
  id: string
  kind: string
  label: string
  icon: IconKey
  life_sustaining: boolean
  draw_watts: number
  saver_draw_watts: number | null
  saver_enabled: boolean
  duty_cycle: number
  ac_powered: boolean
  internal_battery_wh: number
  spare_batteries_wh: number
  charge_percent: number
  notes: string
}

export type BackupType = 'power_station' | 'ups' | 'generator' | 'car_inverter'

export interface BackupSource {
  id: string
  type: BackupType
  label: string
  capacity_wh: number | null
  max_output_watts: number
  has_ac_outlet: boolean
  charge_percent: number | null
  fuel_hours: number | null
}

export interface BackupTemplate {
  type: BackupType
  name: string
  icon: 'power_station' | 'ups' | 'generator' | 'car'
  typical_capacity_wh: number | null
  typical_max_output_watts: number
  description: string
}

export interface Caregiver {
  id: string
  name: string
  relationship: string
  joined_at: string
}

export interface Household {
  id: string
  name: string
  patient_first_name: string
  address?: string | null
  lat?: number | null
  lon?: number | null
  state?: string | null
  utility_name?: string | null
  medical_program_registered: boolean
  join_code: string
  armed: boolean
  devices: Device[]
  backups: BackupSource[]
  caregivers: Caregiver[]
  created_at: string
}

export interface PlanDevice {
  device_id: string
  effective_draw_watts: number
  internal_minutes: number
  total_minutes: number
  allocations: { backup_id: string; energy_wh: number; minutes: number }[]
  status: 'ok' | 'limited' | 'no_backup'
  status_detail?: string | null
  plug_steps?: { text: string; backup_id: string; now: boolean }[]
}

export interface Recommendation {
  id: string
  kind: 'saver_mode' | 'add_backup' | 'charge' | 'register_program' | 'caregiver' | 'location' | 'safety'
  title: string
  detail: string
  gain_minutes: number | null
}

export interface Plan {
  computed_at: string
  assumes_saver_mode: boolean
  critical_runtime_minutes: number | null
  bottleneck_device_id: string | null
  devices: PlanDevice[]
  backups: { backup_id: string; usable_wh: number; allocated_wh: number; unused_wh: number; fuel_minutes?: number | null; device_ids?: string[] }[]
  recommendations: Recommendation[]
  assumptions: string[]
}

export type MilestoneKey = 'power_lost' | 'conserve' | 'check_in' | 'halfway' | 'leave_by' | 'critical_low' | 'depleted' | 'replug'

export interface Milestone {
  key: MilestoneKey
  at: string
  title: string
  detail: string
  severity: 'info' | 'warning' | 'critical'
  passed: boolean
}

export interface Ack {
  caregiver_id: string
  name: string
  message?: string | null
  eta_minutes?: number | null
  at: string
}

export interface Outage {
  id: string
  status: 'active' | 'ended'
  source: 'charging_lost' | 'manual' | 'drill'
  started_at: string
  ended_at: string | null
  plan: Plan
  milestones: Milestone[]
  acks: Ack[]
  last_check_in_at: string | null
  last_check_in_ok?: boolean | null
  speed: number
  drill?: boolean
}

export interface HelpSite {
  id: string
  name: string
  kind: 'hospital' | 'fire_station' | 'library' | 'community_centre' | 'police' | 'shelter' | 'other'
  lat: number
  lon: number
  distance_km: number
  travel_minutes: number
  likely_has_power: boolean
  address?: string | null
}

export interface MedicalProgram {
  utility: string
  states: string[]
  program_name: string
  what_it_does: string
  how_to_apply: string
  url: string
  verified_at: string
}

export interface IdentifyResult {
  match: Partial<Device> & { kind: string; label: string }
  confidence: 'high' | 'medium' | 'low'
  source: 'ai' | 'catalog'
  explanation: string
}

export type StreamEvent =
  | { event: 'snapshot'; data: { household: Household; outage: Outage | null } }
  | { event: 'outage_started'; data: { outage: Outage; drill: boolean } }
  | { event: 'milestone'; data: { outage_id: string; milestone: Milestone; drill: boolean } }
  | { event: 'check_in'; data: { outage: Outage } }
  | { event: 'missed_check_in'; data: { outage_id: string; minutes_overdue: number; drill: boolean } }
  | { event: 'ack'; data: { outage_id: string; ack: Ack } }
  | { event: 'outage_ended'; data: { outage: Outage; drill: boolean } }
  | { event: 'household_updated'; data: { household: Household } }
