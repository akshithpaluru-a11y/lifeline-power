import { useEffect, useRef, useState } from 'react'
import { motion } from 'motion/react'
import { Camera, Leaf, Plus, ScanLine, Sparkles, Trash2 } from 'lucide-react'
import { useStore, saveDevice, deleteDevice, saveBackup, deleteBackup } from '../lib/store'
import { useCatalog } from '../lib/useCatalog'
import { humanMinutes } from '../lib/format'
import { aiEnabled, api, ApiError } from '../lib/api'
import type { BackupSource, Device } from '../lib/types'
import { BackupIcon, DeviceIcon } from '../components/DeviceIcon'
import { Segments } from '../components/Segments'
import { Sheet } from '../components/Sheet'
import { Field } from '../components/Field'
import { Button } from '../components/Button'
import { Pill } from '../components/Pill'

type DraftDevice = Partial<Device> & { kind: string }
type DraftBackup = Partial<BackupSource> & { type: BackupSource['type'] }

export default function Devices() {
  const h = useStore((s) => s.household)!
  const plan = useStore((s) => s.plan)
  const [dev, setDev] = useState<DraftDevice | null>(null)
  const [bk, setBk] = useState<DraftBackup | null>(null)

  return (
    <div className="pt-2">
      <h1 className="text-[28px] font-semibold tracking-[-0.03em]">Power</h1>
      <p className="mt-1 text-[13.5px] text-fg-3">Every device that needs electricity, and every battery that can keep it going.</p>

      <h2 className="mt-6 text-[12px] font-medium uppercase tracking-[0.14em] text-fg-3">Medical devices</h2>
      <div className="mt-3 space-y-2.5">
        {h.devices.map((d, i) => {
          const p = plan?.devices.find((x) => x.device_id === d.id)
          return (
            <motion.button key={d.id} onClick={() => setDev(d)}
              initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.05 }}
              className="surface flex w-full items-center gap-4 rounded-[24px] p-4 text-left transition hover:bg-white/[0.02] active:scale-[.99]">
              <span className={`grid size-12 shrink-0 place-items-center rounded-2xl ring-1 ring-inset ${d.life_sustaining ? 'bg-amber/10 text-amber ring-amber/20' : 'bg-white/5 text-fg-2 ring-white/10'}`}>
                <DeviceIcon icon={d.icon} size={20} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-2">
                  <span className="truncate text-[15px] font-medium">{d.label}</span>
                  {d.life_sustaining && <Pill tone="amber" className="shrink-0 !px-2 !py-0.5 text-[10px]">Critical</Pill>}
                  {d.saver_enabled && <Leaf size={13} className="shrink-0 text-live" />}
                </span>
                <span className="tnum mt-1 block text-[12.5px] text-fg-3">
                  {Math.round(p?.effective_draw_watts ?? d.draw_watts)} W · {d.internal_battery_wh + d.spare_batteries_wh ? `${d.internal_battery_wh + d.spare_batteries_wh} Wh · ${d.charge_percent}%` : 'no battery'}
                </span>
                {d.internal_battery_wh > 0 && <Segments value={d.charge_percent / 100} count={24} tone={d.charge_percent < 30 ? 'alert' : 'live'} className="mt-2.5 h-1" />}
                {p?.status_detail && <span className={`mt-2 block text-[12px] leading-snug ${p.status === 'ok' ? 'text-fg-4' : 'text-amber/90'}`}>{p.status_detail}</span>}
              </span>
              <span className="shrink-0 text-right">
                <span className={`tnum block text-[17px] font-semibold ${!p?.total_minutes ? 'text-alert' : ''}`}>{p?.total_minutes ? humanMinutes(p.total_minutes) : '0m'}</span>
                <span className={`text-[11px] ${p?.status === 'limited' ? 'text-amber/90' : 'text-fg-4'}`}>{p?.status === 'limited' ? 'limited' : p?.status === 'no_backup' ? 'no backup' : p?.allocations.length ? 'with backup' : 'on battery'}</span>
              </span>
            </motion.button>
          )
        })}
      </div>
      <Button variant="secondary" className="mt-3 w-full" icon={<Plus size={16} />} onClick={() => setDev({ kind: 'oxygen_concentrator_portable' })}>Add a device</Button>

      <h2 className="mt-8 text-[12px] font-medium uppercase tracking-[0.14em] text-fg-3">Backup power</h2>
      <div className="mt-3 space-y-2.5">
        {h.backups.map((b) => {
          const p = plan?.backups.find((x) => x.backup_id === b.id)
          return (
            <button key={b.id} onClick={() => setBk(b)} className="surface flex w-full items-center gap-4 rounded-[24px] p-4 text-left hover:bg-white/[0.02]">
              <span className="grid size-12 shrink-0 place-items-center rounded-2xl bg-sky/10 text-sky ring-1 ring-inset ring-sky/20"><BackupIcon type={b.type} size={20} /></span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[15px] font-medium">{b.label}</span>
                <span className="tnum mt-1 block text-[12.5px] text-fg-3">
                  {b.capacity_wh ? `${b.capacity_wh} Wh · ${b.charge_percent ?? 100}% · ${b.max_output_watts} W max` : `${b.fuel_hours ?? 0} h of fuel · ${b.max_output_watts} W max`}
                </span>
                {p && p.usable_wh > 0 && <Segments value={p.allocated_wh / p.usable_wh} count={24} tone="muted" className="mt-2.5 h-1" />}
              </span>
            </button>
          )
        })}
        {!h.backups.length && <p className="rounded-2xl bg-white/[0.03] p-4 text-[13px] leading-relaxed text-fg-3 ring-1 ring-inset ring-white/[0.05]">A power station, UPS, generator or car inverter adds hours. LifeLine shares it out so the life-sustaining device lasts longest.</p>}
      </div>
      <Button variant="secondary" className="mt-3 w-full" icon={<Plus size={16} />} onClick={() => setBk({ type: 'power_station' })}>Add backup power</Button>

      {plan?.assumptions.length ? <p className="mt-6 text-[11.5px] leading-relaxed text-fg-4">{plan.assumptions.join(' ')}</p> : null}

      <Sheet open={!!dev} onClose={() => setDev(null)} title={dev?.id ? 'Edit device' : 'Add a device'}>
        {dev && <DeviceForm value={dev} onDone={() => setDev(null)} />}
      </Sheet>
      <Sheet open={!!bk} onClose={() => setBk(null)} title={bk?.id ? 'Edit backup power' : 'Add backup power'}>
        {bk && <BackupForm value={bk} onDone={() => setBk(null)} />}
      </Sheet>
    </div>
  )
}

