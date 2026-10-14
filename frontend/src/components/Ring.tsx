import { motion } from 'motion/react'
import type { ReactNode } from 'react'

/**
 * Countdown dial: a bezel of 60 ticks with a glowing arc for what's left.
 * `value` is the fraction remaining (1 = full).
 */
export function Ring({ value, size = 300, tone = 'amber', children }: {
  value: number
  size?: number
  tone?: 'amber' | 'alert' | 'live'
  children?: ReactNode
}) {
  const v = Math.max(0, Math.min(1, value))
  const stroke = 10
  const r = size / 2 - 22
  const c = 2 * Math.PI * r
  const colors = {
    amber: ['#FFD36B', '#FF8A1C'],
    alert: ['#FF8A7F', '#FF3B30'],
    live: ['#8CF5C3', '#22C27D'],
  }[tone]
  const angle = -90 + v * 360
  const hx = size / 2 + r * Math.cos((angle * Math.PI) / 180)
  const hy = size / 2 + r * Math.sin((angle * Math.PI) / 180)
  const id = `ring-${tone}`

  return (
    <div className="relative" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="absolute inset-0 overflow-visible">
        <defs>
          <linearGradient id={id} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor={colors[0]} />
            <stop offset="1" stopColor={colors[1]} />
          </linearGradient>
          <filter id={`${id}-glow`} x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="6" />
          </filter>
        </defs>
        {Array.from({ length: 60 }, (_, i) => {
          const a = (i / 60) * 2 * Math.PI - Math.PI / 2
          const major = i % 5 === 0
          const r1 = size / 2 - 4
          const r2 = r1 - (major ? 9 : 5)
          const lit = i / 60 < v
          return (
            <line key={i}
              x1={size / 2 + r1 * Math.cos(a)} y1={size / 2 + r1 * Math.sin(a)}
              x2={size / 2 + r2 * Math.cos(a)} y2={size / 2 + r2 * Math.sin(a)}
              stroke={lit ? colors[0] : 'white'} strokeOpacity={lit ? (major ? 0.9 : 0.45) : major ? 0.18 : 0.08}
              strokeWidth={major ? 1.6 : 1} strokeLinecap="round" />
          )
        })}
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="white" strokeOpacity={0.06} strokeWidth={stroke} />
        <motion.circle
          cx={size / 2} cy={size / 2} r={r} fill="none" stroke={`url(#${id})`} strokeWidth={stroke} strokeLinecap="round"
          strokeDasharray={c} transform={`rotate(-90 ${size / 2} ${size / 2})`}
          initial={{ strokeDashoffset: c }} animate={{ strokeDashoffset: c * (1 - v) }}
          transition={{ duration: 1.4, ease: [0.16, 1, 0.3, 1] }}
          filter={`url(#${id}-glow)`} opacity={0.55}
        />
        <motion.circle
          cx={size / 2} cy={size / 2} r={r} fill="none" stroke={`url(#${id})`} strokeWidth={stroke} strokeLinecap="round"
          strokeDasharray={c} transform={`rotate(-90 ${size / 2} ${size / 2})`}
          initial={{ strokeDashoffset: c }} animate={{ strokeDashoffset: c * (1 - v) }}
          transition={{ duration: 1.4, ease: [0.16, 1, 0.3, 1] }}
        />
        {v > 0.005 && <circle cx={hx} cy={hy} r={4} fill="white" style={{ filter: `drop-shadow(0 0 6px ${colors[0]})` }} />}
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center text-center">{children}</div>
    </div>
  )
}
