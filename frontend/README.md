# LifeLine Power frontend

React + TypeScript + Vite + Tailwind CSS progressive web app. It follows the contract in
[`../docs/API.md`](../docs/API.md).

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173, proxies /api to http://localhost:8000
npm run build      # typecheck + production build into dist/
```

When `GET /api/health` doesn't answer, the app switches to an in-browser mock server
(`src/lib/mockServer.ts`) that implements the same endpoints and planner rules, stores data in
`localStorage` and shares live events between tabs. So the whole app, including the caregiver view
in a second tab, works with no backend at all. Point the proxy elsewhere with `API_URL=... npm run dev`.

## Screens

| Route | What it is |
|---|---|
| `/` | Landing page with a live phone demo |
| `/setup` | Four-step setup: who, devices, backup power, invite the family (QR + code) |
| `/app` | Home: power status, charger watch, life-support runtime, ways to get more time |
| `/app/outage` | Full-screen outage mode: countdown ring, every device, caregiver acks, milestones, check-in, 911 |
| `/app/devices` | Devices and backup power, with "fill it in from the label" lookup |
| `/app/people` | Family code, QR invite, caregivers |
| `/app/help` | Map of nearby places with backup power |
| `/app/card` | Printable emergency card for first responders |
| `/join` | Caregiver joins with the 6-character family code |
| `/live` | Caregiver's live view: countdown, timeline, "I'm on my way" with ETA |

## How outage detection works

`src/lib/power.ts` watches the Battery Status API. The phone or laptop stays plugged in at home;
when `charging` flips from true to false while the app is armed, it raises an outage
(`source: "charging_lost"`), sounds a Web Audio alarm, vibrates, shows a notification and keeps the
screen awake with the Wake Lock API. Chrome on Android and desktop support this. Elsewhere, drills
and manual outage mode still work.

**Demo tip:** "Run a drill" starts a `drill` outage at 60x speed, so an hour of battery passes in a
minute. Open `/join?code=XXXXXX` in a second window to watch the caregiver side update live.

## Design

- Type: Geist and Geist Mono (self-hosted via Fontsource).
- Color: near-black ink surfaces, signal amber for the brand and countdowns, green for powered,
  red for urgent, sky blue for backup power and caregivers. Tokens live in `src/styles.css`.
- Logo: a heartbeat line that turns into a power bolt (`public/logo.svg`).
