import type { BackupSource, Device, Household, Milestone, Plan, PlanDevice, Recommendation } from './types'

// A TypeScript copy of the backend's planner rules (docs/API.md, "Planner rules"), used when the
// app runs without the server. The server's plan always wins when it's available.

const USABLE = 0.9
const INVERTER = 0.85

export function effectiveDraw(d: Device, saver = d.saver_enabled) {
  const w = saver && d.saver_draw_watts != null ? d.saver_draw_watts : d.draw_watts
  return w * d.duty_cycle
}

export function computePlan(h: Household, saver = false): Plan {
  const rows = h.devices.map((d) => {
    const draw = effectiveDraw(d, saver || d.saver_enabled)
    const wh = (d.internal_battery_wh * (d.charge_percent / 100) + d.spare_batteries_wh) * USABLE
    const internal = draw > 0 ? (wh / draw) * 60 : 0
    return { d, draw, internal, total: internal, allocations: [] as PlanDevice['allocations'] }
  })

  const pools = h.backups
    .filter((b) => b.type === 'power_station' || b.type === 'ups')
    .map((b) => ({ b, left: (b.capacity_wh ?? 0) * ((b.charge_percent ?? 100) / 100) * USABLE, usable: (b.capacity_wh ?? 0) * ((b.charge_percent ?? 100) / 100) * USABLE }))

  const canUse = (b: BackupSource, d: Device, draw: number) => (!d.ac_powered || b.has_ac_outlet) && draw <= b.max_output_watts

  // Water-filling in small steps: always top up whichever device runs out first.
  const fill = (group: typeof rows) => {
    for (let guard = 0; guard < 20000; guard++) {
      const live = group.filter((r) => r.draw > 0 && pools.some((p) => p.left > 0.01 && canUse(p.b, r.d, r.draw)))
      if (!live.length) break
      const r = live.reduce((a, b) => (b.total < a.total ? b : a))
      const p = pools.find((p) => p.left > 0.01 && canUse(p.b, r.d, r.draw))!
      const step = Math.min(p.left, Math.max(1, p.usable / 400))
      const delivered = r.d.ac_powered ? step * INVERTER : step
      const mins = (delivered / r.draw) * 60
      p.left -= step
      r.total += mins
      const a = r.allocations.find((x) => x.backup_id === p.b.id)
      if (a) { a.energy_wh += step; a.minutes += mins } else r.allocations.push({ backup_id: p.b.id, energy_wh: step, minutes: mins })
    }
  }
  fill(rows.filter((r) => r.d.life_sustaining))
  fill(rows.filter((r) => !r.d.life_sustaining))

  for (const g of h.backups.filter((b) => (b.type === 'generator' || b.type === 'car_inverter') && b.fuel_hours)) {
    for (const r of rows) if (canUse(g, r.d, r.draw) && r.draw > 0) {
      r.total += g.fuel_hours! * 60
      r.allocations.push({ backup_id: g.id, energy_wh: r.draw * g.fuel_hours!, minutes: g.fuel_hours! * 60 })
    }
  }

  const devices: PlanDevice[] = rows.map((r) => ({
    device_id: r.d.id,
    effective_draw_watts: r.draw,
    internal_minutes: r.internal,
    total_minutes: r.total,
    allocations: r.allocations,
    status: r.total <= 0 ? 'no_backup' : r.allocations.length || r.internal > 0 ? 'ok' : 'limited',
  }))

  const critical = rows.filter((r) => r.d.life_sustaining)
  const bottleneck = critical.length ? critical.reduce((a, b) => (b.total < a.total ? b : a)) : null

  const recs: Recommendation[] = []
  for (const r of rows) {
    if (!r.d.saver_enabled && r.d.saver_draw_watts != null && r.d.saver_draw_watts < r.d.draw_watts) {
      recs.push({ id: `saver_${r.d.id}`, kind: 'saver_mode', title: r.d.kind === 'cpap_bipap' ? `Turn off the ${r.d.label} humidifier during an outage` : `Use low-power mode on ${r.d.label}`, detail: `Cuts its power use from ${r.d.draw_watts} W to ${r.d.saver_draw_watts} W.`, gain_minutes: r.draw > 0 ? r.total * (r.d.draw_watts / r.d.saver_draw_watts - 1) : null })
    }
    if (r.d.internal_battery_wh > 0 && r.d.charge_percent < 90) {
      recs.push({ id: `charge_${r.d.id}`, kind: 'charge', title: `Charge ${r.d.label} to full`, detail: `It’s at ${r.d.charge_percent}%. Keep it topped up so the clock starts full.`, gain_minutes: r.draw > 0 ? ((r.d.internal_battery_wh * (1 - r.d.charge_percent / 100) * USABLE) / r.draw) * 60 : null })
    }
  }
  if (!h.backups.length) recs.push({ id: 'add_backup', kind: 'add_backup', title: 'Add a portable power station', detail: 'A 1,000 Wh station adds hours to every device and charges from a car or solar panel.', gain_minutes: null })
  if (!h.medical_program_registered) recs.push({ id: 'register_program', kind: 'register_program', title: `Register with ${h.utility_name || 'your power company'} as a medical customer`, detail: 'You get warned before planned shutoffs.', gain_minutes: null })
  if (h.caregivers.length < 2) recs.push({ id: 'caregiver', kind: 'caregiver', title: 'Add a second caregiver', detail: 'Share the join code so two people get every alert.', gain_minutes: null })

  return {
    computed_at: new Date().toISOString(),
    assumes_saver_mode: saver,
    critical_runtime_minutes: bottleneck ? bottleneck.total : null,
    bottleneck_device_id: bottleneck?.d.id ?? null,
    devices,
    backups: pools.map((p) => ({ backup_id: p.b.id, usable_wh: p.usable, allocated_wh: p.usable - p.left, unused_wh: p.left })),
    recommendations: recs.sort((a, b) => (b.gain_minutes ?? -1) - (a.gain_minutes ?? -1)),
    assumptions: ['Batteries deliver about 90% of their rating.', 'AC devices on a power station lose about 15% to the inverter.'],
  }
}

