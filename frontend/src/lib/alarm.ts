// A two-tone alert built with Web Audio, so it plays without shipping an audio file.

let ctx: AudioContext | null = null
let timer: number | null = null

function beep(freq: number, at: number, dur = 0.16) {
  if (!ctx) return
  const o = ctx.createOscillator()
  const g = ctx.createGain()
  o.type = 'sine'
  o.frequency.value = freq
  g.gain.setValueAtTime(0, at)
  g.gain.linearRampToValueAtTime(0.28, at + 0.012)
  g.gain.exponentialRampToValueAtTime(0.0001, at + dur)
  o.connect(g).connect(ctx.destination)
  o.start(at)
  o.stop(at + dur + 0.02)
}

function chime() {
  if (!ctx) return
  const t = ctx.currentTime
  beep(880, t); beep(1320, t + 0.18); beep(880, t + 0.36); beep(1320, t + 0.54)
  navigator.vibrate?.([220, 120, 220, 120, 400])
}

/** Call from a user gesture once so browsers allow sound later. */
export function primeAudio() {
  if (!ctx) ctx = new AudioContext()
  if (ctx.state === 'suspended') ctx.resume()
}

export function startAlarm() {
  primeAudio()
  stopAlarm()
  chime()
  timer = window.setInterval(chime, 2600)
}

export function stopAlarm() {
  if (timer !== null) { clearInterval(timer); timer = null }
  navigator.vibrate?.(0)
}

export function notify(title: string, body: string) {
  if (!('Notification' in window) || Notification.permission !== 'granted') return
  try { new Notification(title, { body, icon: '/icon-192.png', tag: 'lifeline-outage', requireInteraction: true }) } catch { /* ignore */ }
}
