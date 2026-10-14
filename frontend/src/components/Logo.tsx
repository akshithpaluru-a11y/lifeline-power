export function LogoMark({ size = 28, className = '' }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" className={className} aria-hidden>
      <defs>
        <linearGradient id="ll-a" gradientUnits="userSpaceOnUse" x1="0" y1="10" x2="0" y2="54">
          <stop offset="0" stopColor="#FFD36B" />
          <stop offset="1" stopColor="#FF8A1C" />
        </linearGradient>
        <linearGradient id="ll-b" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#1B1E1C" />
          <stop offset="1" stopColor="#090A09" />
        </linearGradient>
      </defs>
      <rect width="64" height="64" rx="15" fill="url(#ll-b)" />
      <rect x=".5" y=".5" width="63" height="63" rx="14.5" fill="none" stroke="#fff" strokeOpacity=".09" />
      <path d="M9 33h11l3.2-6 3.3 6" fill="none" stroke="url(#ll-a)" strokeWidth="3.2" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M41 33h14" stroke="url(#ll-a)" strokeWidth="3.2" strokeLinecap="round" />
      <path d="M36.5 13 27 34.5h7.5L31 51l11.5-22.5H35L38.5 13z" fill="url(#ll-a)" stroke="#121413" strokeWidth="2.4" paintOrder="stroke" strokeLinejoin="round" />
    </svg>
  )
}

export function Logo({ size = 28 }: { size?: number }) {
  return (
    <span className="inline-flex items-center gap-2.5">
      <LogoMark size={size} />
      <span className="text-[15px] font-semibold tracking-[-0.02em]">
        LifeLine<span className="text-fg-3 font-normal"> Power</span>
      </span>
    </span>
  )
}
