import { useState } from 'react'
import { motion } from 'motion/react'
import { Check, Copy, Share2, X, Bell, Car, Eye, RefreshCw } from 'lucide-react'
import { useStore, removeCaregiver } from '../lib/store'
import { ago } from '../lib/format'
import { Qr } from '../components/Qr'
import { Button } from '../components/Button'

const HUES = ['from-[#FFB224] to-[#FF7A1C]', 'from-[#7CB7FF] to-[#4F7BFF]', 'from-[#3DDC97] to-[#14A86B]', 'from-[#FF8A9B] to-[#E84867]']

export default function People() {
  const h = useStore((s) => s.household)!
  const [copied, setCopied] = useState(false)
  const [rotated, setRotated] = useState<string | null>(null)
  const remove = async (id: string, name: string) => { await removeCaregiver(id); setRotated(name) }
  const url = `${location.origin}/join?code=${h.join_code}`
  const share = async () => {
    const text = `Join ${h.patient_first_name}'s LifeLine so you get an alert if the power goes out. Code: ${h.join_code}`
    if (navigator.share) { try { await navigator.share({ title: 'LifeLine Power', text, url }); return } catch { /* cancelled */ } }
    navigator.clipboard?.writeText(`${text}\n${url}`); setCopied(true); setTimeout(() => setCopied(false), 1600)
  }

  return (
    <div className="pt-2">
      <h1 className="text-[28px] font-semibold tracking-[-0.03em]">Family</h1>
      <p className="mt-1 text-[13.5px] text-fg-3">Caregivers join with this code. They get the alarm, the live countdown and every check-in on their own phone.</p>

      <motion.section initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="surface mt-5 overflow-hidden rounded-[28px]">
        <div className="flex items-center gap-5 p-5">
          <div className="shrink-0 rounded-[20px] bg-fg p-3 shadow-[0_20px_50px_-20px_rgb(255_178_36/.5)]"><Qr value={url} size={112} /></div>
          <div className="min-w-0">
            <div className="text-[11.5px] font-medium uppercase tracking-[0.14em] text-fg-3">Family code</div>
            <div className="tnum mt-1 font-mono text-[30px] font-medium tracking-[0.12em] text-amber">{h.join_code}</div>
            <p className="mt-1 text-[12px] leading-snug text-fg-3">Scan, or open {location.host}/join and type the code.</p>
          </div>
        </div>
        <div className="grid grid-cols-2 gap-px border-t border-white/[0.06] bg-white/[0.06]">
          <button onClick={share} className="flex items-center justify-center gap-2 bg-ink-900 py-3.5 text-[13.5px] font-medium hover:bg-ink-850"><Share2 size={15} /> Share invite</button>
          <button onClick={() => { navigator.clipboard?.writeText(url); setCopied(true); setTimeout(() => setCopied(false), 1600) }} className="flex items-center justify-center gap-2 bg-ink-900 py-3.5 text-[13.5px] font-medium hover:bg-ink-850">
            {copied ? <Check size={15} className="text-live" /> : <Copy size={15} />} {copied ? 'Copied' : 'Copy link'}
          </button>
        </div>
      </motion.section>
      {rotated && (
        <motion.div initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} className="mt-3 flex items-start gap-3 rounded-2xl bg-amber/[0.08] p-3.5 ring-1 ring-inset ring-amber/20">
          <RefreshCw size={16} className="mt-0.5 shrink-0 text-amber" />
          <p className="flex-1 text-[13px] leading-relaxed text-fg-2">{rotated} was removed, so the family code changed to keep them out. Share the new code with everyone who still needs it. People already joined stay connected.</p>
          <button onClick={() => setRotated(null)} className="text-fg-4 hover:text-fg" aria-label="Dismiss"><X size={15} /></button>
        </motion.div>
      )}

      <h2 className="mt-7 text-[12px] font-medium uppercase tracking-[0.14em] text-fg-3">Caregivers · {h.caregivers.length}</h2>
      {h.caregivers.length ? (
        <div className="surface mt-3 overflow-hidden rounded-[24px]">
          {h.caregivers.map((c, i) => (
            <div key={c.id} className="flex items-center gap-3.5 border-b border-white/[0.05] px-4 py-3.5 last:border-0">
              <span className={`grid size-10 shrink-0 place-items-center rounded-full bg-gradient-to-br ${HUES[i % HUES.length]} text-[14px] font-semibold text-ink-950`}>{c.name.slice(0, 1)}</span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[14.5px] font-medium">{c.name}</span>
                <span className="block text-[12.5px] text-fg-3">{c.relationship || 'Caregiver'} · joined {ago(c.joined_at)}</span>
              </span>
              <button onClick={() => remove(c.id, c.name)} className="grid size-8 place-items-center rounded-full text-fg-4 hover:bg-white/5 hover:text-fg" aria-label={`Remove ${c.name}`}><X size={15} /></button>
            </div>
          ))}
        </div>
      ) : (
        <p className="mt-3 rounded-2xl bg-white/[0.03] p-4 text-[13px] text-fg-3 ring-1 ring-inset ring-white/[0.05]">No one yet. Share the invite with at least two people.</p>
      )}

      <h2 className="mt-7 text-[12px] font-medium uppercase tracking-[0.14em] text-fg-3">What caregivers get</h2>
      <div className="mt-3 grid grid-cols-3 gap-2.5">
        {[{ i: Bell, t: 'An alert the second power cuts' }, { i: Eye, t: 'The live countdown' }, { i: Car, t: 'A button to say they’re coming' }].map(({ i: I, t }) => (
          <div key={t} className="surface rounded-[20px] p-3.5">
            <I size={17} className="text-amber" />
            <p className="mt-3 text-[12px] leading-snug text-fg-2">{t}</p>
          </div>
        ))}
      </div>
      <Button variant="ghost" className="mt-4 w-full" onClick={() => window.open(url, '_blank')}>Preview the caregiver view</Button>
    </div>
  )
}
