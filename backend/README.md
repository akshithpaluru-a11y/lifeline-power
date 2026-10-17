# LifeLine Power backend

The API behind LifeLine Power: it works out how long every medical device in a home can keep
running when the grid goes down, runs the live outage countdown, alerts caregivers in real time,
and finds nearby places that are likely to have power. It implements the contract in
[`docs/API.md`](../docs/API.md); anything added on top of it is listed in
[`docs/CONTRACT_NOTES.md`](../docs/CONTRACT_NOTES.md).

Stack: Python 3.12+ (developed on 3.13), FastAPI, Pydantic v2, SQLAlchemy 2 (async) on SQLite,
httpx, sse-starlette, the official Anthropic SDK, and pytest. All versions are pinned in
`requirements*.txt` and `pyproject.toml`.

## Setup

```bash
cd backend
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt   # or requirements.txt to run only
```

## Run

```bash
.venv/bin/uvicorn app.main:app --reload --port 8000
```

The database file (`backend/lifeline.db` by default) and its tables are created on first start.
Interactive API docs are at <http://localhost:8000/docs>; click **Authorize** and paste a token.

To try it straight away, create a demo household: Luis, a grandfather in Omaha on a home
ventilator with a spare battery, who also uses an oxygen concentrator and a CPAP; the family has a
portable power station, and his granddaughter Maya is a caregiver.

```bash
.venv/bin/python -m app.seed
```

It prints the household id, the owner and caregiver tokens, the join code and ready-to-paste
`curl` commands. A quick tour (replace the ids and tokens):

```bash
H=http://localhost:8000/api/households/<household id>
curl -H "Authorization: Bearer <owner token>" $H/plan
curl -N "$H/stream?token=<caregiver token>"            # live events; leave it running
curl -X POST -H "Authorization: Bearer <owner token>" -H 'Content-Type: application/json' \
     -d '{"source":"drill","speed":300}' $H/outages      # a 6-hour countdown in about a minute
```

## Test and lint

```bash
.venv/bin/pytest          # unit, API, ticker and live-server SSE tests
.venv/bin/ruff check .    # lint (rules in pyproject.toml)
.venv/bin/ruff format .   # format
.venv/bin/mypy            # strict type checking of app/ and tests/
```

Tests never touch the network: OpenStreetMap, the Census geocoder and Claude are mocked (respx
and a fake SDK client), and a fake clock drives every timing test, so drills don't need real
waiting. The event-stream tests run a real uvicorn server on a random port, because the
in-process test transport buffers whole responses.

## Configuration

Environment variables (or a `.env` file in the working directory):

| Variable | Default | Meaning |
|---|---|---|
| `LIFELINE_DB` | `backend/lifeline.db` | SQLite database file |
| `LIFELINE_CORS_ORIGINS` | `*` | Comma-separated allowed origins |
| `LIFELINE_STATIC_DIR` | unset | Built web app (`frontend/dist`) to serve at `/`, so app and API share one origin |
| `ANTHROPIC_API_KEY` | unset | Enables Claude for the device helper (photos need it) |
| `ANTHROPIC_MODEL` | `claude-haiku-5-5` | Model used by the device helper |
| `LIFELINE_AI_TIMEOUT_SECONDS` | `30` | Timeout for a Claude request |
| `LIFELINE_TICKER_ENABLED` | `true` | Run the background outage ticker |
| `LIFELINE_TICKER_INTERVAL_SECONDS` | `2` | How often the ticker checks active outages |
| `LIFELINE_SSE_PING_SECONDS` | `15` | Keep-alive comment interval on event streams |
| `LIFELINE_OVERPASS_URL` | `https://overpass-api.de/api/interpreter` | OpenStreetMap Overpass API |
| `LIFELINE_NOMINATIM_URL` | `https://nominatim.openstreetmap.org` | OpenStreetMap geocoder |
| `LIFELINE_CENSUS_GEOCODER_URL` | `https://geocoding.geo.census.gov/geocoder` | US Census geocoder |
| `LIFELINE_HTTP_TIMEOUT_SECONDS` | `8` | Timeout for each map-service request |
| `LIFELINE_JOIN_ATTEMPTS_PER_CLIENT` | `10` | Wrong join codes allowed per client (IP or IPv6 /56) per window |
| `LIFELINE_JOIN_ATTEMPTS_OVERALL` | `200` | Wrong join codes allowed from everyone together per window |
| `LIFELINE_JOIN_ATTEMPT_WINDOW_SECONDS` | `900` | Join-code rate-limit window |
| `LIFELINE_USER_AGENT` | `LifeLinePower/1.0 (...)` | Sent to map services, as their usage policies ask |

## Architecture

```
app/
  main.py            app factory: lifespan (database, HTTP client, services, ticker), CORS, errors
  config.py          settings from the environment
  context.py         the long-lived services every request shares
  api/               HTTP routes, one module per area of the contract, plus deps.py (sessions,
                     who is calling, and access checks)
  services/          the operations behind the routes
    households.py    households, equipment, caregivers, join codes; planner inputs
    equipment.py     catalog defaults and consistency rules for devices and backups
    outages.py       start, check-in, ack, end, history; per-household locks
  planner.py         runtime planner (pure functions, no I/O)
  outage_engine.py   milestones, drills, check-ins and battery estimates (pure functions)
  ticker.py          background task that announces milestones and missed check-ins
  events.py          in-process publish/subscribe for the live event streams
  catalog.py         typical device and backup figures, each with its source
  utilities_data.py  utility medical-customer programs, each checked on the utility's own site
  help_sites.py      nearby places with power, from OpenStreetMap (cached, with a fallback)
  geocoding.py       address -> coordinates (Census, then Nominatim), cached and rate-limited
  letters.py         the utility letter and the emergency card
  ai.py              device identification: Claude when configured, catalog matching otherwise
  auth.py            tokens (only SHA-256 hashes are stored) and access rules
  models.py, db.py   SQLite tables and engine
  schemas.py         request and response models, matching docs/API.md
  seed.py            demo household
```

