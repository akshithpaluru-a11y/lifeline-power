# LifeLine Power API contract (v1)

This is the contract the backend (`backend/`, Python FastAPI) and frontend (`frontend/`, TypeScript)
are built against **in parallel**. Treat it as frozen:

- Additive changes are fine (extra optional response fields, extra endpoints).
- Breaking changes (renaming/removing fields, changing types or URLs) are not. If one seems
  necessary, keep the contract shape and write the reason in `docs/CONTRACT_NOTES.md`.

Conventions

- Base path `/api`. JSON everywhere, `snake_case` keys.
- Timestamps are ISO 8601 UTC strings with `Z` (e.g. `"2026-10-20T18:04:11Z"`).
- Durations are in **minutes** (floats allowed) unless the field name says otherwise.
- Energy in watt-hours (`_wh`), power in watts (`_watts`).
- IDs are opaque strings (short random, URL-safe).
- Dev servers: backend on `http://localhost:8000`, frontend (Vite) on `http://localhost:5173` with
  `/api` proxied to the backend. The backend also allows CORS from any origin in dev.

Errors: non-2xx responses return
```json
{ "error": { "code": "not_found", "message": "Household not found" } }
```
Codes used: `bad_request`, `unauthorized`, `forbidden`, `not_found`, `conflict`, `unavailable`.

## Auth

No accounts or passwords. Two kinds of bearer token:

- **Owner token**: returned once when a household is created. Full read/write. The frontend keeps
  it in `localStorage`.
- **Caregiver token**: returned when a caregiver joins with the household's 6-character
  `join_code`. Read-only access to the household plus the ability to acknowledge alerts.