export function computeMilestones(plan: Plan, startedAt: number, speed: number, travelMinutes = 30, siteName = 'the nearest hospital'): Milestone[] {
  const crit = plan.critical_runtime_minutes ?? 0
  const at = (min: number) => new Date(startedAt + (min * 60_000) / speed).toISOString()
  const now = Date.now()
  const m: Milestone[] = [
    { key: 'power_lost', at: at(0), title: 'Power lost', detail: 'Alarm sounded and caregivers alerted.', severity: 'info', passed: true },
    { key: 'conserve', at: at(0), title: 'Save battery', detail: 'Switch devices to low-power settings and unplug anything you don’t need.', severity: 'info', passed: true },
  ]
  const leave = crit - travelMinutes - 30
  if (crit > 0) {
    // Same as the server: when the battery can't last the drive, say so up front.
    if (crit <= travelMinutes) m.push({ key: 'leave_by', at: at(0), title: 'Not enough battery to reach power', detail: `The battery won’t last the ${travelMinutes} min drive to ${siteName}. Call 911 now.`, severity: 'critical', passed: false })
    m.push({ key: 'check_in', at: at(Math.min(60, crit / 2)), title: 'Check in', detail: 'Tap “I’m OK” so your family knows.', severity: 'info', passed: false })
    if (crit / 2 < leave) m.push({ key: 'halfway', at: at(crit / 2), title: 'Halfway', detail: 'Half the critical battery is gone.', severity: 'warning', passed: false })
    else m.push({ key: 'leave_by', at: at(Math.max(0, leave)), title: 'Leave for a powered location', detail: `Head to ${siteName} (${travelMinutes} min away) before batteries get low.`, severity: 'warning', passed: false })
    m.push({ key: 'critical_low', at: at(Math.max(0, crit - 30)), title: '30 minutes left', detail: 'Call 911 if you can’t reach power.', severity: 'critical', passed: false })
    m.push({ key: 'depleted', at: at(crit), title: 'Battery empty', detail: 'Life-sustaining battery runs out.', severity: 'critical', passed: false })
  }
  return m.map((x) => ({ ...x, passed: x.passed || new Date(x.at).getTime() <= now })).sort((a, b) => a.at.localeCompare(b.at))
}
