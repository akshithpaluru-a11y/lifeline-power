import { useEffect, useRef, useState } from 'react'

interface BatteryManager extends EventTarget {
  charging: boolean
  level: number
}

export type PowerSupport = 'battery-api' | 'unsupported' | 'pending'

/**
 * Watches the phone/laptop charger. Calls onLost when charging stops, which is how
 * LifeLine notices a power cut: the device stays plugged in at home, so losing the
 * charger means the wall socket went dead.
 */
export function useChargerWatch(enabled: boolean, onLost: () => void, onRestored?: () => void) {
  const [support, setSupport] = useState<PowerSupport>('pending')
  const [charging, setCharging] = useState<boolean | null>(null)
  const [level, setLevel] = useState<number | null>(null)
  const lost = useRef(onLost)
  const restored = useRef(onRestored)
  lost.current = onLost
  restored.current = onRestored

  useEffect(() => {
    const nav = navigator as Navigator & { getBattery?: () => Promise<BatteryManager> }
    if (!nav.getBattery) { setSupport('unsupported'); return }
    let bm: BatteryManager | null = null
    let prev: boolean | null = null
    const update = () => {
      if (!bm) return
      setCharging(bm.charging)
      setLevel(bm.level)
      if (enabled && prev === true && bm.charging === false) lost.current()
      if (enabled && prev === false && bm.charging === true) restored.current?.()
      prev = bm.charging
    }
    nav.getBattery().then((b) => {
      bm = b
      setSupport('battery-api')
      prev = b.charging
      setCharging(b.charging)
      setLevel(b.level)
      b.addEventListener('chargingchange', update)
      b.addEventListener('levelchange', update)
    }).catch(() => setSupport('unsupported'))
    return () => {
      bm?.removeEventListener('chargingchange', update)
      bm?.removeEventListener('levelchange', update)
    }
  }, [enabled])

  return { support, charging, level }
}

/** Keeps the screen awake while LifeLine is watching, so the browser isn't suspended. */
export function useWakeLock(on: boolean) {
  useEffect(() => {
    if (!on || !('wakeLock' in navigator)) return
    let lock: WakeLockSentinel | null = null
    let cancelled = false
    const acquire = () => navigator.wakeLock.request('screen').then((l) => {
      if (cancelled) l.release(); else lock = l
    }).catch(() => {})
    acquire()
    const onVis = () => { if (document.visibilityState === 'visible') acquire() }
    document.addEventListener('visibilitychange', onVis)
    return () => { cancelled = true; lock?.release(); document.removeEventListener('visibilitychange', onVis) }
  }, [on])
}
