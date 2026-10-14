export function hms(ms: number) {
  const s = Math.max(0, Math.floor(ms / 1000))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  const pad = (n: number) => String(n).padStart(2, '0')
  return { h, m, s: sec, text: `${h}:${pad(m)}:${pad(sec)}` }
}

export function humanMinutes(min: number) {
  if (min <= 0) return 'No battery'
  const h = Math.floor(min / 60)
  const m = Math.round(min % 60)
  if (h === 0) return `${m}m`
  return m ? `${h}h ${m}m` : `${h}h`
}

export function clock(iso: string | Date) {
  const d = typeof iso === 'string' ? new Date(iso) : iso
  return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
}

export function ago(iso: string, now = Date.now()) {
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000))
  if (s < 60) return `${s}s ago`
  const m = Math.round(s / 60)
  if (m < 60) return `${m} min ago`
  return `${Math.floor(m / 60)}h ${m % 60}m ago`
}

export const uid = () => Math.random().toString(36).slice(2, 10)
