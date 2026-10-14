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