Send as `Authorization: Bearer <token>`. For the SSE stream (EventSource can't set headers) send
`?token=<token>` instead. Owner-only endpoints return 403 for caregiver tokens.

---

## Core objects

### DeviceTemplate (catalog entry, read-only)
```json
{
  "kind": "oxygen_concentrator_home",
  "name": "Home oxygen concentrator",
  "category": "breathing",            // breathing | feeding | mobility | monitoring | therapy | cooling | other
  "icon": "oxygen",                   // icon key, see list below
  "life_sustaining_default": true,
  "typical_draw_watts": 300,
  "saver_draw_watts": 180,            // null if no lower-power mode
  "saver_tip": "Ask your care team ahead of time if a lower flow setting is safe for you.",  // null when the device has no saver mode
  "internal_battery_wh": 0,           // 0 = no internal battery
  "duty_cycle": 1.0,                  // fraction of time the device is actually drawing power
  "ac_powered": true,                 // needs an AC outlet (backup must have an AC inverter)
  "description": "Stationary concentrators plug into the wall and have no battery."
}
```
Icon keys (the frontend has an icon for each): `oxygen`, `oxygen_portable`, `ventilator`, `cpap`,
`suction`, `feeding_pump`, `infusion_pump`, `wheelchair`, `nebulizer`, `fridge`, `dialysis`,
`monitor`, `bed`, `device` (generic).

Catalog kinds (backend must provide all of these; values are typical figures and every one is
editable per device): `oxygen_concentrator_home`, `oxygen_concentrator_portable`, `home_ventilator`,
`cpap_bipap`, `suction_machine`, `enteral_feeding_pump`, `infusion_pump`, `power_wheelchair`,
`nebulizer`, `medication_fridge`, `home_dialysis`, `pulse_oximeter_monitor`,
`pressure_mattress_pump`, `custom`.

### Device (a device in a household)
```json
{
  "id": "dv_7Kx2",
  "kind": "home_ventilator",
  "label": "Dad's ventilator",
  "icon": "ventilator",
  "life_sustaining": true,
  "draw_watts": 30,
  "saver_draw_watts": null,
  "saver_enabled": false,             // household chose to use saver mode during outages
  "duty_cycle": 1.0,
  "ac_powered": true,
  "internal_battery_wh": 90,
  "spare_batteries_wh": 90,           // extra batteries that fit only this device
  "charge_percent": 100,              // last known charge of internal battery, 0-100
  "notes": ""
}
```

### BackupSource (shared power in the house)
```json
{
  "id": "bk_19aQ",
  "type": "power_station",            // power_station | ups | generator | car_inverter
  "label": "Jackery 1000",
  "capacity_wh": 1002,                // usable energy; for generator: null
  "max_output_watts": 1500,
  "has_ac_outlet": true,
  "charge_percent": 100,              // null for generator / car_inverter
  "fuel_hours": null                  // generator / car_inverter only: hours per tank at typical load
}
```

### Caregiver
```json
{ "id": "cg_P0p1", "name": "Maya", "relationship": "Daughter", "joined_at": "2026-10-20T18:00:00Z" }
```

### Household
```json
{
  "id": "hh_a81Kd",
  "name": "The Reyes home",
  "patient_first_name": "Luis",
  "address": "123 Elm St, Omaha, NE 68102",   // optional, free text
  "lat": 41.2565, "lon": -95.9345,            // optional
  "state": "NE",                              // optional 2-letter
  "utility_name": "OPPD",                     // optional
  "medical_program_registered": false,
  "join_code": "K7Q2MX",
  "armed": true,                              // outage alarm turned on for this home
  "devices": [ /* Device */ ],
  "backups": [ /* BackupSource */ ],
  "caregivers": [ /* Caregiver */ ],
  "created_at": "2026-10-20T17:55:00Z"
}
```

### Plan (what the batteries can do, computed by the backend)
```json
{
  "computed_at": "2026-10-20T18:04:11Z",
  "assumes_saver_mode": false,
  "critical_runtime_minutes": 372.5,      // shortest runtime among life-sustaining devices (null if none)
  "bottleneck_device_id": "dv_7Kx2",       // the life-sustaining device that runs out first
  "devices": [
    {
      "device_id": "dv_7Kx2",
      "effective_draw_watts": 30.0,
      "internal_minutes": 180.0,           // internal + spare batteries only
      "total_minutes": 372.5,              // after backup power is shared out
      "allocations": [                      // where the extra time comes from
        { "backup_id": "bk_19aQ", "energy_wh": 96.3, "minutes": 192.5 }
      ],
      "status": "ok"                        // ok | limited | no_backup (runs only while grid power is on)
    }
  ],
  "backups": [
    { "backup_id": "bk_19aQ", "usable_wh": 851.7, "allocated_wh": 851.7, "unused_wh": 0.0 }
  ],
  "recommendations": [
    {
      "id": "saver_cpap",
      "kind": "saver_mode",                 // saver_mode | add_backup | charge | register_program | caregiver | location
      "title": "Turn off the CPAP humidifier during an outage",
      "detail": "Cuts its power use from 55 W to 15 W.",
      "gain_minutes": 140.0                 // extra critical runtime if followed; null if not applicable
    }
  ],
  "assumptions": [
    "AC devices on a power station lose about 15% to the inverter."
  ]
}
```
Planner rules the backend implements (documented so the UI can explain them):
1. Effective draw = (saver draw if `saver_enabled` and available, else draw) x duty cycle.
2. Each device first uses its own internal + spare batteries (x charge %, 90% usable).
3. Shared backups (power stations, UPS) are split to **maximize the shortest runtime among
   life-sustaining devices** (water-filling), then non-critical devices get what is left. AC devices
   lose 15% to the inverter; a backup can't power an AC device if `has_ac_outlet` is false, or any
   device whose draw exceeds `max_output_watts`.
4. Generators and car inverters count as `fuel_hours` of power for any device they can run
   (refueling extends this). They always add a carbon-monoxide safety recommendation.
5. A device with no battery and no usable backup gets `status: "no_backup"` and `total_minutes: 0`.

### Outage
```json
{
  "id": "og_55Hk",
  "status": "active",                       // active | ended
  "source": "charging_lost",                // charging_lost | manual | drill
  "drill": false,                           // true when source is "drill" (convenience flag)
  "started_at": "2026-10-20T18:04:11Z",
  "ended_at": null,
  "plan": { /* Plan snapshot, recomputed on check-in */ },
  "milestones": [
    {
      "key": "leave_by",                    // see list below
      "at": "2026-10-20T23:16:41Z",
      "title": "Leave for a powered location",
      "detail": "Head to Nebraska Medical Center (12 min away) before batteries get low.",
      "severity": "warning",                // info | warning | critical
      "passed": false
    }
  ],
  "acks": [
    { "caregiver_id": "cg_P0p1", "name": "Maya", "message": "On my way with the generator", "eta_minutes": 25, "at": "2026-10-20T18:06:00Z" }
  ],
  "last_check_in_at": "2026-10-20T18:04:11Z",
  "speed": 1                                // 1 for real outages; drills may run faster (see below)
}
```
**Drill speed.** `speed` (1 to 600, default 1) is accepted only when `source` is `"drill"`. It
compresses time so a 6-hour countdown can be demoed in minutes: battery drain, milestone `at` times
and the server ticker all run `speed` times faster. Clients counting down locally must do the same
(remaining = (milestone.at - now) as returned; the server already compresses `at`).
Milestone keys: `power_lost` (at start, info), `conserve` (start, info), `check_in` (next hourly
check-in), `halfway` (50% of critical runtime, warning), `leave_by` (critical runtime minus travel
time to nearest help site minus 30 min buffer, warning; travel defaults to 30 min if unknown),
`critical_low` (30 min of critical runtime left, critical: "Call 911 if you can't reach power"),
`depleted` (critical runtime reached, critical). `drill` outages produce the same milestones but
caregiver events are marked `drill: true`.

### HelpSite
```json
{
  "id": "osm_node_123",
  "name": "Nebraska Medical Center",
  "kind": "hospital",                       // hospital | fire_station | library | community_centre | police | shelter | other
  "lat": 41.255, "lon": -95.976,
  "distance_km": 3.4,
  "travel_minutes": 12,                     // rough driving estimate
  "likely_has_power": true,                 // hospitals/fire stations have backup generators
  "address": "987400 Nebraska Medical Center, Omaha, NE"   // null when OpenStreetMap has no address
}
```

---

## Endpoints

### Meta
| Method | Path | Auth | Returns |
|---|---|---|---|
| GET | `/api/health` | none | `{ "ok": true, "version": "1.0.0" }` |
| GET | `/api/catalog/devices` | none | `{ "devices": DeviceTemplate[] }` |
| GET | `/api/catalog/backups` | none | `{ "backups": [{ "type", "name", "icon", "typical_capacity_wh", "typical_max_output_watts", "description" }] }` (icon keys: `power_station`, `ups`, `generator`, `car`) |

### Households
| Method | Path | Auth | Body | Returns |
|---|---|---|---|---|
| POST | `/api/households` | none | `{ name, patient_first_name, address?, lat?, lon?, state?, utility_name? }` | `201 { household: Household, owner_token }` |
| GET | `/api/households/{id}` | owner or caregiver | | `{ household: Household }` |
| PATCH | `/api/households/{id}` | owner | any of `name, patient_first_name, address, lat, lon, state, utility_name, medical_program_registered, armed` | `{ household }` |
| DELETE | `/api/households/{id}` | owner | | `204` |
| POST | `/api/households/{id}/join-code/rotate` | owner | | `{ join_code }` |

### Devices and backups
| Method | Path | Auth | Body | Returns |
|---|---|---|---|---|
| POST | `/api/households/{id}/devices` | owner | `{ kind, label?, ...any Device field to override template }` | `201 { device: Device }` (unspecified fields filled from the template) |
| PATCH | `/api/households/{id}/devices/{device_id}` | owner | any Device field except `id`, `kind` | `{ device }` |
| DELETE | `/api/households/{id}/devices/{device_id}` | owner | | `204` |
| POST | `/api/households/{id}/backups` | owner | `{ type, label?, capacity_wh?, max_output_watts?, has_ac_outlet?, charge_percent?, fuel_hours? }` | `201 { backup: BackupSource }` |
| PATCH | `/api/households/{id}/backups/{backup_id}` | owner | any BackupSource field except `id` | `{ backup }` |
| DELETE | `/api/households/{id}/backups/{backup_id}` | owner | | `204` |

### Planning
| Method | Path | Auth | Returns |
|---|---|---|---|
| GET | `/api/households/{id}/plan?saver=true\|false` | owner or caregiver | `{ plan: Plan }` (`saver=true` previews every device in saver mode) |

### Outages
| Method | Path | Auth | Body | Returns |
|---|---|---|---|---|
| POST | `/api/households/{id}/outages` | owner | `{ source: "charging_lost" \| "manual" \| "drill", speed?: number }` | `201 { outage: Outage }`; `409 conflict` if one is already active |
| GET | `/api/households/{id}/outages/current` | owner or caregiver | | `{ outage: Outage \| null }` |
| POST | `/api/households/{id}/outages/current/check-in` | owner | `{ ok: true, devices?: [{ device_id, charge_percent }] }` | `{ outage }` (plan and milestones recomputed from the new charge levels) |
| POST | `/api/households/{id}/outages/current/ack` | caregiver | `{ message?, eta_minutes? }` | `{ outage }` |
| POST | `/api/households/{id}/outages/current/end` | owner | | `{ outage }` (status `ended`) |
| GET | `/api/households/{id}/outages` | owner | | `{ outages: Outage[] }` (history, newest first, without `plan`) |

### Caregivers
| Method | Path | Auth | Body | Returns |
|---|---|---|---|---|
| POST | `/api/caregivers/join` | none | `{ join_code, name, relationship? }` | `201 { caregiver: Caregiver, caregiver_token, household: Household }` |
| DELETE | `/api/households/{id}/caregivers/{caregiver_id}` | owner | | `204` |

### Live events (Server-Sent Events)
`GET /api/households/{id}/stream?token=<owner or caregiver token>`

`text/event-stream`. Each message has an `event:` name and a single-line JSON `data:` (one `data:` line per message). A `: ping` comment every
15 s keeps the connection open. On connect the server immediately sends one `snapshot`.

| event | data |
|---|---|
| `snapshot` | `{ household: Household, outage: Outage \| null }` |
| `outage_started` | `{ outage: Outage, drill: bool }` |
| `milestone` | `{ outage_id, milestone: Milestone, drill: bool }` (sent when a milestone's time passes) |
| `check_in` | `{ outage: Outage }` |
| `missed_check_in` | `{ outage_id, minutes_overdue, drill: bool }` (owner hasn't checked in 15 min after a `check_in` milestone) |
| `ack` | `{ outage_id, ack: Ack }` |
| `outage_ended` | `{ outage: Outage, drill: bool }` |
| `household_updated` | `{ household: Household }` |

### Help and preparation
| Method | Path | Auth | Returns |
|---|---|---|---|
| GET | `/api/households/{id}/help-sites?lat=&lon=&radius_km=15` | owner or caregiver | `{ sites: HelpSite[], source: "openstreetmap" \| "fallback", note? }` (lat/lon default to the household's) |
| GET | `/api/utilities/medical-programs?state=NE&utility=OPPD` | none | `{ programs: [{ utility, states, program_name, what_it_does, how_to_apply, url, verified_at }], generic_advice: string[] }` |
| GET | `/api/households/{id}/medical-letter` | owner | `{ subject, body }` (letter to the utility asking to be registered as a medical/life-support customer, with a blank for the clinician's signature) |
| GET | `/api/households/{id}/emergency-card` | owner or caregiver | `{ patient_first_name, address, devices: [{ label, life_sustaining, draw_watts, internal_minutes }], critical_runtime_minutes, caregivers: [{ name, relationship }], utility_name, medical_program_registered, instructions: string[] }` |

### AI device helper (optional)
`POST /api/ai/identify-device` (no auth), body either `{ text: "Inogen One G5" }` or multipart
`image` (photo of the device's label). Returns
```json
{
  "match": { "kind": "oxygen_concentrator_portable", "label": "Inogen One G5", "draw_watts": 28, "internal_battery_wh": 99 },
  "confidence": "high",          // high | medium | low
  "source": "ai",                // ai | catalog (keyword match fallback when no API key is configured)
  "explanation": "Label shows Inogen One G5; 8-cell battery is about 99 Wh."
}
```
Uses Claude when `ANTHROPIC_API_KEY` is set; otherwise falls back to keyword matching against the
catalog and returns `source: "catalog"`.
