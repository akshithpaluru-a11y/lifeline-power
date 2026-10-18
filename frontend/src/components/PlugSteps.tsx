import { Plug } from 'lucide-react'
import type { PlanDevice } from '../lib/types'

/** The server's plug instructions for one device, in order. "now" steps are the ones to do right away. */
export function PlugSteps({ steps, className = '' }: { steps: NonNullable<PlanDevice['plug_steps']>; className?: string }) {
  if (!steps.length) return null
  return (
    <ol className={`space-y-1 ${className}`}>
      {steps.map((s, i) => (
        <li key={i} className={`flex items-start gap-1.5 text-[12px] leading-snug ${s.now ? 'text-fg-2' : 'text-fg-4'}`}>
          <Plug size={12} className={`mt-[2px] shrink-0 ${s.now ? 'text-sky' : 'text-fg-4'}`} />
          <span>{s.text}</span>
        </li>
      ))}
    </ol>
  )
}
