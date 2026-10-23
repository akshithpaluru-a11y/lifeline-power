# LifeLine Power

When the power goes out, some people can't just wait for it to come back. Over 3 million Medicare
patients depend on electricity-powered medical equipment such as oxygen concentrators, home
ventilators, CPAP machines and feeding pumps (HHS emPOWER).

LifeLine Power turns the phone already plugged in at home into an outage alarm. When charging
stops, it starts a live battery countdown for every medical device in the house, tells the family's
caregivers right away, works out when the family needs to leave for a powered location, and points
them to the nearest one.

Built for the 2026 Congressional App Challenge.

## Repository layout

| Path | What it is |
|------|------------|
| `backend/` | Python (FastAPI) API: households, devices, runtime planner, outage engine, live caregiver alerts |
| `frontend/` | TypeScript (React + Vite) progressive web app |
| `docs/API.md` | The API contract both halves are built against |

## Run it

- **Locally:** start the API (see `backend/README.md`), then the web app (`cd frontend && npm ci
  && npm run dev`), which forwards `/api` to port 8000.
- **As one service:** the root `Dockerfile` builds the web app and serves it from the API on one
  origin (`LIFELINE_STATIC_DIR`). `render.yaml` deploys that container to Render's free plan: in
  Render, choose New, then Blueprint, and pick this repository. Free instances sleep when idle
  (the first visit takes about a minute to wake) and start with an empty database after each
  restart, which suits demos. Set `ANTHROPIC_API_KEY` in Render to turn on the photo device helper.
