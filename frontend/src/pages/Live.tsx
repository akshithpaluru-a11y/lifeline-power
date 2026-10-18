import { useEffect, useRef, useState } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import { AnimatePresence, motion } from 'motion/react'
import { Car, Check, MapPin, PlugZap, ShieldCheck, Zap, Flag, TriangleAlert, Clock, Phone, Plug } from 'lucide-react'
import { api } from '../lib/api'
import { getCaregiver, setCaregiver } from '../lib/caregiver'
import { useNow } from '../lib/useNow'
import { clock, hms, humanMinutes } from '../lib/format'
import { criticalLeft, upsertMilestone } from '../lib/time'
import { startAlarm, stopAlarm, notify } from '../lib/alarm'
import type { Household, Outage, Plan, StreamEvent } from '../lib/types'
import { Logo } from '../components/Logo'
import { Pill } from '../components/Pill'
import { Segments } from '../components/Segments'
import { DeviceIcon } from '../components/DeviceIcon'
import { Button } from '../components/Button'
import { PulseTrace } from '../components/PulseTrace'

export default function Live() {
  const me = getCaregiver()
  const nav = useNavigate()
  const [h, setH] = useState<Household | null>(null)
  const [plan, setPlan] = useState<Plan | null>(null)
  const [outage, setOutage] = useState<Outage | null>(null)
  const [ended, setEnded] = useState<Outage | null>(null)
  const [missed, setMissed] = useState<number | null>(null)
  const [eta, setEta] = useState<number | null>(15)
  const [busy, setBusy] = useState(false)
  const outageRef = useRef<Outage | null>(null)
  outageRef.current = outage
  const now = useNow(outage && outage.speed > 1 ? 250 : 1000)

  useEffect(() => {
    if (!me) return
    const onEvent = (e: StreamEvent) => {
      switch (e.event) {
        case 'snapshot': setH(e.data.household); setOutage(e.data.outage); break
        case 'household_updated': setH(e.data.household); break
        case 'outage_started':
          setOutage(e.data.outage); setEnded(null); setMissed(null); startAlarm()
          notify(e.data.drill ? 'Drill: power is out' : 'Power is out', 'Open LifeLine to see the countdown.')
          break
        case 'check_in': setOutage(e.data.outage); setMissed(null); break
        case 'ack': setOutage((o) => (o && o.id === e.data.outage_id ? { ...o, acks: [...o.acks.filter((a) => a.caregiver_id !== e.data.ack.caregiver_id), e.data.ack] } : o)); break
        case 'milestone': setOutage((o) => (o && o.id === e.data.outage_id ? { ...o, milestones: upsertMilestone(o.milestones, e.data.milestone) } : o)); break
        case 'missed_check_in': setMissed(e.data.minutes_overdue); startAlarm(); break
        case 'outage_ended': setOutage(null); setEnded(e.data.outage); stopAlarm(); break
      }
    }
    // The owner removed this caregiver: their link no longer works, so send them back to join.
    const revoked = () => { stopAlarm(); setCaregiver(null); nav('/join?removed=1', { replace: true }) }
    const stop = api.stream(me.household_id, me.token, onEvent, revoked)
    return () => { stop(); stopAlarm() }
  }, [me?.token])

  useEffect(() => { if (me && h) api.plan(me.household_id, me.token).then((r) => setPlan(r.plan)).catch(() => {}) }, [h, me?.token])

  if (!me) return <Navigate to="/join" replace />
  if (!h) return <div className="min-h-dvh bg-ink-950" />

  const first = h.patient_first_name
  const mine = outage?.acks.find((a) => a.caregiver_id === me.caregiver_id)
  const active = !!outage

  return (
    <div className="min-h-dvh bg-ink-950">
      <div className={`pointer-events-none fixed inset-x-0 top-0 h-[480px] transition-colors ${active ? 'bg-[radial-gradient(60%_80%_at_50%_0%,rgb(255_90_78/.2),transparent)]' : 'bg-[radial-gradient(60%_80%_at_50%_0%,rgb(61_220_151/.12),transparent)]'}`} />
      <div className="relative mx-auto max-w-[440px] px-5 pb-40 pt-[max(1rem,env(safe-area-inset-top))]">
        <div className="flex items-center justify-between">
          <Logo size={24} />
          <span className="text-[12px] text-fg-3">Signed in as {me.name}</span>
        </div>

        <AnimatePresence mode="wait">
          {active ? <OutageView key="o" outage={outage} h={h} now={now} missed={missed} /> : (
            <motion.section key="ok" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} className="mt-8">
              {ended ? <Pill tone="live"><Check size={12} /> POWER RESTORED {clock(ended.ended_at!)}</Pill> : <Pill tone="live" pulse>ALL CLEAR</Pill>}
              <h1 className="mt-4 text-[32px] font-semibold leading-[1.05] tracking-[-0.035em]">{first}’s power is on.</h1>
              <p className="mt-2 text-[14px] text-fg-3">{h.armed ? 'LifeLine is watching the outlet. You’ll be alerted the second it goes out.' : 'Watching is paused at home right now.'}</p>
              <div className="-mx-5 mt-6"><PulseTrace tone={h.armed ? 'live' : 'amber'} flat={!h.armed} height={64} /></div>
              {plan?.critical_runtime_minutes != null && (
                <div className="surface mt-6 rounded-[24px] p-5">
                  <div className="text-[12px] font-medium uppercase tracking-[0.12em] text-fg-3">If it goes out, life support lasts</div>
                  <div className="tnum mt-2 text-[40px] font-semibold leading-none tracking-[-0.03em] text-amber">{humanMinutes(plan.critical_runtime_minutes)}</div>
                  <ul className="mt-5 space-y-2.5 border-t border-white/[0.06] pt-4">
                    {h.devices.map((d) => (
                      <li key={d.id} className="flex items-center gap-3 text-[13.5px]">
                        <DeviceIcon icon={d.icon} size={16} className={d.life_sustaining ? 'text-amber' : 'text-fg-3'} />
                        <span className="flex-1 text-fg-2">{d.label}</span>
                        <span className="tnum font-medium text-fg-2">{humanMinutes(plan.devices.find((p) => p.device_id === d.id)?.total_minutes ?? 0)}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {h.address && <p className="mt-4 flex items-center gap-2 text-[13px] text-fg-3"><MapPin size={14} /> {h.address}</p>}
            </motion.section>
          )}
        </AnimatePresence>
      </div>

      {active && (
        <div className="fixed inset-x-0 bottom-0 z-40">
          <div className="mx-auto max-w-[440px] bg-gradient-to-t from-ink-950 via-ink-950/95 to-transparent px-4 pb-[max(.75rem,env(safe-area-inset-bottom))] pt-8">
            {!mine && (
              <div className="mb-2.5 flex items-center gap-1.5">
                <span className="mr-1 text-[12px] text-fg-3">ETA</span>
                {[10, 15, 30, 60].map((m) => (
                  <button key={m} onClick={() => setEta(m)} className={`tnum flex-1 rounded-xl py-2 text-[12.5px] font-medium ring-1 ring-inset ${eta === m ? 'bg-fg text-ink-950 ring-fg' : 'bg-white/[0.04] text-fg-2 ring-white/10'}`}>{m < 60 ? `${m}m` : '1h+'}</button>
                ))}
              </div>
            )}
            <div className="flex gap-2.5">
              <Button size="lg" className="flex-1" variant={mine ? 'secondary' : 'primary'} disabled={!!mine || busy}
                icon={mine ? <Check size={18} /> : <Car size={18} />}
                onClick={async () => { setBusy(true); stopAlarm(); try { const r = await api.ack(me.household_id, me.token, { message: 'On my way', eta_minutes: eta ?? undefined }); setOutage(r.outage) } finally { setBusy(false) } }}>
                {mine ? `${first} knows you’re coming` : 'I’m on my way'}
              </Button>
              {h.lat != null && (
                <a href={`https://www.google.com/maps/dir/?api=1&destination=${h.lat},${h.lon}`} target="_blank" rel="noreferrer"
                  className="grid size-14 place-items-center rounded-[18px] bg-white/[0.06] ring-1 ring-inset ring-white/10" aria-label="Directions"><MapPin size={19} /></a>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function OutageView({ outage, h, now, missed }: { outage: Outage; h: Household; now: number; missed: number | null }) {
  const first = h.patient_first_name
  const crit = outage.plan.critical_runtime_minutes ?? 0
  const bottleneck = h.devices.find((d) => d.id === outage.plan.bottleneck_device_id)
  const left = criticalLeft(outage, now)
  const t = hms(left * 60_000)
  const elapsed = hms((now - new Date(outage.started_at).getTime()) * outage.speed)
  const drill = outage.drill ?? outage.source === 'drill'
  const replug = [...outage.milestones].reverse().find((m) => m.key === 'replug' && m.passed)
  const noTime = outage.milestones.find((m) => m.key === 'leave_by' && m.severity === 'critical')
    ?? (outage.plan.critical_runtime_minutes === 0 && bottleneck ? { title: `${first}’s ${bottleneck.label} has no power`, detail: 'It has no battery or backup that can run it. Call or go now, and call 911 if you can’t reach them.' } : undefined)

  const events = [
    { at: outage.started_at, icon: Zap, text: outage.source === 'charging_lost' ? 'Power cut detected at home' : drill ? 'Drill started' : 'Outage mode started', tone: 'text-alert' },
    ...(outage.last_check_in_at && new Date(outage.last_check_in_at).getTime() - new Date(outage.started_at).getTime() > 5000 ? [{ at: outage.last_check_in_at, icon: ShieldCheck, text: `${first} checked in: OK`, tone: 'text-live' }] : []),
    ...outage.acks.map((a) => ({ at: a.at, icon: Car, text: `${a.name} is on the way${a.eta_minutes ? ` · ${a.eta_minutes} min` : ''}`, tone: 'text-live' })),
    ...outage.milestones.filter((m) => m.passed && !['power_lost', 'conserve'].includes(m.key)).map((m) => ({ at: m.at, icon: m.severity === 'critical' ? TriangleAlert : Flag, text: m.title, tone: m.severity === 'critical' ? 'text-alert' : 'text-amber' })),
  ].sort((a, b) => b.at.localeCompare(a.at))
  const next = outage.milestones.find((m) => !m.passed && new Date(m.at).getTime() > now && m.key !== 'depleted')

  return (
    <motion.section initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} className="mt-8">
      <div className="flex gap-2"><Pill tone="alert" pulse>POWER OUT · {elapsed.text}</Pill>{drill && <Pill tone="sky">DRILL</Pill>}</div>
      <h1 className="mt-4 text-[32px] font-semibold leading-[1.05] tracking-[-0.035em]">{first}’s power is out.</h1>
      <p className="mt-2 text-[14px] text-fg-3">{h.address ?? h.name} · since {clock(outage.started_at)}</p>

      {missed != null && (
        <div className="mt-4 flex items-center gap-3 rounded-2xl bg-alert/10 p-3.5 ring-1 ring-inset ring-alert/25">
          <TriangleAlert size={18} className="shrink-0 text-alert" />
          <span className="text-[13.5px]">{first} hasn’t checked in for {Math.round(missed)} min. Call or go now.</span>
        </div>
      )}

      {noTime && (
        <div className="mt-4 rounded-2xl bg-alert/15 p-4 ring-1 ring-inset ring-alert/40">
          <div className="flex items-center gap-2 text-[15px] font-semibold text-alert"><TriangleAlert size={18} className="shrink-0" /> {noTime.title}</div>
          <p className="mt-1.5 text-[13px] leading-relaxed text-fg-2">{noTime.detail}</p>
          <a href="tel:911" className="mt-3 flex items-center justify-center gap-2 rounded-xl bg-alert py-2.5 text-[14px] font-semibold text-ink-950"><Phone size={15} /> Call 911</a>
        </div>
      )}

      {replug && !noTime && (
        <div className="mt-4 rounded-2xl bg-amber/10 p-3.5 ring-1 ring-inset ring-amber/25">
          <div className="flex items-center gap-2 text-[14px] font-semibold text-amber"><Plug size={16} className="shrink-0" /> {first} needs to move plugs</div>
          <p className="mt-1 text-[13px] leading-relaxed text-fg-2">{replug.detail}</p>
        </div>
      )}

      {bottleneck && (
        <section className="surface mt-6 rounded-[26px] p-5">
          <div className="flex items-center gap-2 text-[12px] font-medium uppercase tracking-[0.14em] text-fg-3">
            <DeviceIcon icon={bottleneck.icon} size={14} /> {bottleneck.label}
          </div>
          <div className="tnum mt-2 font-mono text-[52px] font-medium leading-none tracking-[-0.04em]">
            {t.h}<span className="text-fg-3">:</span>{String(t.m).padStart(2, '0')}<span className="text-[26px] text-fg-3">:{String(t.s).padStart(2, '0')}</span>
          </div>
          <p className="mt-2 text-[13px] text-fg-3">of life support left</p>
          <Segments value={crit ? Math.min(1, left / crit) : 0} count={32} tone={left < 60 ? 'alert' : 'amber'} className="mt-4 h-2" />
          {next && (
            <p className="mt-4 flex items-start gap-2 border-t border-white/[0.06] pt-4 text-[13px] leading-snug text-fg-2">
              <Clock size={14} className="mt-0.5 shrink-0 text-amber" />
              <span><span className="font-medium text-fg">{next.key === 'check_in' ? `${first} checks in` : next.title}</span> in {humanMinutes(((new Date(next.at).getTime() - now) * outage.speed) / 60_000)}. {next.key === 'check_in' ? 'You’ll be alerted if they miss it.' : next.detail}</span>
            </p>
          )}
        </section>
      )}

      <section className="mt-6">
        <h2 className="text-[12px] font-medium uppercase tracking-[0.14em] text-fg-3">Timeline</h2>
        <ol className="relative mt-3 space-y-4 border-l border-white/[0.08] pl-5">
          <AnimatePresence initial={false}>
            {events.map((e) => (
              <motion.li key={e.at + e.text} layout initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} className="relative">
                <span className="absolute -left-[27px] top-0.5 grid size-[14px] place-items-center rounded-full bg-ink-950 ring-1 ring-white/15"><span className={`size-1.5 rounded-full bg-current ${e.tone}`} /></span>
                <div className="flex items-center gap-2 text-[13.5px]"><e.icon size={14} className={e.tone} /> {e.text}</div>
                <div className="tnum mt-0.5 text-[12px] text-fg-4">{clock(e.at)}</div>
              </motion.li>
            ))}
          </AnimatePresence>
        </ol>
      </section>
      <p className="mt-6 flex items-center gap-2 text-[12px] text-fg-4"><PlugZap size={13} /> Updates live. Keep this page open.</p>
    </motion.section>
  )
}
