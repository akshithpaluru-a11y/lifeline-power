// Scrolling monitor trace. Steady beats while powered, a flat line with a single
// stutter when the power is out.
export function PulseTrace({ tone = 'live', height = 56, flat = false }: { tone?: 'live' | 'amber' | 'alert'; height?: number; flat?: boolean }) {
  const color = { live: '#3DDC97', amber: '#FFB224', alert: '#FF5A4E' }[tone]
  const beat = flat
    ? 'h120'
    : 'h34 l4 -6 l4 6 h6 l5 -26 l6 44 l5 -24 h8 l5 -5 l6 5 h37'
  const seg = `M0 30 ${beat} ${beat}`
  return (
    <div className="relative overflow-hidden [mask-image:linear-gradient(90deg,transparent,black_18%,black_82%,transparent)]" style={{ height }}>
      <svg viewBox="0 0 240 56" preserveAspectRatio="none" className="absolute inset-y-0 left-0 h-full w-[200%]" style={{ animation: 'sweep 3.2s linear infinite reverse' }}>
        <defs>
          <linearGradient id={`pt-${tone}`} x1="0" x2="1">
            <stop offset="0" stopColor={color} stopOpacity="0" />
            <stop offset=".5" stopColor={color} stopOpacity="1" />
            <stop offset="1" stopColor={color} stopOpacity=".2" />
          </linearGradient>
        </defs>
        <path d={seg} fill="none" stroke={`url(#pt-${tone})`} strokeWidth="1.6" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
      </svg>
    </div>
  )
}
