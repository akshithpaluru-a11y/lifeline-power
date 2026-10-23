import type { ButtonHTMLAttributes, ReactNode } from 'react'

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'light'

const V: Record<Variant, string> = {
  primary: 'text-ink-950 bg-gradient-to-b from-amber-hi to-amber shadow-[inset_0_1px_0_rgb(255_255_255/.5),0_8px_24px_-8px_rgb(255_178_36/.6)] hover:brightness-105',
  secondary: 'text-fg bg-white/[0.06] ring-1 ring-inset ring-white/10 hover:bg-white/[0.09]',
  ghost: 'text-fg-2 hover:text-fg hover:bg-white/5',
  danger: 'text-white bg-gradient-to-b from-[#ff6f64] to-alert shadow-[inset_0_1px_0_rgb(255_255_255/.35),0_8px_24px_-8px_rgb(255_90_78/.7)] hover:brightness-105',
  light: 'text-ink-950 bg-fg hover:bg-white',
}

export function Button({ variant = 'primary', size = 'md', icon, children, className = '', ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant
  size?: 'sm' | 'md' | 'lg'
  icon?: ReactNode
}) {
  const s = { sm: 'h-9 px-3.5 text-[13px] rounded-xl', md: 'h-11 px-4 text-[14px] rounded-2xl', lg: 'h-14 px-5 text-[16px] rounded-[18px]' }[size]
  return (
    <button {...rest} className={`inline-flex select-none items-center justify-center gap-2 font-medium tracking-[-0.01em] transition active:scale-[.98] disabled:opacity-40 disabled:pointer-events-none ${s} ${V[variant]} ${className}`}>
      {icon}
      {children}
    </button>
  )
}
