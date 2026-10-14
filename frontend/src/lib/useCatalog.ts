import { useEffect, useState } from 'react'
import { api } from './api'
import { BACKUP_CATALOG, DEVICE_CATALOG } from './catalog'
import type { BackupTemplate, DeviceTemplate } from './types'

let cache: { devices: DeviceTemplate[]; backups: BackupTemplate[] } | null = null

export function useCatalog() {
  const [c, setC] = useState(cache ?? { devices: DEVICE_CATALOG, backups: BACKUP_CATALOG })
  useEffect(() => {
    if (cache) return
    Promise.all([api.catalogDevices(), api.catalogBackups()])
      .then(([d, b]) => { cache = { devices: d.devices, backups: b.backups }; setC(cache) })
      .catch(() => {})
  }, [])
  return c
}
