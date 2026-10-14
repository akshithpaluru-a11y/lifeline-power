import { useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { motion } from 'motion/react'
import { ArrowRight } from 'lucide-react'
import { api } from '../lib/api'
import { setCaregiver } from '../lib/caregiver'
import { primeAudio } from '../lib/alarm'
import { Logo } from '../components/Logo'
import { Field } from '../components/Field'
import { Button } from '../components/Button'

export default function Join() {
  const [q] = useSearchParams()
  const nav = useNavigate()
  const [code, setCode] = useState((q.get('code') ?? '').toUpperCase())
  const [name, setName] = useState('')
  const [rel, setRel] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const join = async () => {
    setBusy(true); setErr(null); primeAudio()
    try {
      const r = await api.join(code.trim().toUpperCase(), name.trim(), rel.trim() || undefined)
      setCaregiver({ household_id: r.household.id, token: r.caregiver_token, caregiver_id: r.caregiver.id, name: r.caregiver.name })
      if ('Notification' in window && Notification.permission === 'default') await Notification.requestPermission()
      nav('/live')
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Something went wrong')
    } finally { setBusy(false) }
  }

  return (
    <div className="min-h-dvh bg-ink-950">
      <div className="pointer-events-none fixed inset-x-0 top-0 h-[420px] bg-[radial-gradient(60%_100%_at_50%_0%,rgb(124_183_255/.09),transparent)]" />
      <div className="relative mx-auto flex min-h-dvh max-w-[440px] flex-col px-5 pb-6 pt-[max(1rem,env(safe-area-inset-top))]">
        <Logo size={26} />
        <motion.div className="mt-12 space-y-4" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1] }}>
          <h1 className="text-[32px] font-semibold leading-[1.05] tracking-[-0.035em]">Join as a caregiver</h1>
          <p className="text-[14.5px] leading-relaxed text-fg-3">You’ll get an alert the moment their power goes out, with a live countdown of every device’s battery.</p>
          <label className="block pt-2">
            <span className="mb-1.5 block text-[12px] font-medium text-fg-3">Family code</span>
            <input value={code} onChange={(e) => setCode(e.target.value.toUpperCase().slice(0, 6))} placeholder="K7Q2MX" autoCapitalize="characters"
              className="tnum h-16 w-full rounded-2xl bg-white/[0.04] text-center font-mono text-[30px] tracking-[0.3em] text-amber outline-none ring-1 ring-inset ring-white/[0.08] placeholder:text-fg-4 focus:ring-amber/60" />
          </label>
          <Field label="Your name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Maya" />
          <Field label="Relationship" value={rel} onChange={(e) => setRel(e.target.value)} placeholder="Daughter, neighbor, nurse…" />
          {err && <p className="rounded-xl bg-alert/10 p-3 text-[13px] text-alert ring-1 ring-inset ring-alert/20">{err}</p>}
        </motion.div>
        <Button size="lg" className="mt-auto w-full" disabled={busy || code.length !== 6 || !name.trim()} onClick={join}>
          {busy ? 'Joining…' : <>Join <ArrowRight size={18} /></>}
        </Button>
      </div>
    </div>
  )
}
