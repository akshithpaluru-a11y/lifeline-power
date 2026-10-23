import type { ReactNode } from 'react'

const TONES = {
  live: 'text-live bg-live/10 ring-live/20',
  amber: 'text-amber bg-amber/10 ring-amber/20',
  alert: 'text-alert bg-alert/10 ring-alert/25',
  sky: 'text-sky bg-sky/10 ring-sky/20',
  muted: 'text-fg-2 bg-white/5 ring-white/10',
}

export function Pill({ tone = 'muted', pulse, children, className = '' }: {
  tone?: keyof typeof TONES
  pulse?: boolean
  children: ReactNode
  className?: string
}) {
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-medium tracking-wide ring-1 ring-inset ${TONES[tone]} ${className}`}>
      {pulse !== undefined && (
        <span className="relative flex size-1.5">
          {pulse && <span className="absolute inset-0 rounded-full bg-current" style={{ animation: 'ping-soft 1.6s var(--ease-out-expo) infinite' }} />}
          <span className="relative size-1.5 rounded-full bg-current" />
        </span>
      )}
      {children}
    </span>
  )
}
