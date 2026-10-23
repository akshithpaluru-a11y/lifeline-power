// Segmented level meter, like the readout on a lab instrument.
export function Segments({ value, count = 12, tone = 'amber', className = '' }: {
  value: number
  count?: number
  tone?: 'amber' | 'live' | 'alert' | 'muted'
  className?: string
}) {
  const on = Math.round(Math.max(0, Math.min(1, value)) * count)
  const color = {
    amber: 'bg-amber shadow-[0_0_8px_-1px] shadow-amber/60',
    live: 'bg-live shadow-[0_0_8px_-1px] shadow-live/60',
    alert: 'bg-alert shadow-[0_0_8px_-1px] shadow-alert/60',
    muted: 'bg-fg-3',
  }[tone]
  return (
    <div className={`flex gap-[3px] ${className}`} role="meter" aria-valuenow={Math.round(value * 100)} aria-valuemin={0} aria-valuemax={100}>
      {Array.from({ length: count }, (_, i) => (
        <span key={i} className={`h-full flex-1 rounded-[2px] transition-colors duration-500 ${i < on ? color : 'bg-white/[0.07]'}`} />
      ))}
    </div>
  )
}
