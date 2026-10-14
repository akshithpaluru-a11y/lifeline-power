import { Wind, HeartPulse, Moon, Activity, Droplets, Syringe, Accessibility, CloudFog, Refrigerator, Waves, MonitorDot, BedDouble, Cpu, BatteryCharging, Zap, Fuel, Car } from 'lucide-react'
import type { IconKey } from '../lib/types'

const MAP: Record<IconKey, typeof Wind> = {
  oxygen: Wind,
  oxygen_portable: Wind,
  ventilator: HeartPulse,
  cpap: Moon,
  suction: Activity,
  feeding_pump: Droplets,
  infusion_pump: Syringe,
  wheelchair: Accessibility,
  nebulizer: CloudFog,
  fridge: Refrigerator,
  dialysis: Waves,
  monitor: MonitorDot,
  bed: BedDouble,
  device: Cpu,
}

export function DeviceIcon({ icon, size = 18, className = '' }: { icon: IconKey; size?: number; className?: string }) {
  const I = MAP[icon] ?? Cpu
  return <I size={size} strokeWidth={1.75} className={className} />
}

const BACKUP = { power_station: BatteryCharging, ups: Zap, generator: Fuel, car_inverter: Car }
export function BackupIcon({ type, size = 18, className = '' }: { type: keyof typeof BACKUP; size?: number; className?: string }) {
  const I = BACKUP[type]
  return <I size={size} strokeWidth={1.75} className={className} />
}
