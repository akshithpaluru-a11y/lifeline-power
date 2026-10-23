import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AnimatePresence, motion } from 'motion/react'
import { ArrowLeft, ArrowRight, Check, LocateFixed, Share2 } from 'lucide-react'
import { createHome, useStore, type NewHome } from '../lib/store'
import { useCatalog } from '../lib/useCatalog'
import { primeAudio } from '../lib/alarm'
import type { BackupSource } from '../lib/types'
import { Logo } from '../components/Logo'
import { Field } from '../components/Field'
import { Button } from '../components/Button'
import { BackupIcon, DeviceIcon } from '../components/DeviceIcon'
import { Qr } from '../components/Qr'

const STEPS = ['Home', 'Devices', 'Backup', 'Family'] as const

export default function Setup() {
  const nav = useNavigate()
  const { devices: catalog, backups: backupCatalog } = useCatalog()
  const created = useStore((s) => s.household)
  const [step, setStep] = useState(0)
  const [first, setFirst] = useState('')
  const [address, setAddress] = useState('')
  const [state, setStateCode] = useState('')
  const [utility, setUtility] = useState('')
  const [loc, setLoc] = useState<{ lat: number; lon: number } | null>(null)
  const [kinds, setKinds] = useState<string[]>([])
  const [backups, setBackups] = useState<BackupSource['type'][]>([])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const toggle = <T,>(list: T[], v: T) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v])
  const canNext = [first.trim().length > 0, kinds.length > 0, true, true][step]

  const create = async () => {
    setBusy(true); setErr(null)
    const n: NewHome = {
      household: { name: `${first.trim()}’s home`, patient_first_name: first.trim(), address: address.trim() || null, state: state.trim().toUpperCase() || null, utility_name: utility.trim() || null, ...(loc ? { lat: loc.lat, lon: loc.lon } : {}) },
      devices: kinds.map((kind) => ({ kind })),
      backups: backups.map((type) => ({ type })),
    }
    try { await createHome(n); setStep(3) } catch (e) { setErr(e instanceof Error ? e.message : 'Could not save') } finally { setBusy(false) }
  }

  const next = () => (step === 2 ? create() : setStep(step + 1))
  const finish = async () => {
    primeAudio()
    if ('Notification' in window && Notification.permission === 'default') await Notification.requestPermission()
    nav('/app')
  }
  const joinUrl = created ? `${location.origin}/join?code=${created.join_code}` : ''

  return (
    <div className="min-h-dvh bg-ink-950">
      <div className="pointer-events-none fixed inset-x-0 top-0 h-[400px] bg-[radial-gradient(60%_100%_at_50%_0%,rgb(255_178_36/.09),transparent)]" />
      <div className="relative mx-auto flex min-h-dvh max-w-[440px] flex-col px-5 pb-6 pt-[max(1rem,env(safe-area-inset-top))]">
        <div className="flex items-center justify-between">
          <Logo size={26} />
          <span className="tnum text-[12px] text-fg-3">{step + 1} of {STEPS.length}</span>
        </div>
        <div className="mt-5 flex gap-1.5">
          {STEPS.map((s, i) => <span key={s} className={`h-1 flex-1 rounded-full transition-colors duration-500 ${i <= step ? 'bg-amber' : 'bg-white/10'}`} />)}
        </div>

        <AnimatePresence mode="wait">
          <motion.div key={step} className="mt-8 flex-1" initial={{ opacity: 0, x: 16 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -16 }} transition={{ duration: 0.35, ease: [0.16, 1, 0.3, 1] }}>
            {step === 0 && (
              <div className="space-y-4">
                <h1 className="text-[30px] font-semibold leading-[1.05] tracking-[-0.035em]">Who are we<br />keeping powered?</h1>
                <p className="text-[14px] text-fg-3">Their first name shows on alerts and the emergency card.</p>
                <Field label="Their first name" value={first} onChange={(e) => setFirst(e.target.value)} placeholder="Rosa" autoFocus />
                <Field label="Home address" value={address} onChange={(e) => setAddress(e.target.value)} placeholder="Optional, for responders" />
                <div className="grid grid-cols-[88px_1fr] gap-3">
                  <Field label="State" value={state} maxLength={2} onChange={(e) => setStateCode(e.target.value)} placeholder="DC" />
                  <Field label="Power company" value={utility} onChange={(e) => setUtility(e.target.value)} placeholder="Pepco" />
                </div>
                <button onClick={() => navigator.geolocation?.getCurrentPosition((p) => setLoc({ lat: p.coords.latitude, lon: p.coords.longitude }))}
                  className={`flex w-full items-center gap-3 rounded-2xl px-4 py-3 text-left text-[13.5px] ring-1 ring-inset transition ${loc ? 'bg-live/10 text-live ring-live/25' : 'bg-white/[0.04] text-fg-2 ring-white/[0.08]'}`}>
                  {loc ? <Check size={17} /> : <LocateFixed size={17} />}
                  {loc ? 'Location saved for finding nearby help' : 'Use this phone’s location to find nearby help'}
                </button>
              </div>
            )}
            {step === 1 && (
              <div>
                <h1 className="text-[30px] font-semibold leading-[1.05] tracking-[-0.035em]">What needs<br />power?</h1>
                <p className="mt-3 text-[14px] text-fg-3">Pick everything at home. You can enter exact batteries later.</p>
                <div className="mt-6 grid grid-cols-2 gap-2.5">
                  {catalog.filter((c) => c.kind !== 'custom').map((c) => {
                    const on = kinds.includes(c.kind)
                    return (
                      <button key={c.kind} onClick={() => setKinds(toggle(kinds, c.kind))}
                        className={`relative flex flex-col items-start gap-3 rounded-[22px] p-4 text-left ring-1 ring-inset transition active:scale-[.98] ${on ? 'bg-amber/[0.09] ring-amber/50' : 'surface'}`}>
                        <span className={`grid size-10 place-items-center rounded-xl ${on ? 'bg-amber text-ink-950' : 'bg-white/[0.06] text-fg-2'}`}><DeviceIcon icon={c.icon} size={19} /></span>
                        <span className="text-[13.5px] font-medium leading-snug">{c.name}</span>
                        {on && <span className="absolute right-3 top-3 grid size-5 place-items-center rounded-full bg-amber text-ink-950"><Check size={12} strokeWidth={3} /></span>}
                      </button>
                    )
                  })}
                </div>
              </div>
            )}
            {step === 2 && (
              <div>
                <h1 className="text-[30px] font-semibold leading-[1.05] tracking-[-0.035em]">Any backup<br />power?</h1>
                <p className="mt-3 text-[14px] text-fg-3">LifeLine shares it out so the most important device lasts longest. Skip if you have none.</p>
                <div className="mt-6 space-y-2.5">
                  {backupCatalog.map((b) => {
                    const on = backups.includes(b.type)
                    return (
                      <button key={b.type} onClick={() => setBackups(toggle(backups, b.type))}
                        className={`flex w-full items-center gap-4 rounded-[22px] p-4 text-left ring-1 ring-inset transition ${on ? 'bg-sky/[0.09] ring-sky/50' : 'surface'}`}>
                        <span className={`grid size-10 shrink-0 place-items-center rounded-xl ${on ? 'bg-sky text-ink-950' : 'bg-white/[0.06] text-fg-2'}`}><BackupIcon type={b.type} size={19} /></span>
                        <span className="min-w-0 flex-1">
                          <span className="block text-[14px] font-medium">{b.name}</span>
                          <span className="block text-[12px] text-fg-3">{b.description}</span>
                        </span>
                        {on && <Check size={18} className="shrink-0 text-sky" />}
                      </button>
                    )
                  })}
                </div>
                {err && <p className="mt-4 rounded-xl bg-alert/10 p-3 text-[13px] text-alert">{err}</p>}
              </div>
            )}
            {step === 3 && created && (
              <div className="flex flex-col items-center text-center">
                <h1 className="text-[30px] font-semibold leading-[1.05] tracking-[-0.035em]">Invite the family</h1>
                <p className="mt-3 max-w-[320px] text-[14px] leading-relaxed text-fg-3">They scan this or type the code at {location.host}/join. Then they’re alerted the second the power goes out.</p>
                <motion.div initial={{ scale: 0.9, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} transition={{ type: 'spring', damping: 16 }}
                  className="mt-7 rounded-[28px] bg-fg p-5 shadow-[0_30px_80px_-30px_rgb(255_178_36/.6)]">
                  <Qr value={joinUrl} size={180} />
                </motion.div>
                <div className="tnum mt-5 font-mono text-[34px] font-medium tracking-[0.16em] text-amber">{created.join_code}</div>
                <Button variant="secondary" className="mt-4" icon={<Share2 size={16} />}
                  onClick={() => navigator.share ? navigator.share({ title: 'LifeLine Power', text: `Join ${first}'s LifeLine. Code ${created.join_code}`, url: joinUrl }).catch(() => {}) : navigator.clipboard?.writeText(joinUrl)}>
                  Share invite
                </Button>
              </div>
            )}
          </motion.div>
        </AnimatePresence>

        <div className="mt-6 flex gap-2.5">
          {step > 0 && step < 3 && <Button variant="secondary" size="lg" onClick={() => setStep(step - 1)} icon={<ArrowLeft size={18} />} aria-label="Back" />}
          {step < 3
            ? <Button size="lg" className="flex-1" disabled={!canNext || busy} onClick={next}>{busy ? 'Saving…' : step === 2 ? (backups.length ? 'Save home' : 'Skip and save') : 'Continue'} {!busy && <ArrowRight size={18} />}</Button>
            : <Button size="lg" className="flex-1" onClick={finish}>Start watching</Button>}
        </div>
      </div>
    </div>
  )
}
