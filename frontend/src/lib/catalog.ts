import type { BackupTemplate, DeviceTemplate } from './types'

// Offline copy of GET /api/catalog/*, used until the backend answers. Typical figures only;
// every number is editable per device.
const t = (kind: string, name: string, category: DeviceTemplate['category'], icon: DeviceTemplate['icon'], life: boolean,
  draw: number, saver: number | null, battery: number, duty: number, ac: boolean, description: string, saver_tip: string | null = null): DeviceTemplate =>
  ({ kind, name, category, icon, life_sustaining_default: life, typical_draw_watts: draw, saver_draw_watts: saver, saver_tip, internal_battery_wh: battery, duty_cycle: duty, ac_powered: ac, description })

export const DEVICE_CATALOG: DeviceTemplate[] = [
  t('oxygen_concentrator_home', 'Home oxygen concentrator', 'breathing', 'oxygen', true, 300, 180, 0, 1, true, 'Stationary concentrators plug into the wall and have no battery.', 'Ask your care team ahead of time if a lower flow setting is safe for you.'),
  t('oxygen_concentrator_portable', 'Portable oxygen concentrator', 'breathing', 'oxygen_portable', true, 40, 25, 99, 1, false, 'Battery-powered unit, pulse-dose setting 2.', 'Pulse mode instead of continuous flow uses far less power, if your clinician allows it.'),
  t('home_ventilator', 'Home ventilator', 'breathing', 'ventilator', true, 30, null, 90, 1, true, 'Most home ventilators have an internal and a detachable battery.'),
  t('cpap_bipap', 'CPAP or BiPAP', 'breathing', 'cpap', false, 55, 15, 0, 1, true, 'Heated humidifier uses most of the power.', 'Turn the heated humidifier off during an outage.'),
  t('suction_machine', 'Suction machine', 'breathing', 'suction', true, 60, null, 60, 0.15, true, 'Runs in short bursts.'),
  t('enteral_feeding_pump', 'Feeding pump', 'feeding', 'feeding_pump', false, 4, null, 44, 1, false, 'Enteral pump with internal battery.'),
  t('infusion_pump', 'Infusion pump', 'feeding', 'infusion_pump', true, 6, null, 30, 1, false, 'IV medication pump.'),
  t('power_wheelchair', 'Power wheelchair', 'mobility', 'wheelchair', false, 120, null, 1200, 0.25, false, 'Two 12 V batteries.'),
  t('nebulizer', 'Nebulizer', 'breathing', 'nebulizer', false, 80, null, 0, 0.1, true, 'A few short treatments a day.'),
  t('medication_fridge', 'Medication fridge', 'cooling', 'fridge', false, 60, null, 0, 0.35, true, 'Insulin and other refrigerated medicine.'),
  t('home_dialysis', 'Home dialysis machine', 'therapy', 'dialysis', true, 500, null, 0, 1, true, 'Needs a large backup to finish a session.'),
  t('pulse_oximeter_monitor', 'Pulse oximeter / monitor', 'monitoring', 'monitor', false, 5, null, 10, 1, false, 'Bedside monitor.'),
  t('pressure_mattress_pump', 'Pressure mattress pump', 'therapy', 'bed', false, 25, null, 0, 1, true, 'Air mattress that prevents pressure sores.'),
  t('custom', 'Other device', 'other', 'device', false, 50, null, 0, 1, true, 'Enter numbers from the device label.'),
]

export const BACKUP_CATALOG: BackupTemplate[] = [
  { type: 'power_station', name: 'Portable power station', icon: 'power_station', typical_capacity_wh: 1000, typical_max_output_watts: 1500, description: 'Battery box with AC outlets, like a Jackery or EcoFlow.' },
  { type: 'ups', name: 'UPS battery backup', icon: 'ups', typical_capacity_wh: 300, typical_max_output_watts: 600, description: 'Plugs in between the wall and the device.' },
  { type: 'generator', name: 'Generator', icon: 'generator', typical_capacity_wh: null, typical_max_output_watts: 3500, description: 'Runs on fuel. Outdoors only.' },
  { type: 'car_inverter', name: 'Car with inverter', icon: 'car', typical_capacity_wh: null, typical_max_output_watts: 300, description: 'Power from a running car through an inverter.' },
]

export const templateFor = (kind: string) => DEVICE_CATALOG.find((d) => d.kind === kind) ?? DEVICE_CATALOG[DEVICE_CATALOG.length - 1]
