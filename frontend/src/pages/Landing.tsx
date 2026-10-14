import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { createDemoHome, getState } from '../lib/store'
import { motion } from 'motion/react'
import { ArrowRight, ArrowUpRight, BellRing, IdCard, MapPin, Radio, Timer, Users, Globe, Plug, Smartphone, MessageSquare } from 'lucide-react'
import { Logo } from '../components/Logo'
import { PhoneDemo } from '../components/PhoneDemo'

const up = (d = 0) => ({ initial: { opacity: 0, y: 18 }, whileInView: { opacity: 1, y: 0 }, viewport: { once: true, margin: '-60px' }, transition: { duration: 0.8, delay: d, ease: [0.16, 1, 0.3, 1] as const } })

export default function Landing() {
  const nav = useNavigate()
  const [busy, setBusy] = useState(false)
  const demo = async () => {
    setBusy(true)
    try { if (!getState().household) await createDemoHome(); nav('/app') } finally { setBusy(false) }
  }
  return (
    <div className="min-h-dvh overflow-x-hidden bg-ink-950">
      <div className="pointer-events-none absolute inset-x-0 top-0 h-[900px] bg-[radial-gradient(50%_60%_at_70%_20%,rgb(255_178_36/.10),transparent),radial-gradient(40%_40%_at_10%_10%,rgb(124_183_255/.06),transparent)]" />
      <div className="pointer-events-none absolute inset-x-0 top-0 h-[900px] opacity-[0.35] [background-image:linear-gradient(rgb(255_255_255/.04)_1px,transparent_1px),linear-gradient(90deg,rgb(255_255_255/.04)_1px,transparent_1px)] [background-size:56px_56px] [mask-image:radial-gradient(70%_60%_at_50%_0%,black,transparent)]" />

      <header className="relative z-10 mx-auto flex max-w-6xl items-center justify-between px-5 py-5 sm:px-8">
        <Logo />
        <nav className="hidden items-center gap-8 text-[13.5px] text-fg-3 md:flex">
          <a href="#how" className="hover:text-fg">How it works</a>
          <a href="#features" className="hover:text-fg">Features</a>
          <a href="#why" className="hover:text-fg">Why it matters</a>
        </nav>
        <Link to="/app" className="rounded-full bg-fg px-4 py-2 text-[13px] font-medium text-ink-950 hover:bg-white">Open app</Link>
      </header>

      <section className="relative mx-auto grid max-w-6xl items-center gap-14 px-5 pb-24 pt-10 sm:px-8 lg:grid-cols-[1.35fr_1fr] lg:pt-16">
        <div>
          <motion.span {...up()} className="inline-flex items-center gap-2 rounded-full bg-white/[0.04] py-1 pl-1 pr-3 text-[12.5px] text-fg-2 ring-1 ring-inset ring-white/10">
            <span className="rounded-full bg-amber px-2 py-0.5 text-[11px] font-semibold text-ink-950">New</span>
            For families on oxygen, ventilators and CPAP
          </motion.span>
          <motion.h1 {...up(0.05)} className="mt-6 text-[44px] font-semibold leading-[0.98] tracking-[-0.045em] sm:text-[64px] lg:text-[68px]">
            The power’s out.<br />
            <span className="bg-gradient-to-r from-amber-hi via-amber to-amber-lo bg-clip-text text-transparent">Every minute counts.</span>
          </motion.h1>
          <motion.p {...up(0.1)} className="mt-6 max-w-[520px] text-[17px] leading-relaxed text-fg-2">
            LifeLine Power turns the phone on the charger into an outage alarm. The second the outlet goes dead, it counts down the battery on every medical device in the house and alerts the people who can help.
          </motion.p>
          <motion.div {...up(0.15)} className="mt-9 flex flex-wrap gap-3">
            <Link to="/setup" className="group inline-flex h-12 items-center gap-2 rounded-2xl bg-gradient-to-b from-amber-hi to-amber px-5 text-[15px] font-medium text-ink-950 shadow-[inset_0_1px_0_rgb(255_255_255/.5),0_12px_32px_-10px_rgb(255_178_36/.7)]">
              Set it up in 2 minutes <ArrowRight size={17} className="transition group-hover:translate-x-0.5" />
            </Link>
            <button onClick={demo} disabled={busy} className="inline-flex h-12 items-center gap-2 rounded-2xl bg-white/[0.05] px-5 text-[15px] font-medium ring-1 ring-inset ring-white/10 hover:bg-white/[0.08] disabled:opacity-60">
              {busy ? 'Building sample home…' : 'Try a sample home'}
            </button>
          </motion.div>
          <motion.div {...up(0.2)} className="mt-10 flex items-center gap-6 text-[12.5px] text-fg-3">
            <span className="flex items-center gap-2"><Smartphone size={15} /> No extra hardware</span>
            <span className="flex items-center gap-2"><MessageSquare size={15} /> Family needs no app</span>
            <span className="hidden items-center gap-2 sm:flex"><Globe size={15} /> Runs in any browser</span>
          </motion.div>
        </div>
        <motion.div initial={{ opacity: 0, y: 40, rotate: 2 }} animate={{ opacity: 1, y: 0, rotate: 0 }} transition={{ duration: 1.2, ease: [0.16, 1, 0.3, 1], delay: 0.2 }}>
          <PhoneDemo />
        </motion.div>
      </section>

      <section id="why" className="relative border-y border-white/[0.06] bg-ink-900/50">
        <div className="mx-auto grid max-w-6xl gap-px px-5 sm:px-8 md:grid-cols-3">
          {[
            ['3M+', 'Medicare patients rely on electricity-dependent medical equipment at home.', 'HHS emPOWER'],
            ['1', 'Phone on a charger. That’s the whole sensor. No smart plug, no hub.', 'How LifeLine works'],
            ['0', 'Apps a caregiver has to install. They join with a family code in any browser.', 'Works on any phone'],
          ].map(([n, t, s], i) => (
            <motion.div key={n} {...up(i * 0.08)} className="py-10 md:px-8 md:first:pl-0">
              <div className="tnum font-mono text-[56px] font-medium leading-none tracking-[-0.05em]">{n}</div>
              <p className="mt-4 max-w-[300px] text-[14.5px] leading-relaxed text-fg-2">{t}</p>
              <p className="mt-3 text-[11.5px] uppercase tracking-[0.12em] text-fg-4">{s}</p>
            </motion.div>
          ))}
        </div>
      </section>

      <section id="how" className="mx-auto max-w-6xl px-5 py-28 sm:px-8">
        <motion.p {...up()} className="text-[12.5px] font-medium uppercase tracking-[0.16em] text-amber">How it works</motion.p>
        <motion.h2 {...up(0.05)} className="mt-3 max-w-[640px] text-[36px] font-semibold leading-[1.05] tracking-[-0.035em] sm:text-[46px]">From dark outlet to help on the way, in seconds.</motion.h2>
        <div className="mt-14 grid gap-4 md:grid-cols-3">
          {[
            { icon: Plug, n: '01', t: 'Plug in and leave it', d: 'Add the devices at home and who to call. Leave the phone charging near the bed with LifeLine open.' },
            { icon: Radio, n: '02', t: 'The outlet goes dead', d: 'The charger stops. LifeLine notices instantly, sounds an alarm and starts a countdown for every device.' },
            { icon: Users, n: '03', t: 'Family sees it live', d: 'Caregivers get an alert and a live view: battery left, check-ins, and who’s already on the way.' },
          ].map((s, i) => (
            <motion.div key={s.n} {...up(i * 0.08)} className="surface group relative overflow-hidden rounded-[28px] p-7">
              <div className="flex items-center justify-between">
                <span className="grid size-11 place-items-center rounded-2xl bg-white/[0.05] ring-1 ring-inset ring-white/10 text-amber"><s.icon size={20} strokeWidth={1.75} /></span>
                <span className="tnum font-medium text-[13px] text-fg-4">{s.n}</span>
              </div>
              <h3 className="mt-10 text-[20px] font-semibold tracking-[-0.02em]">{s.t}</h3>
              <p className="mt-2 text-[14.5px] leading-relaxed text-fg-3">{s.d}</p>
            </motion.div>
          ))}
        </div>
      </section>

      <section id="features" className="mx-auto max-w-6xl px-5 pb-28 sm:px-8">
        <div className="grid gap-4 md:grid-cols-6">
          <motion.div {...up()} className="surface relative overflow-hidden rounded-[28px] p-7 md:col-span-4">
            <Timer className="text-amber" size={22} strokeWidth={1.75} />
            <h3 className="mt-6 text-[22px] font-semibold tracking-[-0.02em]">A clock for every device</h3>
            <p className="mt-2 max-w-[420px] text-[14.5px] leading-relaxed text-fg-3">Runtime is worked out from each battery’s size, charge and power draw, so you know which device runs out first and when.</p>
            <div className="mt-8 space-y-3">
              {[['Oxygen concentrator', '3:25:10', 0.62, 'bg-amber'], ['CPAP', '2:43:12', 0.5, 'bg-fg-3'], ['Feeding pump', '5:58:41', 1, 'bg-fg-3']].map(([n, t, v, c]) => (
                <div key={n as string} className="flex items-center gap-4">
                  <span className="w-40 shrink-0 text-[13.5px] text-fg-2">{n}</span>
                  <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-white/[0.06]"><motion.div className={`h-full rounded-full ${c}`} initial={{ width: 0 }} whileInView={{ width: `${(v as number) * 100}%` }} viewport={{ once: true }} transition={{ duration: 1.4, ease: [0.16, 1, 0.3, 1] }} /></div>
                  <span className="tnum w-20 text-right font-mono text-[13.5px]">{t}</span>
                </div>
              ))}
            </div>
          </motion.div>
          <motion.div {...up(0.06)} className="surface relative overflow-hidden rounded-[28px] p-7 md:col-span-2">
            <BellRing className="text-alert" size={22} strokeWidth={1.75} />
            <h3 className="mt-6 text-[22px] font-semibold tracking-[-0.02em]">Loud when it matters</h3>
            <p className="mt-2 text-[14.5px] leading-relaxed text-fg-3">A two-tone alarm, vibration and a full-screen alert, even at 3 AM.</p>
            <div className="mt-8 flex h-16 items-end gap-[5px]">
              {Array.from({ length: 22 }, (_, i) => (
                <motion.span key={i} className="flex-1 rounded-full bg-gradient-to-t from-alert/30 to-alert"
                  animate={{ height: ['18%', `${30 + ((i * 37) % 70)}%`, '18%'] }}
                  transition={{ duration: 1.1, repeat: Infinity, delay: i * 0.05, ease: 'easeInOut' }} />
              ))}
            </div>
          </motion.div>
          <motion.div {...up()} className="surface rounded-[28px] p-7 md:col-span-2">
            <MapPin className="text-sky" size={22} strokeWidth={1.75} />
            <h3 className="mt-6 text-[22px] font-semibold tracking-[-0.02em]">Where to go</h3>
            <p className="mt-2 text-[14.5px] leading-relaxed text-fg-3">The nearest charging site, cooling center and hospital, with directions in one tap.</p>
          </motion.div>
          <motion.div {...up(0.06)} className="surface rounded-[28px] p-7 md:col-span-2">
            <IdCard className="text-live" size={22} strokeWidth={1.75} />
            <h3 className="mt-6 text-[22px] font-semibold tracking-[-0.02em]">Card for responders</h3>
            <p className="mt-2 text-[14.5px] leading-relaxed text-fg-3">A printable card with every device, its runtime and who to call.</p>
          </motion.div>
          <motion.div {...up(0.12)} className="surface rounded-[28px] p-7 md:col-span-2">
            <Users className="text-amber" size={22} strokeWidth={1.75} />
            <h3 className="mt-6 text-[22px] font-semibold tracking-[-0.02em]">One live link</h3>
            <p className="mt-2 text-[14.5px] leading-relaxed text-fg-3">Caregivers follow the countdown and check-ins from any phone. Nothing to install.</p>
          </motion.div>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-5 pb-24 sm:px-8">
        <motion.div {...up()} className="grain relative overflow-hidden rounded-[36px] bg-gradient-to-br from-amber-hi via-amber to-amber-lo p-10 text-ink-950 sm:p-14">
          <svg viewBox="0 0 64 64" className="pointer-events-none absolute -right-10 -top-6 hidden h-[420px] w-[420px] text-ink-950/[0.08] md:block" aria-hidden>
            <path d="M9 33h11l3.2-6 3.3 6" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
            <path d="M41 33h14" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" />
            <path d="M36.5 13 27 34.5h7.5L31 51l11.5-22.5H35L38.5 13z" fill="currentColor" />
          </svg>
          <h2 className="max-w-[560px] text-[34px] font-semibold leading-[1.02] tracking-[-0.04em] sm:text-[48px]">Storm season doesn’t wait. Neither should the plan.</h2>
          <Link to="/setup" className="mt-8 inline-flex h-12 items-center gap-2 rounded-2xl bg-ink-950 px-5 text-[15px] font-medium text-fg hover:bg-ink-900">
            Build your outage plan <ArrowUpRight size={17} />
          </Link>
        </motion.div>
      </section>

      <footer className="mx-auto flex max-w-6xl flex-col gap-3 border-t border-white/[0.06] px-5 py-8 text-[12.5px] text-fg-4 sm:flex-row sm:items-center sm:justify-between sm:px-8">
        <Logo size={22} />
        <span>Built for the Congressional App Challenge 2026. Not a substitute for 911.</span>
      </footer>
    </div>
  )
}