export function DeviceForm({ value, onDone, onSubmit }: { value: DraftDevice; onDone: () => void; onSubmit?: (d: DraftDevice) => void }) {
  const { devices: catalog } = useCatalog()
  const fill = (kind: string, keep?: Partial<Device>): DraftDevice => {
    const t = catalog.find((c) => c.kind === kind) ?? catalog[catalog.length - 1]
    return { kind, label: t.name, icon: t.icon, life_sustaining: t.life_sustaining_default, draw_watts: t.typical_draw_watts, saver_draw_watts: t.saver_draw_watts,
      saver_enabled: false, duty_cycle: t.duty_cycle, ac_powered: t.ac_powered, internal_battery_wh: t.internal_battery_wh, spare_batteries_wh: 0, charge_percent: 100, notes: '', ...keep }
  }
  const [d, setD] = useState<DraftDevice>(() => (value.id ? value : fill(value.kind, value)))
  const [scan, setScan] = useState('')
  const [scanNote, setScanNote] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const file = useRef<HTMLInputElement>(null)
  const [ai, setAi] = useState(false)
  useEffect(() => { aiEnabled().then(setAi) }, [])
  const set = <K extends keyof Device>(k: K, v: Device[K]) => setD((x) => ({ ...x, [k]: v }))
  const tmpl = catalog.find((c) => c.kind === d.kind)

  const applyMatch = (r: Awaited<ReturnType<typeof api.identifyDevice>>) => {
    setD((x) => ({ ...fill(r.match.kind), id: x.id, ...Object.fromEntries(Object.entries(r.match).filter(([, v]) => v != null)) }))
    setScanNote(r.confidence === 'low' ? `Check the label: ${r.explanation}` : r.explanation)
  }
  const identify = async () => {
    if (!scan.trim()) return
    setBusy(true)
    try { applyMatch(await api.identifyDevice(scan)) } catch { setScanNote('Couldn’t look that up. Pick the type below.') } finally { setBusy(false) }
  }
  const photo = async (f: File) => {
    setBusy(true)
    try { applyMatch(await api.identifyPhoto(f)) } catch (e) { setScanNote(e instanceof ApiError && e.status === 400 ? e.message : 'Couldn’t read that photo. Type the model name instead.') } finally { setBusy(false) }
  }

  const save = async () => {
    setBusy(true)
    try { if (onSubmit) onSubmit(d); else await saveDevice(d); onDone() } finally { setBusy(false) }
  }

  return (
    <div className="space-y-4">
      <div className="rounded-2xl bg-gradient-to-b from-amber/[0.08] to-transparent p-3.5 ring-1 ring-inset ring-amber/15">
        <div className="flex items-center gap-2 text-[12px] font-medium text-amber"><Sparkles size={13} /> Fill it in from the label</div>
        <div className="mt-2.5 flex gap-2">
          <input value={scan} maxLength={500} onChange={(e) => setScan(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && identify()} placeholder="e.g. Inogen One G5"
            className="h-10 min-w-0 flex-1 rounded-xl bg-white/[0.05] px-3 text-[14px] outline-none ring-1 ring-inset ring-white/10 placeholder:text-fg-4 focus:ring-amber/50" />
          <button onClick={identify} disabled={busy} className="grid size-10 place-items-center rounded-xl bg-amber text-ink-950 disabled:opacity-50" aria-label="Look up model"><ScanLine size={17} /></button>
          {ai && <button onClick={() => file.current?.click()} disabled={busy} className="grid size-10 place-items-center rounded-xl bg-white/[0.06] ring-1 ring-inset ring-white/10" aria-label="Photo of label"><Camera size={17} /></button>}
          <input ref={file} type="file" accept="image/*" capture="environment" hidden onChange={(e) => e.target.files?.[0] && photo(e.target.files[0])} />
        </div>
        {scanNote && <p className="mt-2 text-[11.5px] leading-snug text-fg-3">{scanNote}</p>}
      </div>

      <div className="-mx-1 flex gap-2 overflow-x-auto px-1 pb-1 [scrollbar-width:none]">
        {catalog.map((c) => (
          <button key={c.kind} onClick={() => setD((x) => fill(c.kind, { id: x.id, charge_percent: x.charge_percent }))}
            className={`flex w-[76px] shrink-0 flex-col items-center gap-1.5 rounded-2xl px-2 py-2.5 text-center text-[10.5px] leading-tight ring-1 ring-inset transition ${d.kind === c.kind ? 'bg-amber/10 text-amber ring-amber/40' : 'bg-white/[0.03] text-fg-2 ring-white/[0.08]'}`}>
            <DeviceIcon icon={c.icon} size={18} />{c.name.replace(/ \/.*/, '').replace('concentrator', 'O₂').replace('Home oxygen', 'Home').replace('Portable oxygen', 'Portable')}
          </button>
        ))}
      </div>
      {tmpl && <p className="-mt-1 text-[12px] text-fg-4">{tmpl.description}</p>}

      <Field label="Name" value={d.label ?? ''} onChange={(e) => set('label', e.target.value)} />
      <div className="grid grid-cols-3 gap-2.5">
        <Field label="Draw" type="number" inputMode="decimal" min={0} suffix="W" value={d.draw_watts ?? 0} onChange={(e) => set('draw_watts', +e.target.value)} />
        <Field label="Battery" type="number" inputMode="decimal" min={0} suffix="Wh" value={d.internal_battery_wh ?? 0} onChange={(e) => set('internal_battery_wh', +e.target.value)} />
        <Field label="Spares" type="number" inputMode="decimal" min={0} suffix="Wh" value={d.spare_batteries_wh ?? 0} onChange={(e) => set('spare_batteries_wh', +e.target.value)} />
      </div>
      {(d.internal_battery_wh ?? 0) > 0 && (
        <label className="block">
          <span className="mb-1.5 flex justify-between text-[12px] font-medium text-fg-3"><span>Charge right now</span><span className="tnum text-fg">{d.charge_percent}%</span></span>
          <input type="range" min={0} max={100} value={d.charge_percent ?? 100} onChange={(e) => set('charge_percent', +e.target.value)} className="w-full accent-[#FFB224]" />
        </label>
      )}
      <Toggle label="Life-sustaining" hint="Its clock is the one on the big screen" checked={!!d.life_sustaining} onChange={(v) => set('life_sustaining', v)} />
      {d.saver_draw_watts != null && (
        <Toggle label={`Low-power mode in outages (${d.saver_draw_watts} W)`} hint={tmpl?.saver_tip ?? 'Uses less power during an outage'} checked={!!d.saver_enabled} onChange={(v) => set('saver_enabled', v)} />
      )}
      <Toggle label="Plugs into a wall outlet (AC)" hint="Backups need an AC outlet to run it" checked={!!d.ac_powered} onChange={(v) => set('ac_powered', v)} />

      <div className="flex gap-2.5 pt-1">
        {d.id && !onSubmit && <Button variant="secondary" onClick={async () => { await deleteDevice(d.id!); onDone() }} aria-label="Delete device" icon={<Trash2 size={16} />} />}
        <Button className="flex-1" onClick={save} disabled={busy || !d.label?.trim()}>Save</Button>
      </div>
    </div>
  )
}

export function BackupForm({ value, onDone, onSubmit }: { value: DraftBackup; onDone: () => void; onSubmit?: (b: DraftBackup) => void }) {
  const { backups: catalog } = useCatalog()
  const fill = (type: BackupSource['type']): DraftBackup => {
    const t = catalog.find((c) => c.type === type)!
    return { type, label: t.name, capacity_wh: t.typical_capacity_wh, max_output_watts: t.typical_max_output_watts, has_ac_outlet: true, charge_percent: t.typical_capacity_wh ? 100 : null, fuel_hours: t.typical_capacity_wh ? null : 8 }
  }
  const [b, setB] = useState<DraftBackup>(() => (value.id ? value : fill(value.type)))
  const battery = b.type === 'power_station' || b.type === 'ups'
  const save = async () => { if (onSubmit) onSubmit(b); else await saveBackup(b); onDone() }
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-4 gap-2">
        {catalog.map((c) => (
          <button key={c.type} onClick={() => setB((x) => ({ ...fill(c.type), id: x.id }))}
            className={`flex flex-col items-center gap-1.5 rounded-2xl px-1 py-2.5 text-center text-[10.5px] leading-tight ring-1 ring-inset ${b.type === c.type ? 'bg-sky/10 text-sky ring-sky/40' : 'bg-white/[0.03] text-fg-2 ring-white/[0.08]'}`}>
            <BackupIcon type={c.type} size={18} />{c.name.replace('Portable ', '').replace(' battery backup', '').replace(' with inverter', '')}
          </button>
        ))}
      </div>
      <p className="-mt-1 text-[12px] text-fg-4">{catalog.find((c) => c.type === b.type)?.description}</p>
      <Field label="Name" value={b.label ?? ''} onChange={(e) => setB({ ...b, label: e.target.value })} />
      <div className="grid grid-cols-2 gap-3">
        {battery
          ? <Field label="Capacity" type="number" min={0} suffix="Wh" value={b.capacity_wh ?? 0} onChange={(e) => setB({ ...b, capacity_wh: +e.target.value })} />
          : <Field label="Fuel lasts" type="number" min={0} suffix="hours" value={b.fuel_hours ?? 0} onChange={(e) => setB({ ...b, fuel_hours: +e.target.value })} />}
        <Field label="Max output" type="number" min={0} suffix="W" value={b.max_output_watts ?? 0} onChange={(e) => setB({ ...b, max_output_watts: +e.target.value })} />
      </div>
      {battery && (
        <label className="block">
          <span className="mb-1.5 flex justify-between text-[12px] font-medium text-fg-3"><span>Charge right now</span><span className="tnum text-fg">{b.charge_percent ?? 100}%</span></span>
          <input type="range" min={0} max={100} value={b.charge_percent ?? 100} onChange={(e) => setB({ ...b, charge_percent: +e.target.value })} className="w-full accent-[#7CB7FF]" />
        </label>
      )}
      <Toggle label="Has an AC outlet" hint="Needed for devices that plug into the wall" checked={!!b.has_ac_outlet} onChange={(v) => setB({ ...b, has_ac_outlet: v })} />
      {!battery && <p className="rounded-2xl bg-amber/[0.07] p-3 text-[12.5px] leading-snug text-amber ring-1 ring-inset ring-amber/15">Run generators outdoors only, far from windows. Carbon monoxide kills.</p>}
      <div className="flex gap-2.5 pt-1">
        {b.id && !onSubmit && <Button variant="secondary" onClick={async () => { await deleteBackup(b.id!); onDone() }} aria-label="Delete" icon={<Trash2 size={16} />} />}
        <Button className="flex-1" onClick={save} disabled={!b.label?.trim()}>Save</Button>
      </div>
    </div>
  )
}

export function Toggle({ label, hint, checked, onChange }: { label: string; hint?: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <button type="button" onClick={() => onChange(!checked)} className="flex w-full items-center justify-between gap-3 rounded-2xl bg-white/[0.04] px-4 py-3 text-left ring-1 ring-inset ring-white/[0.08]">
      <span>
        <span className="block text-[14px]">{label}</span>
        {hint && <span className="block text-[11.5px] text-fg-4">{hint}</span>}
      </span>
      <span className={`relative h-6 w-10 shrink-0 rounded-full transition ${checked ? 'bg-amber' : 'bg-white/15'}`}>
        <span className={`absolute top-0.5 size-5 rounded-full bg-white shadow transition-all ${checked ? 'left-[18px]' : 'left-0.5'}`} />
      </span>
    </button>
  )
}
