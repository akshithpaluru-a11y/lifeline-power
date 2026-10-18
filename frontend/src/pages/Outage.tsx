import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useOutletContext } from 'react-router-dom'
import { AnimatePresence, motion } from 'motion/react'
import { Car, Check, Copy, MapPin, Navigation, Phone, PlugZap, ThumbsUp, VolumeX, TriangleAlert, Plug } from 'lucide-react'
import { useStore, checkIn, endOutage, getState } from '../lib/store'
import { api } from '../lib/api'
import { stopAlarm } from '../lib/alarm'
import { useNow } from '../lib/useNow'
import { clock, hms, humanMinutes } from '../lib/format'
import { criticalLeft, deadline, planMinutesLeft } from '../lib/time'
import { Ring } from '../components/Ring'
import { Pill } from '../components/Pill'
import { Segments } from '../components/Segments'
import { DeviceIcon } from '../components/DeviceIcon'
import { Button } from '../components/Button'
import { LogoMark } from '../components/Logo'
import { PlugSteps } from '../components/PlugSteps'
import type { useChargerWatch } from '../lib/power'
import type { HelpSite } from '../lib/types'

export default function Outage() {
  const h = useStore((s) => s.household)!
  const outage = useStore((s) => s.outage)
  const missed = useStore((s) => s.missed)
  const power = useOutletContext<ReturnType<typeof useChargerWatch>>()
  const now = useNow(outage && outage.speed > 1 ? 250 : 1000)
  const nav = useNavigate()
  const [muted, setMuted] = useState(false)
  const [seenReplug, setSeenReplug] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [site, setSite] = useState<HelpSite | null>(null)

  useEffect(() => { if (!outage || outage.status !== 'active') nav('/app', { replace: true }) }, [outage, nav])
  useEffect(() => {
    const s = getState().session
    if (s) api.helpSites(s.household_id, s.token).then((r) => setSite(r.sites.find((x) => x.likely_has_power) ?? r.sites[0] ?? null)).catch(() => {})
  }, [])

  const rows = useMemo(() => {
    if (!outage) return []
    return outage.plan.devices
      .map((p) => ({ p, d: h.devices.find((x) => x.id === p.device_id) }))
      .filter((r): r is { p: typeof r.p; d: NonNullable<typeof r.d> } => !!r.d)
      .sort((a, b) => a.p.total_minutes - b.p.total_minutes)
  }, [outage, h.devices])

  if (!outage) return null

  const drill = outage.drill ?? outage.source === 'drill'
  const crit = outage.plan.critical_runtime_minutes ?? Math.max(0, ...rows.map((r) => r.p.total_minutes))
  const primary = rows.find((r) => r.d.id === outage.plan.bottleneck_device_id) ?? rows.find((r) => r.p.total_minutes > 0) ?? rows[0]
  const left = criticalLeft(outage, now)
  const frac = crit > 0 ? Math.min(1, left / crit) : 0
  const t = hms(left * 60_000)
  const elapsed = hms((now - new Date(outage.started_at).getTime()) * outage.speed)
  const urgent = left < 60
  const emptyAt = outage.milestones.find((m) => m.key === 'depleted')?.at
  const upcoming = outage.milestones.filter((m) => new Date(m.at).getTime() > now && m.key !== 'depleted' && (!emptyAt || m.at <= emptyAt)).slice(0, 3)
  const leaveBy = outage.milestones.find((m) => m.key === 'leave_by')
  const replug = outage.milestones.find((m) => m.key === 'replug' && m.passed && m.at !== seenReplug)
  const plugRows = rows.filter((r) => r.p.plug_steps?.length)
  const noTime = leaveBy?.severity === 'critical' ? leaveBy
    : outage.plan.critical_runtime_minutes === 0 && primary ? { title: `${primary.d.label} has no power`, detail: 'It has no battery or backup that can run it. Get to a place with power now, or call 911.' } : null
  const shareUrl = `${location.origin}/join?code=${h.join_code}`

  const silence = () => { stopAlarm(); setMuted(true) }
  const end = async () => { stopAlarm(); await endOutage(); nav('/app') }

  return (
    <div className="relative -mx-5 min-h-dvh overflow-hidden px-5 pb-44 pt-[max(1rem,env(safe-area-inset-top))]">
      <motion.div
        className={`pointer-events-none absolute inset-x-0 -top-40 h-[560px] ${urgent ? 'bg-[radial-gradient(50%_60%_at_50%_40%,rgb(255_90_78/.30),transparent)]' : 'bg-[radial-gradient(50%_60%_at_50%_40%,rgb(255_160_40/.22),transparent)]'}`}
        animate={{ opacity: [0.65, 1, 0.65] }} transition={{ duration: 3.2, repeat: Infinity, ease: 'easeInOut' }}
      />

      <div className="relative flex items-center justify-between">
        <div className="flex items-center gap-2">
          <LogoMark size={26} />
          <Pill tone="alert" pulse>POWER OUT</Pill>
          {drill && <Pill tone="sky">DRILL ×{outage.speed}</Pill>}
        </div>
        <button onClick={silence} className="flex items-center gap-1.5 rounded-full bg-white/5 px-3 py-1.5 text-[12px] text-fg-2 ring-1 ring-white/10" aria-label="Silence alarm">
          <VolumeX size={14} /> {muted ? 'Silenced' : 'Silence'}
        </button>
      </div>
      <p className="relative mt-3 text-[13px] text-fg-3">
        Out since {clock(outage.started_at)} · <span className="tnum font-medium text-fg-2">{elapsed.text}</span> ·{' '}
        {outage.source === 'charging_lost' ? 'the charger lost power' : drill ? 'practice run' : 'started by hand'}
      </p>

      <AnimatePresence>
        {outage.source === 'charging_lost' && power?.charging && (
          <motion.button key="back" onClick={end} initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
            className="relative mt-3 flex w-full items-center gap-3 rounded-2xl bg-live/10 p-3 text-left ring-1 ring-inset ring-live/25">
            <PlugZap size={18} className="shrink-0 text-live" />
            <span className="text-[13.5px]">The charger has power again. <span className="text-live">Tap if the power’s back.</span></span>
          </motion.button>
        )}
        {missed != null && (
          <motion.div key="missed" initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
            className="relative mt-3 flex items-center gap-3 rounded-2xl bg-alert/10 p-3 ring-1 ring-inset ring-alert/25">
            <TriangleAlert size={18} className="shrink-0 text-alert" />
            <span className="text-[13.5px]">Check-in is {Math.round(missed)} min late. Your family has been told. Tap <span className="text-fg">I’m OK</span>.</span>
          </motion.div>
        )}
      </AnimatePresence>

      {noTime && (
        <div className="relative mt-3 rounded-2xl bg-alert/15 p-4 ring-1 ring-inset ring-alert/40">
          <div className="flex items-center gap-2 text-[15px] font-semibold text-alert"><TriangleAlert size={18} className="shrink-0" /> {noTime.title}</div>
          <p className="mt-1.5 text-[13px] leading-relaxed text-fg-2">{noTime.detail}</p>
          <a href="tel:911" className="mt-3 flex items-center justify-center gap-2 rounded-xl bg-alert py-2.5 text-[14px] font-semibold text-ink-950"><Phone size={15} /> Call 911</a>
        </div>
      )}

      {replug && (
        <div className="relative mt-3 rounded-2xl bg-amber/10 p-4 ring-1 ring-inset ring-amber/30">
          <div className="flex items-center gap-2 text-[15px] font-semibold text-amber"><Plug size={17} className="shrink-0" /> {replug.title}</div>
          <p className="mt-1.5 text-[13px] leading-relaxed text-fg-2">{replug.detail}</p>
          <button onClick={() => setSeenReplug(replug.at)} className="mt-3 rounded-xl bg-amber px-4 py-2 text-[13px] font-semibold text-ink-950">Done</button>
        </div>
      )}

      {primary && (
        <motion.div className="relative mt-4 flex justify-center" initial={{ scale: 0.92, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} transition={{ duration: 0.9, ease: [0.16, 1, 0.3, 1] }}>
          <Ring value={frac} size={312} tone={urgent ? 'alert' : 'amber'}>
            <span className="flex max-w-[200px] items-center gap-1.5 truncate text-[12px] font-medium uppercase tracking-[0.14em] text-fg-2">
              <DeviceIcon icon={primary.d.icon} size={14} /> <span className="truncate">{primary.d.label}</span>
            </span>
            <span className="tnum mt-2 font-mono text-[58px] font-medium leading-none tracking-[-0.04em]">
              {t.h}<span className="text-fg-3">:</span>{String(t.m).padStart(2, '0')}
              <span className="text-[30px] text-fg-3">:{String(t.s).padStart(2, '0')}</span>
            </span>
            <span className="mt-3 text-[13px] text-fg-3">
              {drill ? <>of life support left</> : <>Battery empty around <span className="text-fg">{clock(new Date(outage.milestones.find((m) => m.key === 'depleted')?.at ?? deadline(outage, crit)))}</span></>}
            </span>
          </Ring>
        </motion.div>
      )}

      <section className="relative mt-6 space-y-2">
        {rows.filter((r) => r !== primary).map(({ p, d }) => {
          const l = planMinutesLeft(outage, p.total_minutes, now)
          return (
            <div key={d.id} className="surface flex items-center gap-3 rounded-2xl px-4 py-3">
              <DeviceIcon icon={d.icon} className="shrink-0 text-fg-2" />
              <span className="min-w-0 flex-1 truncate text-[13.5px]">{d.label}</span>
              <Segments value={p.total_minutes > 0 ? l / p.total_minutes : 0} count={8} tone={p.total_minutes <= 0 ? 'alert' : 'muted'} className="h-1.5 w-14 shrink-0" />
              <span className={`tnum w-[68px] shrink-0 text-right text-[13px] font-medium ${p.total_minutes <= 0 ? 'text-alert' : 'text-fg-2'}`}>{p.total_minutes <= 0 ? 'No power' : humanMinutes(l)}</span>
            </div>
          )
        })}
      </section>

      {plugRows.length > 0 && (
        <section className="surface relative mt-4 rounded-[24px] p-4">
          <h2 className="text-[14px] font-semibold tracking-tight">Where to plug in</h2>
          <ul className="mt-3 space-y-3">
            {plugRows.map(({ p, d }) => (
              <li key={d.id}>
                <div className="flex items-center gap-2 text-[13px] font-medium"><DeviceIcon icon={d.icon} size={14} className="text-fg-3" /> {d.label}</div>
                <PlugSteps steps={p.plug_steps!} className="mt-1.5 pl-[22px]" />
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="surface relative mt-4 rounded-[24px] p-4">
        <div className="flex items-center justify-between">
          <h2 className="text-[14px] font-semibold tracking-tight">Family alerted</h2>
          <button onClick={() => { navigator.clipboard?.writeText(shareUrl); setCopied(true); setTimeout(() => setCopied(false), 1600) }}
            className="flex items-center gap-1.5 text-[12px] text-fg-3 hover:text-fg">{copied ? <Check size={13} /> : <Copy size={13} />} {copied ? 'Copied' : 'Invite link'}</button>
        </div>
        {h.caregivers.length === 0 && <p className="mt-2 text-[13px] text-fg-3">No one has joined yet. Share the invite link so someone gets these alerts.</p>}
        <ul className="mt-3 space-y-2.5">
          {h.caregivers.map((c, i) => {
            const a = outage.acks.find((x) => x.caregiver_id === c.id)
            return (
              <motion.li key={c.id} className="flex items-center gap-3" initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: 0.4 + i * 0.2 }}>
                <span className="grid size-8 shrink-0 place-items-center rounded-full bg-gradient-to-b from-ink-600 to-ink-700 text-[11.5px] font-semibold ring-1 ring-white/10">{c.name.slice(0, 1)}</span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[13.5px]">{c.name} <span className="text-fg-3">· {c.relationship}</span></span>
                  {a?.message && <span className="block truncate text-[12px] text-fg-3">“{a.message}”</span>}
                </span>
                <AnimatePresence mode="wait">
                  {a ? (
                    <motion.span key="ack" initial={{ scale: 0.8, opacity: 0 }} animate={{ scale: 1, opacity: 1 }}>
                      <Pill tone="live"><Car size={12} /> {a.eta_minutes ? `${a.eta_minutes} min away` : 'On the way'}</Pill>
                    </motion.span>
                  ) : (
                    <motion.span key="sent" initial={{ opacity: 0 }} animate={{ opacity: 1 }}><Pill tone="sky"><Check size={12} /> Alerted</Pill></motion.span>
                  )}
                </AnimatePresence>
              </motion.li>
            )
          })}
        </ul>
      </section>

      {upcoming.length > 0 && (
        <section className="surface relative mt-4 rounded-[24px] p-4">
          <h2 className="text-[14px] font-semibold tracking-tight">What’s next</h2>
          <ol className="mt-3 space-y-3">
            {upcoming.map((m) => {
              const inMin = ((new Date(m.at).getTime() - now) * outage.speed) / 60_000
              return (
                <li key={m.key} className="flex gap-3">
                  <span className={`mt-1.5 size-2 shrink-0 rounded-full ${m.severity === 'critical' ? 'bg-alert' : m.severity === 'warning' ? 'bg-amber' : 'bg-sky'}`} />
                  <span className="min-w-0 flex-1">
                    <span className="flex items-baseline justify-between gap-3">
                      <span className="text-[13.5px] font-medium">{m.title}</span>
                      <span className="tnum shrink-0 text-[12px] text-fg-3">in {humanMinutes(inMin)}</span>
                    </span>
                    <span className="block text-[12.5px] leading-snug text-fg-3">{m.detail}</span>
                  </span>
                </li>
              )
            })}
          </ol>
        </section>
      )}

      <section className="relative mt-4 space-y-2.5">
        {site && (
          <a href={`https://www.google.com/maps/dir/?api=1&destination=${site.lat},${site.lon}`} target="_blank" rel="noreferrer"
            className="surface flex items-center gap-3 rounded-[20px] p-3.5 hover:bg-white/[0.03]">
            <span className="grid size-10 place-items-center rounded-xl bg-sky/15 text-sky"><MapPin size={18} /></span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[13.5px] font-medium">{site.name}</span>
              <span className="block text-[12px] text-fg-3">{site.travel_minutes} min away{site.likely_has_power ? ' · has backup power' : ''}{leaveBy && !noTime && !drill ? ` · leave by ${clock(leaveBy.at)}` : ''}</span>
            </span>
            <Navigation size={16} className="text-fg-3" />
          </a>
        )}
      </section>

      <div className="fixed inset-x-0 bottom-0 z-40">
        <div className="mx-auto max-w-[440px] bg-gradient-to-t from-ink-950 via-ink-950/95 to-transparent px-4 pb-[max(.75rem,env(safe-area-inset-bottom))] pt-6">
          {outage.last_check_in_at && <p className="mb-2 text-center text-[12px] text-fg-3">Last check-in {clock(outage.last_check_in_at)}</p>}
          <div className="grid grid-cols-[1fr_auto] gap-2.5">
            <Button variant="light" size="lg" icon={<ThumbsUp size={18} />} onClick={async () => { silence(); await checkIn() }}>I’m OK</Button>
            <a href="tel:911" className="flex h-14 items-center gap-2 rounded-[18px] bg-gradient-to-b from-[#ff6f64] to-alert px-5 text-[16px] font-medium text-white shadow-[inset_0_1px_0_rgb(255_255_255/.35),0_8px_24px_-8px_rgb(255_90_78/.7)]">
              <Phone size={18} /> 911
            </a>
          </div>
          <button onClick={end} className="mt-2 flex w-full items-center justify-center gap-1.5 py-2 text-[12.5px] text-fg-3 hover:text-fg">
            <PlugZap size={14} /> {drill ? 'End drill' : 'Power’s back'}
          </button>
        </div>
      </div>
    </div>
  )
}
