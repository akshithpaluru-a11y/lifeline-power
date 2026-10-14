import type { InputHTMLAttributes, ReactNode } from 'react'

export function Field({ label, hint, suffix, ...rest }: InputHTMLAttributes<HTMLInputElement> & { label: string; hint?: ReactNode; suffix?: string }) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-[12px] font-medium text-fg-3">{label}</span>
      <span className="flex items-center rounded-2xl bg-white/[0.04] ring-1 ring-inset ring-white/[0.08] focus-within:ring-amber/60 transition">
        <input {...rest} className="h-12 w-full bg-transparent px-4 text-[15px] text-fg placeholder:text-fg-4 outline-none" />
        {suffix && <span className="pr-4 text-[13px] text-fg-3">{suffix}</span>}
      </span>
      {hint && <span className="mt-1.5 block text-[11.5px] text-fg-4">{hint}</span>}
    </label>
  )
}
