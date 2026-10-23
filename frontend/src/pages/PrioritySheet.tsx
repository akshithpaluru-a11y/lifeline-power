import { useEffect, useState } from 'react'
import { CircleCheck, Copy, ExternalLink, Check } from 'lucide-react'
import { Sheet } from '../components/Sheet'
import { Button } from '../components/Button'
import { api } from '../lib/api'
import { getState, updateHousehold, useStore } from '../lib/store'
import type { MedicalProgram } from '../lib/types'

export function PrioritySheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const h = useStore((s) => s.household)!
  const [programs, setPrograms] = useState<MedicalProgram[]>([])
  const [advice, setAdvice] = useState<string[]>([])
  const [letter, setLetter] = useState<{ subject: string; body: string } | null>(null)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (!open) return
    api.medicalPrograms(h.state, h.utility_name).then((r) => { setPrograms(r.programs); setAdvice(r.generic_advice) }).catch(() => {})
    const s = getState().session
    if (s) api.medicalLetter(s.household_id, s.token).then(setLetter).catch(() => {})
  }, [open, h.state, h.utility_name])

  const p = programs[0]
  return (
    <Sheet open={open} onClose={onClose} title="Get on the priority list">
      <div className="space-y-4 text-[14px] leading-relaxed text-fg-2">
        {p ? (
          <div className="rounded-2xl bg-white/[0.04] p-4 ring-1 ring-inset ring-white/[0.06]">
            <div className="text-[12px] font-medium uppercase tracking-[0.12em] text-fg-3">{p.utility}</div>
            <div className="mt-1 text-[16px] font-semibold text-fg">{p.program_name}</div>
            <p className="mt-2 text-[13.5px]">{p.what_it_does}</p>
            <p className="mt-2 text-[13px] text-fg-3">{p.how_to_apply}</p>
            {p.url && <a href={p.url} target="_blank" rel="noreferrer" className="mt-3 inline-flex items-center gap-1.5 text-[13px] text-amber">Open the sign-up page <ExternalLink size={13} /></a>}
          </div>
        ) : (
          <p>Most power companies keep a list of homes with life-sustaining equipment, often called <span className="text-fg">Medical Baseline</span>, <span className="text-fg">Critical Care</span> or <span className="text-fg">Life Support</span>.</p>
        )}
        <ul className="space-y-2.5">
          {advice.map((t) => <li key={t} className="flex gap-2.5"><CircleCheck size={17} className="mt-0.5 shrink-0 text-live" />{t}</li>)}
        </ul>
        {letter && (
          <div className="rounded-2xl bg-[#F6F4EE] p-4 text-[12.5px] leading-relaxed text-[#121413]">
            <div className="flex items-center justify-between">
              <span className="font-semibold">{letter.subject}</span>
              <button onClick={() => { navigator.clipboard?.writeText(`${letter.subject}\n\n${letter.body}`); setCopied(true); setTimeout(() => setCopied(false), 1500) }}
                className="flex items-center gap-1 rounded-lg bg-[#121413]/10 px-2 py-1 text-[11.5px] font-medium">{copied ? <Check size={12} /> : <Copy size={12} />}{copied ? 'Copied' : 'Copy'}</button>
            </div>
            <pre className="mt-2 max-h-40 overflow-y-auto whitespace-pre-wrap font-sans">{letter.body}</pre>
          </div>
        )}
        <Button className="w-full" onClick={async () => { await updateHousehold({ medical_program_registered: true }); onClose() }}>
          {h.medical_program_registered ? 'Registered' : 'We’re registered'}
        </Button>
      </div>
    </Sheet>
  )
}
