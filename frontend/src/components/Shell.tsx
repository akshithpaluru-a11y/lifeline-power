import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { House, LayoutGrid, MapPin, Users } from 'lucide-react'
import { useCallback, useEffect, useRef } from 'react'
import { Logo } from './Logo'
import { useStore, startOutage, updateHousehold, getState } from '../lib/store'
import { useChargerWatch, useWakeLock } from '../lib/power'
import { startAlarm, stopAlarm, notify } from '../lib/alarm'
import { humanMinutes } from '../lib/format'
import { Welcome } from '../pages/Welcome'

export async function raiseOutage(source: 'charging_lost' | 'manual' | 'drill', speed?: number) {
  const o = await startOutage(source, speed)
  startAlarm()
  const { household, plan } = getState()
  const d = household?.devices.find((x) => x.id === plan?.bottleneck_device_id)
  notify(source === 'drill' ? 'Drill: power is out' : 'Power is out at home',
    d && plan?.critical_runtime_minutes != null ? `${d.label}: about ${humanMinutes(plan.critical_runtime_minutes)} of battery. Caregivers are being alerted.` : 'Caregivers are being alerted.')
  return o
}

const TABS = [
  { to: '/app', label: 'Home', icon: House, end: true },
  { to: '/app/devices', label: 'Power', icon: LayoutGrid },
  { to: '/app/help', label: 'Help', icon: MapPin },
  { to: '/app/people', label: 'Family', icon: Users },
]

export function Shell() {
  const ready = useStore((s) => s.ready)
  const household = useStore((s) => s.household)
  const outage = useStore((s) => s.outage)
  const armed = household?.armed ?? false
  const nav = useNavigate()
  const loc = useLocation()

  const onLost = useCallback(async () => { await raiseOutage('charging_lost'); nav('/app/outage') }, [nav])
  const power = useChargerWatch(armed && !!household, onLost)
  useWakeLock(armed && !!household)

  // An active outage always owns the screen.
  const wasActive = useRef(false)
  useEffect(() => {
    const active = outage?.status === 'active'
    if (active && !['/app/outage', '/app/card', '/app/help'].includes(loc.pathname)) nav('/app/outage')
    if (!active && wasActive.current) stopAlarm()
    wasActive.current = active
  }, [outage, loc.pathname, nav])

  if (!ready) return <div className="min-h-dvh bg-ink-950" />
  if (!household) return <Welcome />

  const isOutage = loc.pathname === '/app/outage'

  return (
    <div className="min-h-dvh bg-ink-950">
      <div className="pointer-events-none fixed inset-x-0 top-0 h-[420px] bg-[radial-gradient(60%_100%_at_50%_0%,rgb(255_178_36/.07),transparent)]" />
      <div className="relative mx-auto flex min-h-dvh max-w-[440px] flex-col">
        {!isOutage && (
          <header className="no-print sticky top-0 z-30 flex items-center justify-between bg-ink-950/70 px-5 pb-3 pt-[max(1rem,env(safe-area-inset-top))] backdrop-blur-xl">
            <NavLink to="/" aria-label="LifeLine Power home"><Logo size={26} /></NavLink>
            <button
              onClick={() => updateHousehold({ armed: !armed })}
              className={`flex items-center gap-2 rounded-full py-1.5 pl-2 pr-3 text-[12px] font-medium ring-1 ring-inset transition ${armed ? 'bg-live/10 text-live ring-live/20' : 'bg-white/5 text-fg-3 ring-white/10'}`}
              aria-pressed={armed}
            >
              <span className="relative flex size-2">
                {armed && <span className="absolute inset-0 rounded-full bg-live" style={{ animation: 'ping-soft 2s var(--ease-out-expo) infinite' }} />}
                <span className={`relative size-2 rounded-full ${armed ? 'bg-live' : 'bg-fg-4'}`} />
              </span>
              {armed ? 'Watching' : 'Paused'}
            </button>
          </header>
        )}
        <main className="flex-1 px-5 pb-28">
          <Outlet context={power} />
        </main>
        {!isOutage && (
          <nav className="no-print fixed inset-x-0 bottom-0 z-30">
            <div className="mx-auto max-w-[440px] px-4 pb-[max(.75rem,env(safe-area-inset-bottom))]">
              <div className="flex rounded-[22px] bg-ink-850/80 p-1.5 shadow-[0_10px_40px_-10px_rgb(0_0_0/.8)] ring-1 ring-white/[0.08] backdrop-blur-xl">
                {TABS.map(({ to, label, icon: I, end }) => (
                  <NavLink key={to} to={to} end={end}
                    className={({ isActive }) => `flex flex-1 flex-col items-center gap-1 rounded-2xl py-2 text-[10.5px] font-medium transition ${isActive ? 'bg-white/[0.07] text-fg' : 'text-fg-3 hover:text-fg-2'}`}>
                    <I size={19} strokeWidth={1.8} />
                    {label}
                  </NavLink>
                ))}
              </div>
            </div>
          </nav>
        )}
      </div>
    </div>
  )
}
