import { useState } from 'react'
import { Link, useNavigate, useOutletContext } from 'react-router-dom'
import { motion } from 'motion/react'
import { BellRing, ChevronRight, IdCard, MapPin, Plug, PlugZap, Unplug, TrendingUp, BatteryCharging, Users, ShieldCheck, Leaf } from 'lucide-react'
import { useStore, saveDevice } from '../lib/store'
import { humanMinutes } from '../lib/format'
import { DeviceIcon } from '../components/DeviceIcon'
import { Pill } from '../components/Pill'
import { PulseTrace } from '../components/PulseTrace'
import { raiseOutage } from '../components/Shell'
import { primeAudio } from '../lib/alarm'
import type { useChargerWatch } from '../lib/power'
import type { Recommendation } from '../lib/types'
import { PrioritySheet } from './PrioritySheet'

const fade = (i: number) => ({ initial: { opacity: 0, y: 12 }, animate: { opacity: 1, y: 0 }, transition: { delay: 0.05 * i, duration: 0.6, ease: [0.16, 1, 0.3, 1] as const } })
const DRILL_SPEED = 60

export default function Home() {
  const h = useStore((s) => s.household)!
  const plan = useStore((s) => s.plan)
  const power = useOutletContext<ReturnType<typeof useChargerWatch>>()
  const nav = useNavigate()
  const [priority, setPriority] = useState(false)

  const rows = h.devices
    .map((d) => ({ d, p: plan?.devices.find((x) => x.device_id === d.id) }))
    .sort((a, b) => Number(b.d.life_sustaining) - Number(a.d.life_sustaining) || (a.p?.total_minutes ?? 0) - (b.p?.total_minutes ?? 0))
  const max = Math.max(60, ...rows.map((r) => r.p?.total_minutes ?? 0))
  const bottleneck = h.devices.find((d) => d.id === plan?.bottleneck_device_id)
  const crit = plan?.critical_runtime_minutes

  const charger = power.support === 'battery-api'
    ? power.charging
      ? { icon: PlugZap, text: `Plugged in · ${Math.round((power.level ?? 0) * 100)}%`, tone: 'live' as const }
      : { icon: Unplug, text: `On battery · ${Math.round((power.level ?? 0) * 100)}%`, tone: 'amber' as const }
    : { icon: Plug, text: power.support === 'pending' ? 'Checking charger…' : 'Charger not visible', tone: 'muted' as const }

  const manual = async () => { primeAudio(); await raiseOutage('manual'); nav('/app/outage') }
  const drill = async () => { primeAudio(); await raiseOutage('drill', DRILL_SPEED); nav('/app/outage') }

  const act = (r: Recommendation) => {
    if (r.kind === 'saver_mode') {
      const d = h.devices.find((x) => !x.saver_enabled && x.saver_draw_watts != null && (r.id.includes(x.id) || r.id.includes(x.icon) || r.title.includes(x.label)))
      if (d) return saveDevice({ ...d, saver_enabled: true })
    }
    if (r.kind === 'register_program') return setPriority(true)
    if (r.kind === 'caregiver') return nav('/app/people')
    if (r.kind === 'location') return nav('/app/help')
    return nav('/app/devices')
  }
  const recIcon = { saver_mode: Leaf, add_backup: BatteryCharging, charge: BatteryCharging, register_program: ShieldCheck, caregiver: Users, location: MapPin }

  return (
    <div className="space-y-4 pt-2">
      <motion.section {...fade(0)} className="surface grain relative overflow-hidden rounded-[28px] p-5">
        <div className="pointer-events-none absolute -right-16 -top-20 size-56 rounded-full bg-live/10 blur-3xl" />
        <div className="flex items-center justify-between gap-2">
          <span className="truncate text-[12px] font-medium uppercase tracking-[0.14em] text-fg-3">{h.name}</span>
          <Pill tone={charger.tone} pulse={charger.tone === 'live'} className="shrink-0"><charger.icon size={12} strokeWidth={2} />{charger.text}</Pill>
        </div>
        <h1 className="mt-5 text-[34px] font-semibold leading-[1.02] tracking-[-0.035em]">
          Power is on.<br /><span className="text-fg-3">{h.armed ? 'We’re watching.' : 'Watching is paused.'}</span>
        </h1>
        <div className="-mx-5 mt-4"><PulseTrace tone={h.armed ? 'live' : 'amber'} flat={!h.armed} /></div>
        <p className="mt-3 text-[13.5px] leading-relaxed text-fg-2">
          Keep this phone plugged in near {h.patient_first_name}. If the outlet goes dead, LifeLine sounds an alarm, starts the countdown and alerts {h.caregivers.length || 'your'} {h.caregivers.length === 1 ? 'caregiver' : 'caregivers'}.
        </p>
        <p className="mt-2 text-[12px] leading-relaxed text-fg-4">Keep cellular data on. The Wi-Fi router goes down with the power.</p>
        {power.support === 'unsupported' && (
          <div className="mt-3 rounded-xl bg-white/[0.04] p-3">
            <p className="text-[12.5px] leading-relaxed text-fg-3">This browser can’t see the charger. Open LifeLine in Chrome on Android or a laptop to catch outages automatically, or tap below when the power goes out.</p>
            <button onClick={manual} className="mt-3 flex w-full items-center justify-center gap-2 rounded-xl bg-alert/15 py-2.5 text-[13.5px] font-medium text-alert ring-1 ring-inset ring-alert/25">
              <Unplug size={15} /> The power’s out
            </button>
          </div>
        )}
      </motion.section>

      <motion.section {...fade(1)} className="surface rounded-[28px] p-5">
        <div className="flex items-baseline justify-between">
          <h2 className="text-[15px] font-semibold tracking-tight">If the power went out now</h2>
          <Link to="/app/devices" className="text-[12.5px] text-fg-3 hover:text-fg">Edit</Link>
        </div>
        {crit != null && bottleneck && (
          <div className="mt-4 flex items-end justify-between gap-4 rounded-2xl bg-amber/[0.06] p-4 ring-1 ring-inset ring-amber/15">
            <div>
              <div className="text-[11.5px] font-medium uppercase tracking-[0.12em] text-amber/80">Life support lasts</div>
              <div className="tnum mt-1 text-[34px] font-semibold leading-none tracking-[-0.03em] text-amber">{humanMinutes(crit)}</div>
            </div>
            <div className="text-right text-[12px] leading-snug text-fg-3">limited by<br /><span className="text-fg-2">{bottleneck.label}</span></div>
          </div>
        )}
        <ul className="mt-4 space-y-4">
          {rows.map(({ d, p }) => {
            const total = p?.total_minutes ?? 0
            const own = Math.min(total, p?.internal_minutes ?? 0)
            const none = total <= 0
            return (
              <li key={d.id} className="flex items-center gap-3.5">
                <span className={`grid size-10 shrink-0 place-items-center rounded-2xl ring-1 ring-inset ${d.life_sustaining ? 'bg-amber/10 text-amber ring-amber/20' : 'bg-white/5 text-fg-2 ring-white/10'}`}>
                  <DeviceIcon icon={d.icon} />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="truncate text-[14px] font-medium">{d.label}</span>
                    <span className={`tnum shrink-0 text-[13px] font-medium ${none ? 'text-alert' : d.id === bottleneck?.id ? 'text-amber' : 'text-fg-2'}`}>{none ? 'No backup' : humanMinutes(total)}</span>
                  </div>
                  <div className="mt-2 flex h-1.5 gap-[2px] overflow-hidden rounded-full bg-white/[0.06]">
                    {own > 0 && <motion.span className={`h-full ${d.life_sustaining ? 'bg-amber' : 'bg-fg-3'}`} initial={{ width: 0 }} animate={{ width: `${(own / max) * 100}%` }} transition={{ duration: 1, ease: [0.16, 1, 0.3, 1] }} />}
                    {total > own && <motion.span className="h-full bg-sky" initial={{ width: 0 }} animate={{ width: `${((total - own) / max) * 100}%` }} transition={{ duration: 1, delay: 0.15, ease: [0.16, 1, 0.3, 1] }} />}
                  </div>
                </div>
              </li>
            )
          })}
        </ul>
        {h.backups.length > 0 && (
          <div className="mt-4 flex items-center gap-4 border-t border-white/[0.06] pt-3.5 text-[11.5px] text-fg-3">
            <span className="flex items-center gap-1.5"><span className="size-2 rounded-full bg-amber" /> Device battery</span>
            <span className="flex items-center gap-1.5"><span className="size-2 rounded-full bg-sky" /> From backup power</span>
          </div>
        )}
      </motion.section>

      <motion.section {...fade(2)} className="grid grid-cols-3 gap-2.5">
        {[
          { icon: BellRing, label: 'Run a drill', onClick: drill, accent: true },
          { icon: IdCard, label: 'ER card', to: '/app/card' },
          { icon: MapPin, label: 'Nearby help', to: '/app/help' },
        ].map((a) => {
          const inner = (
            <>
              <span className={`grid size-9 place-items-center rounded-xl ${a.accent ? 'bg-amber text-ink-950' : 'bg-white/[0.06] text-fg'}`}><a.icon size={17} strokeWidth={1.9} /></span>
              <span className="text-[12.5px] font-medium">{a.label}</span>
            </>
          )
          const cls = 'surface flex flex-col items-start gap-3 rounded-[22px] p-3.5 text-left transition hover:bg-white/[0.03] active:scale-[.98]'
          return a.to ? <Link key={a.label} to={a.to} className={cls}>{inner}</Link> : <button key={a.label} onClick={a.onClick} className={cls}>{inner}</button>
        })}
      </motion.section>

      {!!plan?.recommendations.length && (
        <motion.section {...fade(3)} className="surface rounded-[28px] p-5">
          <div className="flex items-center gap-2">
            <TrendingUp size={16} className="text-live" />
            <h2 className="text-[15px] font-semibold tracking-tight">Get more time</h2>
          </div>
          <ul className="mt-3 divide-y divide-white/[0.05]">
            {plan.recommendations.slice(0, 5).map((r) => {
              const I = recIcon[r.kind] ?? TrendingUp
              return (
                <li key={r.id}>
                  <button onClick={() => act(r)} className="flex w-full items-center gap-3 py-3 text-left">
                    <span className="grid size-8 shrink-0 place-items-center rounded-xl bg-white/[0.05] text-fg-2"><I size={15} /></span>
                    <span className="min-w-0 flex-1">
                      <span className="block text-[13.5px] leading-snug">{r.title}</span>
                      <span className="line-clamp-2 block text-[12px] leading-snug text-fg-3">{r.detail}</span>
                    </span>
                    {r.gain_minutes ? <Pill tone="live" className="shrink-0">+{humanMinutes(r.gain_minutes)}</Pill> : <ChevronRight size={16} className="shrink-0 text-fg-4" />}
                  </button>
                </li>
              )
            })}
          </ul>
        </motion.section>
      )}

      <PrioritySheet open={priority} onClose={() => setPriority(false)} />
    </div>
  )
}
