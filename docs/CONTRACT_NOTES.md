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
| `POST /api/caregivers/join` | `429`, code `unavailable` | Too many wrong join codes: 10 per 15 minutes from one client (an IP, or an IPv6 /64), or 200 per 15 minutes overall. Successful joins don't count. While over the limit even the right code gets 429. Tunable with `LIFELINE_JOIN_ATTEMPTS_PER_CLIENT`, `LIFELINE_JOIN_ATTEMPTS_OVERALL`, `LIFELINE_JOIN_ATTEMPT_WINDOW_SECONDS`. Behind a reverse proxy, run uvicorn with `--proxy-headers --forwarded-allow-ips` so it sees real client IPs. |

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
- In `PATCH`, `lat`/`lon`/`state` equal to the saved values count as not sent, so sending the
  whole object back with a new address re-geocodes it. The response and `household_updated` carry
  the committed household, including changes other requests made in the meantime.
- In `PATCH`, `null` clears `address`, `lat`/`lon` (together), `state` and `utility_name`. `null`
  for `name`, `patient_first_name`, `medical_program_registered` or `armed` is a 400.
- Join codes are case-insensitive and ignore spaces and dashes (`k7q-2mx` works). Unknown code: 404.
- Limits per household: 20 devices, 8 backups, 20 caregivers (409 beyond that), enforced even
  under concurrent requests.
- Removing a caregiver also gives the household a new `join_code` (sent to the owner and the
  remaining caregivers in `household_updated`), so the removed person can't simply rejoin. A join
  sent while the code changes gets 404.
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
  `limited` if its own battery or a generator still does. `status_detail` says why. A device left
  off only because other non-life-sustaining devices would run out sooner is `ok`.
- A device left off a backup that is already fully loaded (combined `max_output_watts`) gets
  `status_detail` "<backup label> is already fully loaded by other devices".
  `critical_runtime_minutes` can be 0 when life-sustaining devices together exceed a backup's limit.
- For power stations and UPS units, `Plan.backups[].usable_wh/allocated_wh/unused_wh` and
  `allocations[].energy_wh` are energy taken **out of the battery**, before the 15% inverter loss
  for AC devices (so minutes = energy_wh x 0.85 / effective_draw_watts for an AC device). The
  example in API.md (usable 851.7 Wh from a 1002 Wh station) is illustrative only.
- `gain_minutes` is not clamped; a saver-mode suggestion that would shorten the critical runtime is
  not shown at all.
- New `oxygen_concentrator_home` devices get `saver_draw_watts: null` (many home units have a fixed
  compressor); its `saver_tip` still carries the care-team caveat for any figure a family enters.

**Outages**
- `charging_lost` needs the household to be `armed` (409 otherwise). `manual` and `drill` always work.
- Starting a real outage (`charging_lost` or `manual`) while a drill is running ends the drill
  first: streams get `outage_ended` for the drill, then `outage_started`. A drill can't start
  while a real outage is active (409).
- `power_lost` and `conserve` are `passed: true` as soon as the outage starts, because
  `outage_started` already announces them. Any other milestone that is due immediately (for
  example `leave_by` when there is too little battery) gets its `milestone` event within about two
  seconds.
- `leave_by` uses the nearest hospital, fire station or police station by travel time, never a
  library or community centre. If none is within 15 km it searches 50 km; if still none, it allows
  the drive to the edge of the area searched (about 32 min for 15 km, 100 min for 50 km). It
  assumes 30 minutes only when the household has no location or OpenStreetMap doesn't answer
  within 3 seconds. The place is chosen when the outage starts and kept for its check-ins.
- `power_lost` and `conserve` keep their original text when a check-in rebuilds the timeline.
  When nothing can power a life-sustaining device, `power_lost` says so and to call 911.
