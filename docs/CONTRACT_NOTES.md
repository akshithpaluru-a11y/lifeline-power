# Contract notes (backend)

Everything here is additive or an interpretation of something `API.md` leaves open. Nothing in
the contract was renamed, removed or retyped.

## Extra response fields

| Where | Field | Meaning |
|---|---|---|
| `Plan.devices[]` | `status_detail` (string or null) | Plain-English reason for `limited` / `no_backup`, e.g. "Jackery has no AC outlet". Null when `ok`. |
| `Plan.backups[]` | `fuel_minutes` (number or null) | Generators and car inverters: run time on one tank. Null for batteries. For these backups `usable_wh` and `allocated_wh` are the energy delivered to the devices they run over that time, and `unused_wh` is 0. |
| `Recommendation.kind` | `"safety"` | One more kind: the carbon-monoxide warning (`id: "co_safety"`, `gain_minutes: null`) shown whenever there is a generator or car inverter. Safety items always come first. |
| `Outage` | `last_check_in_ok` (bool) | False when the owner's latest check-in sent `ok: false` ("I need help"). |
| `GET /api/health` | `ai_enabled` (bool) | Whether Claude is configured (photo identification needs it). |
| `GET /api/catalog/backups` items | `has_ac_outlet`, `typical_fuel_hours` | Defaults a new backup of that type gets. |
| Error responses | code `internal` | Used only for unexpected 500 errors. |

Timestamps carry milliseconds: `2026-10-20T18:04:11.250Z` (still ISO 8601 UTC with `Z`).

## Extra request options

- Check-in accepts `backups: [{ backup_id, charge_percent?, fuel_hours? }]` alongside `devices`.
- The check-in and ack bodies may be left out entirely (same as `{ "ok": true }` and `{}`).
- Unknown fields in request bodies are ignored, so a client can send back an object it received.
  `id` and `kind` sent to `PATCH .../devices/{id}` are ignored.
- `POST /api/ai/identify-device` (multipart) may send a `text` field along with `image`.

## Behaviour the contract leaves open

**Households**
- An `address` sent without `lat`/`lon` is geocoded (US Census, then OpenStreetMap Nominatim),
  best effort, within a few seconds; `lat`/`lon` without `state` get their state looked up. A
  new address replaces coordinates found from the old one. If the lookups fail the household is
  saved without them.
- In `PATCH`, `null` clears `address`, `lat`/`lon` (together), `state` and `utility_name`. `null`
  for `name`, `patient_first_name`, `medical_program_registered` or `armed` is a 400.
- Join codes are case-insensitive and ignore spaces and dashes (`k7q-2mx` works). Unknown code: 404.
- Limits per household: 20 devices, 8 backups, 20 caregivers (409 beyond that).
- A token for another household gets 403, whether or not the requested household exists.

**Devices and backups**
- `cpap_bipap` defaults to `life_sustaining: false`; families can switch it on.
- A catalog `saver_draw_watts` that isn't lower than the device's own `draw_watts` is dropped
  (set to null). Sending a `saver_draw_watts` that isn't lower than `draw_watts` is a 400.
- Power stations and UPS units always have `capacity_wh` and `charge_percent` (`null` or missing
  means the catalog's typical capacity and 100%) and must not have `fuel_hours`. Generators and
  car inverters have `fuel_hours` (missing or `null` means typical) and must not have
  `capacity_wh` or `charge_percent`. Breaking these rules is a 400.
- Changing a backup's `type` resets the figures not sent in the same request (and a label that
  was just the old type's name) to the new type's typical values.

**Plan**
- Spare batteries are counted as fully charged; internal batteries at `charge_percent`.
- A non-life-sustaining device that could use a shared battery but gets none of it, because
  life-sustaining devices need all of it, is `no_backup` (0 minutes) if nothing else runs it, or
  `limited` if its own battery or a generator still does. `status_detail` says why.

**Outages**
- `charging_lost` needs the household to be `armed` (409 otherwise). `manual` and `drill` always work.
- Starting a real outage (`charging_lost` or `manual`) while a drill is running ends the drill
  first: streams get `outage_ended` for the drill, then `outage_started`. A drill can't start
  while a real outage is active (409).
- `power_lost` and `conserve` are `passed: true` as soon as the outage starts, because
  `outage_started` already announces them. Any other milestone that is due immediately (for
  example `leave_by` when there is too little battery) gets its `milestone` event within about two
  seconds.
- `leave_by` uses the nearest place likely to have power (hospital, fire or police station, else
  the nearest place found) when the household has a location and OpenStreetMap answers within 3
  seconds; otherwise it assumes 30 minutes' travel. The place is chosen when the outage starts and kept for its check-ins.
- At a check-in the battery levels are estimated from the time since the last plan, then any
  reported levels replace the estimates. Device settings changed during the outage (for example
  saver mode switched on) are used from then on. Check-in levels belong to the outage; they don't
  change the household's saved devices.
- `ok: false` is stored as `last_check_in_ok: false` and included in the `check_in` event; the
  plan is still recomputed.
- `missed_check_in` repeats every further 15 minutes until the owner checks in.
- `GET .../outages` returns the 200 most recent outages.
- Ending an outage leaves its milestones as they were.

**Live events**
- Removing a caregiver closes their open streams; deleting a household closes all of its streams.

**Help and preparation**
- `help-sites`: `lat` and `lon` must be given together; `radius_km` is 1 to 50. With no location
  at all the answer is `{ sites: [], source: "fallback", note }` with a note asking for one.
- `medical-programs`: an unknown state code is a 400. With `utility`, only that utility's
  programs are returned, so families are never pointed at another company's program.
- `medical-letter` names the utility's verified program and links its page when the household's
  `utility_name` matches one. The letter is dated in UTC.

**AI device helper**
- A photo needs `ANTHROPIC_API_KEY`. Without one (or if Claude can't answer) a photo-only request
  is a 503 `unavailable`; with `text` as well, the text is matched against the catalog instead.
  Photos must be JPEG, PNG, GIF or WebP under 5 MB (400 otherwise).
- Figures Claude returns outside sane ranges are replaced with the catalog's typical values.