A request flows route -> service -> pure core (`planner`, `outage_engine`) -> database. The core
modules never touch the database, the network or the clock, which is what makes the maths easy
to test with hand-checked numbers.

Some decisions worth knowing:

- **Live events.** Every change is committed first and published second, so a stream never shows
  something that was rolled back. A stream subscribes before it reads its snapshot, so nothing
  that happens in between is lost. Each message is one `event:` line and one `data:` line of
  compact JSON. Removing a caregiver closes their stream straight away.
- **No lost updates.** The ticker and API requests both change outages. All outage changes take
  a per-household lock, and the outage row carries a version counter as a second line of defence
  (it would catch a clash if the app ever ran as several processes; the API turns one into a 409).
- **Outside services fail soft.** Map lookups have timeouts, caches and a fallback answer, and are
  given a short time budget wherever a request waits on them. An outage starts within about
  three seconds even if OpenStreetMap is unreachable; it then assumes a 30-minute drive.
- **Tokens.** Owner and caregiver tokens are 256-bit random strings; only their SHA-256 hashes are
  stored, and `?token=` values are redacted from the access log.

## How the planner works

The planner answers: *if the power went out now, how long would each device keep running, and
what would help most?* The rules, which the app also shows to families:

1. **Average draw, except where averaging is unsafe.** A device draws its normal watts (or its
   saver watts, if saver mode is on and lower), times its duty cycle. A fridge that runs a third
   of the time at 60 W averages 20 W. But a life-sustaining device that runs in long sessions (a
   BiPAP someone depends on, a dialysis cycler) may have to run continuously once the power is
   out, so it is counted at its full running watts. Only kinds that really run in short bursts
   (suction, nebulizer, power wheelchair, medication fridge) are averaged.
2. **Its own batteries first.** Internal battery x charge %, plus spare batteries (assumed full),
   and only 90% of that counts as usable, because batteries cut off before they are empty.
3. **Shared batteries are shared fairly.** Power stations and UPS units are split to make the
   *shortest* runtime among life-sustaining devices as long as possible. AC devices lose 15% to
   the inverter. A backup can't run an AC device without an AC outlet, and the devices it runs
   together must fit within its maximum output, so life-sustaining devices are plugged in first
   (choosing the set that keeps the shortest life-sustaining runtime longest: every arrangement
   is compared for a typical home, a step-by-step search for bigger ones). Non-life-sustaining
   devices share whatever is left. The plan lists what goes into each backup, and during an
   outage every re-plan keeps that arrangement, so the countdown never jumps because the app
   quietly assumed someone moved a plug.
4. **Fuel.** Generators and car inverters add one tank's run time (`fuel_hours`) to every device
   they can run at once, life-sustaining devices first, up to their output limit. They always
   come with a carbon-monoxide warning.
5. **No backup.** A device with no battery and nothing that can run it gets `no_backup` and 0
   minutes.

Rule 3 is the interesting part. Picture each device as a glass of water whose level is its
runtime: its own batteries and fuel fill it to a starting level, and shared battery energy is
poured into the lowest glasses first so their levels rise together (*water-filling*). The catch is
that not every battery can reach every device. Hall's marriage theorem gives the exact limit: a
common level is reachable only if, for every set of batteries, the devices that can use *only*
those batteries need no more energy than the set holds. The planner checks every set (a home has
a handful of batteries), finds the tightest one, fixes those devices at that level, and repeats
for the rest. Each step solves a piecewise-linear equation for the level. A max-flow pass
(Edmonds-Karp) then works out which battery actually feeds which device, which becomes the plan's
`allocations`.

Worked example: a 350 W oxygen concentrator and a 20 W ventilator (no battery of its own) share a
1000 Wh power station. Both are AC, so 850 Wh reaches them, and they run out together after
850 Wh / 370 W = 137.8 minutes. Give the ventilator its 95 Wh battery instead and it lasts
256.5 minutes on its own (95 x 0.9 / 20 W), so the concentrator gets the whole station:
850 Wh / 350 W = 145.7 minutes. Both cases are in `tests/test_planner.py`.

Recommendations come from re-running the planner with each change (saver mode on a device, a
1000 Wh power station added, batteries charged), so each `gain_minutes` is the real effect on the
first life-sustaining device to run out, not a rule of thumb. Then come the steps that don't add
runtime but matter: registering with the utility's medical program (using the verified program
list when the utility is known), inviting a caregiver, and adding a location.

### Outages

When an outage starts, the plan becomes a timeline: `halfway`, `leave_by` (runtime minus the drive
to the nearest hospital, fire or police station minus a 30-minute buffer), `critical_low` (30
minutes left) and `depleted`, plus an hourly `check_in`. If the battery can't even last the drive,
`leave_by` becomes a critical "call 911 now". A check-in estimates where every battery is now
(fuel first, then each device's own batteries, then its share of the shared batteries), applies
any levels the owner reported, and re-plans, so a check-in without new readings leaves the
countdown where it was. A device reported fuller than estimated was really running off a shared
battery (say, a ventilator plugged into a power station keeps its own battery topped up), so that
energy is taken from the shared battery instead of vanishing. Drills run the same timeline
`speed` times faster.

## Data sources

Every figure in `catalog.py` has a comment naming its source (maker spec sheets and manuals,
USDA, AFDC). Every program in `utilities_data.py` was read on the utility's own page on
2026-10-14, and its `url` is that page. Figures are typical values that families can and should
replace with their own devices' numbers.