- At a check-in the battery levels are estimated from the time since the last plan, then any
  reported levels replace the estimates. A device reading **higher** than the estimate means the
  device was running from something else (usually a power station or UPS it is plugged into), so
  that energy, inverter loss included, is taken from the shared batteries it could use unless the
  owner reported those batteries too. Readings never raise a shared battery's estimate. Device settings changed during the outage (for example
  saver mode switched on) are used from then on. Check-in levels belong to the outage; they don't
  change the household's saved devices.
- `ok: false` is stored as `last_check_in_ok: false` and included in the `check_in` event; the
  plan is still recomputed.
- `missed_check_in` repeats every further 15 minutes until the owner checks in. After a gap in the
  server's ticker only one alert goes out (with the real `minutes_overdue`), not a burst.
- `outage.acks` keeps each caregiver's 5 most recent acknowledgements; older ones from that
  caregiver are dropped. The `ack` event is unchanged.
- Milestone times are capped about 10 years out, so enormous runtimes can't overflow.
- `GET .../outages` returns the 200 most recent outages.
- Ending an outage leaves its milestones as they were.

**Live events**
- Removing a caregiver closes their open streams; deleting a household closes all of its streams.
  A stream that connects at the moment its caregiver is removed ends without a snapshot.
- A stream that falls 256 events behind is closed so the client reconnects and gets a fresh
  snapshot, instead of silently losing events.

**Help and preparation**
- `help-sites`: empty `lat=`, `lon=` or `radius_km=` count as not given. `lat` and `lon` must be given together; `radius_km` is 1 to 50. With no location
  at all the answer is `{ sites: [], source: "fallback", note }` with a note asking for one.
- `medical-programs`: an unknown state code is a 400. With `utility`, only that utility's
  programs are returned, so families are never pointed at another company's program. Long names
  match only as the start of the typed name ("Entergy Louisiana LLC" matches Entergy; "Tacoma City
  Light" does not match Seattle City Light). Names that sister companies in other states share
  (National Grid, Ameren, Dominion, CenterPoint, PacifiCorp, PSEG / PSE&G, City Light) match only
  when `state` is given. The same matching drives the plan's register_program step and the letter.
- `help-sites`: an Overpass answer that reports a runtime error comes back as `source: "fallback"`
  and isn't cached, so the next lookup retries.
- `emergency-card`: the runtime line reads "At the charge levels last saved in the app, X lasts
  about Y". Under a minute of runtime, it says the device has no battery or backup power and to
  go somewhere with power right away or call 911.
- Geocoding service errors aren't remembered, so the next save retries; a slow Census lookup is
  abandoned after 2.5 s so Nominatim still gets a turn.
- `medical-letter` names the utility's verified program and links its page when the household's
  `utility_name` matches one. The letter is dated in UTC.

**AI device helper**
- A photo needs `ANTHROPIC_API_KEY`. Without one (or if Claude can't answer) a photo-only request
  is a 503 `unavailable`; with `text` as well, the text is matched against the catalog instead.
  Photos must be JPEG, PNG, GIF or WebP under 5 MB (400 otherwise). `text` is at most 500
  characters in JSON and multipart alike; JSON bodies over 16 KB are a 400.
- Figures Claude returns are checked against plausible ranges for that device kind: a draw that is
  too low becomes the catalog's typical draw, a draw that is too high is capped at the kind's
  maximum, a battery that is too large becomes the typical battery. Any correction lowers
  `confidence` to `low` and the explanation says to check the label.
- A typed name, or Claude's reading of a label, that matches a model with checked figures (e.g.
  Trilogy 100, Inogen One G5, Inogen At Home, EverFlo, Astral, AirSense 10, SimplyGo) is answered
  from that list. Typed names don't call Claude at all (`source: "catalog"`). Portable
  concentrator figures assume the highest setting, so runtimes err short.
- A photo's type is read from its bytes, not the declared content type. Bytes that aren't JPEG,
  PNG, GIF or WebP are a 400, and a photo Claude can't read is a 400 ("We couldn't read that
  photo"), not a 503.
