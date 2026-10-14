import type { Outage, Plan } from './types'

/** Wall-clock moment a device's battery runs out, honoring drill speed. */
export function deadline(outage: Outage, minutes: number) {
  return new Date(outage.started_at).getTime() + (minutes * 60_000) / (outage.speed || 1)
}

/** Remaining real minutes in plan terms (what the clock "means"), for a drill or a real outage. */
export function planMinutesLeft(outage: Outage, minutes: number, now: number) {
  return Math.max(0, ((deadline(outage, minutes) - now) * (outage.speed || 1)) / 60_000)
}

export const minutesFor = (plan: Plan | null, deviceId: string) => plan?.devices.find((d) => d.device_id === deviceId)?.total_minutes ?? 0
