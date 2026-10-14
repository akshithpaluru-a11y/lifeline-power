import { Link } from 'react-router-dom'
import { ArrowLeft, Printer } from 'lucide-react'
import { useStore } from '../lib/store'
import { humanMinutes } from '../lib/format'
import { templateFor } from '../lib/catalog'
import { LogoMark } from '../components/Logo'
import { Button } from '../components/Button'

export default function Card() {
  const h = useStore((s) => s.household)!
  const plan = useStore((s) => s.plan)
  return (
    <div className="pt-2">
      <div className="no-print flex items-center justify-between">
        <Link to="/app" className="flex items-center gap-1.5 text-[13px] text-fg-3 hover:text-fg"><ArrowLeft size={15} /> Back</Link>
        <Button size="sm" variant="light" icon={<Printer size={14} />} onClick={() => print()}>Print card</Button>
      </div>
      <h1 className="no-print mt-4 text-[28px] font-semibold tracking-[-0.03em]">Emergency card</h1>
      <p className="no-print mt-1 text-[13.5px] text-fg-3">Tape it to the fridge or the device. Responders see what matters in five seconds.</p>

      <article className="mt-5 overflow-hidden rounded-[22px] bg-[#F6F4EE] text-[#121413] shadow-[0_30px_80px_-30px_rgb(255_178_36/.35)] print:rounded-none print:shadow-none">
        <header className="flex items-center justify-between gap-3 bg-[#121413] px-5 py-3.5 text-[#F6F4EE]">
          <span className="flex items-center gap-2 text-[11.5px] font-semibold uppercase tracking-[0.14em]"><LogoMark size={20} /> Powered medical equipment</span>
          <span className="shrink-0 rounded-md bg-[#FF5A4E] px-2 py-0.5 text-[10.5px] font-bold uppercase tracking-wider text-white">Priority</span>
        </header>
        <div className="space-y-4 p-5">
          <div>
            <div className="text-[10.5px] font-semibold uppercase tracking-[0.14em] text-[#6B6F68]">Patient</div>
            <div className="text-[24px] font-semibold tracking-tight">{h.patient_first_name}</div>
            {h.address && <div className="text-[13px] text-[#4A4E48]">{h.address}</div>}
          </div>
          {plan?.critical_runtime_minutes != null && (
            <div className="flex items-baseline justify-between rounded-xl bg-[#121413]/[0.05] px-3.5 py-2.5">
              <span className="text-[12px] font-medium">Life support on battery and backup</span>
              <span className="tnum text-[18px] font-semibold">{humanMinutes(plan.critical_runtime_minutes)}</span>
            </div>
          )}
          <div>
            <div className="text-[10.5px] font-semibold uppercase tracking-[0.14em] text-[#6B6F68]">Depends on</div>
            <table className="mt-1.5 w-full text-[13px]">
              <tbody>
                {h.devices.map((d) => (
                  <tr key={d.id} className="border-t border-[#121413]/10">
                    <td className="py-1.5 font-medium">{d.label}{d.life_sustaining && <span className="ml-1.5 text-[#D9382D]">●</span>}</td>
                    <td className="py-1.5 pl-3 text-[#4A4E48]">{templateFor(d.kind).name}</td>
                    <td className="tnum whitespace-nowrap py-1.5 pl-3 text-right font-medium">{d.draw_watts} W</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="mt-1 text-[10.5px] text-[#6B6F68]">● life-sustaining</div>
          </div>
          <div>
            <div className="text-[10.5px] font-semibold uppercase tracking-[0.14em] text-[#6B6F68]">Caregivers</div>
            <ul className="mt-1 grid grid-cols-2 gap-x-4 gap-y-1 text-[13px]">
              {h.caregivers.map((c) => <li key={c.id}><span className="font-medium">{c.name}</span> <span className="text-[#6B6F68]">{c.relationship}</span></li>)}
            </ul>
          </div>
          <div className="flex items-center justify-between border-t border-[#121413]/10 pt-3 text-[11.5px] text-[#4A4E48]">
            <span>{h.utility_name ? `Utility: ${h.utility_name}` : ''}{h.medical_program_registered ? ' · Medical priority customer' : ''}</span>
            <span className="font-semibold text-[#D9382D]">Breathing trouble: call 911</span>
          </div>
        </div>
      </article>
    </div>
  )
}
