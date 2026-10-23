import { useEffect, useRef, useState } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { Flame, Hospital, Navigation, Library, Building2, ShieldPlus, Tent, MapPin, LocateFixed, Zap } from 'lucide-react'
import { useStore, getState } from '../lib/store'
import { api } from '../lib/api'
import type { HelpSite } from '../lib/types'

type HelpKind = HelpSite['kind']

const KIND: Record<HelpKind, { label: string; icon: typeof Hospital; color: string; cls: string }> = {
  hospital: { label: 'Hospital', icon: Hospital, color: '#FF5A4E', cls: 'bg-alert/12 text-alert' },
  fire_station: { label: 'Fire station', icon: Flame, color: '#FF8A1C', cls: 'bg-amber-lo/12 text-amber-lo' },
  shelter: { label: 'Shelter', icon: Tent, color: '#3DDC97', cls: 'bg-live/12 text-live' },
  library: { label: 'Library', icon: Library, color: '#7CB7FF', cls: 'bg-sky/12 text-sky' },
  community_centre: { label: 'Community center', icon: Building2, color: '#7CB7FF', cls: 'bg-sky/12 text-sky' },
  police: { label: 'Police', icon: ShieldPlus, color: '#B9BBB5', cls: 'bg-white/10 text-fg-2' },
  other: { label: 'Other', icon: MapPin, color: '#B9BBB5', cls: 'bg-white/10 text-fg-2' },
}

const pin = (color: string, home = false) => L.divIcon({
  className: '',
  iconSize: [24, 24],
  iconAnchor: [12, 12],
  html: home
    ? `<div style="position:relative;width:24px;height:24px"><div style="position:absolute;inset:-10px;border-radius:999px;background:${color};opacity:.18"></div><div style="position:absolute;inset:4px;border-radius:999px;background:${color};box-shadow:0 0 0 3px #08090A,0 0 16px ${color}"></div></div>`
    : `<div style="width:24px;height:24px;border-radius:999px;background:#131614;box-shadow:inset 0 0 0 2px ${color},0 4px 12px rgba(0,0,0,.6);display:grid;place-items:center"><div style="width:8px;height:8px;border-radius:99px;background:${color}"></div></div>`,
})

export default function Help() {
  const h = useStore((s) => s.household)!
  const [here, setHere] = useState<{ lat: number; lon: number }>({ lat: h.lat ?? 38.8899, lon: h.lon ?? -77.0091 })
  const [places, setPlaces] = useState<HelpSite[]>([])
  const [note, setNote] = useState<string | null>(null)
  const [filter, setFilter] = useState<'all' | 'power' | HelpKind>('all')
  const el = useRef<HTMLDivElement>(null)
  const map = useRef<L.Map | null>(null)
  const layer = useRef<L.LayerGroup | null>(null)

  useEffect(() => {
    const s = getState().session
    if (s) api.helpSites(s.household_id, s.token, here.lat, here.lon).then((r) => { setPlaces(r.sites); setNote(r.note ?? null) }).catch(() => {})
  }, [here.lat, here.lon])

  useEffect(() => {
    if (!el.current || map.current) return
    map.current = L.map(el.current, { zoomControl: false, attributionControl: true }).setView([here.lat, here.lon], 14)
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
      attribution: '© OpenStreetMap © CARTO', subdomains: 'abcd', maxZoom: 19,
    }).addTo(map.current)
    layer.current = L.layerGroup().addTo(map.current)
    return () => { map.current?.remove(); map.current = null }
  }, [])

  const shown = places.filter((p) => filter === 'all' || (filter === 'power' ? p.likely_has_power : p.kind === filter))

  useEffect(() => {
    if (!map.current || !layer.current) return
    layer.current.clearLayers()
    L.marker([here.lat, here.lon], { icon: pin('#FFB224', true) }).addTo(layer.current)
    shown.forEach((p) => L.marker([p.lat, p.lon], { icon: pin(KIND[p.kind]?.color ?? '#B9BBB5') }).bindTooltip(p.name, { direction: 'top', offset: [0, -10] }).addTo(layer.current!))
    if (shown.length) map.current.fitBounds(L.latLngBounds([[here.lat, here.lon], ...shown.map((p) => [p.lat, p.lon] as [number, number])]).pad(0.25))
  }, [shown.map((p) => p.id).join(), here.lat, here.lon])

  const locate = () => navigator.geolocation?.getCurrentPosition((pos) => setHere({ lat: pos.coords.latitude, lon: pos.coords.longitude }))
  const kinds = Array.from(new Set(places.map((p) => p.kind)))

  return (
    <div className="pt-2">
      <h1 className="text-[28px] font-semibold tracking-[-0.03em]">Nearby help</h1>
      <p className="mt-1 text-[13.5px] text-fg-3">Where to charge devices, cool off or get care if the power stays out.</p>

      <div className="relative mt-4 overflow-hidden rounded-[24px] ring-1 ring-white/[0.08]">
        <div ref={el} className="h-[300px] w-full" />
        <button onClick={locate} className="absolute right-3 top-3 z-[500] grid size-9 place-items-center rounded-xl bg-ink-850/90 text-fg ring-1 ring-white/10 backdrop-blur" aria-label="Use my location"><LocateFixed size={16} /></button>
      </div>

      <div className="-mx-5 mt-4 flex gap-2 overflow-x-auto px-5 [scrollbar-width:none]">
        {(['all', 'power', ...kinds] as const).map((k) => (
          <button key={k} onClick={() => setFilter(k)}
            className={`shrink-0 rounded-full px-3.5 py-1.5 text-[12.5px] font-medium ring-1 ring-inset transition ${filter === k ? 'bg-fg text-ink-950 ring-fg' : 'bg-white/[0.04] text-fg-2 ring-white/10'}`}>
            {k === 'all' ? 'All' : k === 'power' ? 'Has backup power' : KIND[k].label}
          </button>
        ))}
      </div>

      <ul className="mt-4 space-y-2">
        {shown.map((p) => {
          const k = KIND[p.kind] ?? KIND.other
          return (
            <li key={p.id} className="surface flex items-center gap-3.5 rounded-[20px] p-3.5">
              <span className={`grid size-10 place-items-center rounded-xl ${k.cls}`}><k.icon size={18} strokeWidth={1.8} /></span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[14px] font-medium">{p.name}</span>
                <span className="flex items-center gap-1 text-[12px] text-fg-3">{p.likely_has_power && <Zap size={11} className="text-live" />}{k.label} · {p.travel_minutes} min drive</span>
              </span>
              <a href={`https://www.google.com/maps/dir/?api=1&destination=${p.lat},${p.lon}`} target="_blank" rel="noreferrer"
                className="flex items-center gap-1.5 rounded-xl bg-white/5 px-2.5 py-2 text-[12px] font-medium ring-1 ring-inset ring-white/10 hover:bg-white/10">
                <Navigation size={13} /><span className="tnum">{p.distance_km.toFixed(1)}</span>km
              </a>
            </li>
          )
        })}
      </ul>
      {note && <p className="mt-4 text-[11.5px] text-fg-4">{note}</p>}
    </div>
  )
}
