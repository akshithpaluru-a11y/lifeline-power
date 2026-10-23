import { useEffect, useState } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import { MessageSquare, PlugZap, Unplug, Wind } from 'lucide-react'
import { Ring } from './Ring'
import { PulseTrace } from './PulseTrace'
import { Pill } from './Pill'
import { LogoMark } from './Logo'

// Looping product demo for the landing page: powered → plug pulled → outage + family alerted.
export function PhoneDemo() {
  const [phase, setPhase] = useState<0 | 1 | 2 | 3>(0)
  const [sec, setSec] = useState(0)

  useEffect(() => {
    const seq = [
      [0, 3000],
      [1, 1100],
      [2, 2600],
      [3, 5200],
    ] as const
    let i = 0
    let t: number
    const run = () => {
      setPhase(seq[i][0] as 0 | 1 | 2 | 3)
      t = window.setTimeout(() => { i = (i + 1) % seq.length; run() }, seq[i][1])
    }
    run()
    return () => clearTimeout(t)
  }, [])

  useEffect(() => {
    if (phase < 2) { setSec(0); return }
    const t = setInterval(() => setSec((s) => s + 1), 1000)
    return () => clearInterval(t)
  }, [phase])

  const total = 3 * 3600 + 25 * 60
  const left = total - sec
  const hh = Math.floor(left / 3600), mm = Math.floor((left % 3600) / 60), ss = left % 60
  const out = phase >= 2

  return (
    <div className="relative mx-auto w-[300px]">
      <div className={`absolute -inset-16 rounded-full blur-3xl transition-colors duration-1000 ${out ? 'bg-alert/20' : 'bg-live/10'}`} />
      <div className="relative rounded-[52px] bg-gradient-to-b from-[#2A2E30] to-[#151719] p-[10px] shadow-[0_50px_100px_-30px_rgb(0_0_0/.9),inset_0_0_0_1px_rgb(255_255_255/.08)]">
        <div className="relative h-[610px] overflow-hidden rounded-[43px] bg-ink-950">
          <div className="absolute left-1/2 top-2.5 z-20 h-[26px] w-[90px] -translate-x-1/2 rounded-full bg-black" />
          <div className="flex items-center justify-between px-7 pt-3.5 text-[11px] font-semibold">
            <span className="tnum">9:41</span>
            <span className="flex items-center gap-1 text-fg-2">{out ? <Unplug size={12} /> : <PlugZap size={12} className="text-live" />}</span>
          </div>

          <AnimatePresence>
            {phase === 3 && (
              <motion.div className="absolute inset-x-3 top-11 z-30 flex items-center gap-2.5 rounded-[20px] bg-ink-700/90 p-3 ring-1 ring-white/10 backdrop-blur-xl"
                initial={{ y: -80, opacity: 0 }} animate={{ y: 0, opacity: 1 }} exit={{ y: -80, opacity: 0 }} transition={{ type: 'spring', damping: 20, stiffness: 260, delay: 0.6 }}>
                <span className="grid size-8 place-items-center rounded-lg bg-live text-ink-950"><MessageSquare size={15} /></span>
                <span className="text-[11.5px] leading-tight"><span className="font-semibold">Maya is on the way</span> <span className="text-fg-3">now</span><br />ETA 12 min</span>
              </motion.div>
            )}
          </AnimatePresence>

          <AnimatePresence mode="wait">
            {!out ? (
              <motion.div key="on" className="px-5 pt-8" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0, scale: 0.98 }}>
                <div className="flex items-center gap-2"><LogoMark size={22} /><Pill tone="live" pulse>Watching</Pill></div>
                <div className="mt-10 text-[11px] font-medium uppercase tracking-[0.14em] text-fg-3">Rosa’s home</div>
                <div className="mt-2 text-[30px] font-semibold leading-[1.02] tracking-[-0.035em]">Power is on.<br /><span className="text-fg-3">We’re watching.</span></div>
                <div className="-mx-5 mt-6"><PulseTrace tone="live" height={70} /></div>
                <div className="surface mt-6 space-y-3 rounded-[20px] p-4">
                  {[['Oxygen', '3h 25m', 0.62], ['CPAP', '2h 43m', 0.5], ['Feeding pump', '5h 58m', 1]].map(([n, t, v]) => (
                    <div key={n as string}>
                      <div className="flex justify-between text-[12px]"><span>{n}</span><span className="tnum font-medium text-fg-2">{t}</span></div>
                      <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-white/[0.07]"><div className="h-full rounded-full bg-fg-3" style={{ width: `${(v as number) * 100}%` }} /></div>
                    </div>
                  ))}
                </div>
                <div className="mt-3 flex items-center justify-between rounded-[16px] bg-white/[0.03] px-4 py-3 text-[11.5px] text-fg-3 ring-1 ring-inset ring-white/[0.05]">
                  <span>3 caregivers connected</span>
                  <span className="flex -space-x-1.5">
                    {['M', 'L', 'S'].map((c, i) => <span key={c} className={`grid size-5 place-items-center rounded-full text-[9px] font-semibold text-ink-950 ring-2 ring-ink-950 ${['bg-amber', 'bg-sky', 'bg-live'][i]}`}>{c}</span>)}
                  </span>
                </div>
              </motion.div>
            ) : (
              <motion.div key="out" className="relative px-5 pt-8" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
                <motion.div className="pointer-events-none absolute inset-x-0 -top-20 h-[420px] bg-[radial-gradient(50%_55%_at_50%_45%,rgb(255_120_60/.35),transparent)]"
                  animate={{ opacity: [0.6, 1, 0.6] }} transition={{ duration: 2.4, repeat: Infinity }} />
                <motion.div className="pointer-events-none absolute inset-0 bg-alert" initial={{ opacity: 0.5 }} animate={{ opacity: 0 }} transition={{ duration: 0.8 }} />
                <div className="relative flex items-center gap-2"><LogoMark size={22} /><Pill tone="alert" pulse>POWER OUT</Pill></div>
                <div className="relative mt-6 flex justify-center">
                  <Ring value={left / (total / 0.62)} size={240}>
                    <span className="flex items-center gap-1 text-[10.5px] font-medium uppercase tracking-[0.14em] text-fg-2"><Wind size={12} /> Oxygen left</span>
                    <span className="tnum mt-1.5 font-mono text-[44px] font-medium leading-none tracking-[-0.04em]">
                      {hh}<span className="text-fg-3">:</span>{String(mm).padStart(2, '0')}<span className="text-[22px] text-fg-3">:{String(ss).padStart(2, '0')}</span>
                    </span>
                    <span className="mt-2 text-[11px] text-fg-3">Empty around <span className="text-fg">1:06 AM</span></span>
                  </Ring>
                </div>
                <div className="surface relative mt-5 space-y-2.5 rounded-[20px] p-3.5">
                  {['Maya · Daughter', 'Leo · Grandson', 'Sam · Neighbor'].map((n, i) => (
                    <motion.div key={n} className="flex items-center justify-between text-[11.5px]" initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: 0.4 + i * 0.3 }}>
                      <span>{n}</span>
                      {phase === 3 && i === 0 ? <Pill tone="live" className="!text-[10px] !py-0.5">On the way</Pill> : <Pill tone="sky" className="!text-[10px] !py-0.5">Alerted</Pill>}
                    </motion.div>
                  ))}
                </div>
                <div className="relative mt-3 flex items-center gap-2.5 rounded-[16px] bg-white/[0.03] px-3.5 py-2.5 ring-1 ring-inset ring-white/[0.05]">
                  <span className="grid size-7 place-items-center rounded-lg bg-live/15 text-live"><PlugZap size={13} /></span>
                  <span className="text-[11px] leading-tight"><span className="font-medium">Northeast Library</span><br /><span className="text-fg-3">Charging site · 1.2 km</span></span>
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>
      <AnimatePresence>
        {phase === 1 && (
          <motion.div className="absolute -right-6 top-[46%] rounded-full bg-fg px-3 py-1.5 text-[12px] font-semibold text-ink-950 shadow-xl"
            initial={{ opacity: 0, x: 10 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0 }}>
            Outlet lost power
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
