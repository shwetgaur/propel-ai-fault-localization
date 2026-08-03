# KSPDB Fault Localization

Control-room system that turns pole live/dark telemetry into **located outage tickets** — the failed span (or DT/feeder), drive-to coordinates, PIN code, impact, and confidence — then **auto-verifies restoration from telemetry**.

Built for the Propel.ai AI Product Engineer Intern take-home (2026–2027).

## Quick start

```bash
docker compose up --build
```

Open **http://localhost:8080**

First boot seeds ~3,200 poles / 48 DTs (cold start can take ~60–90s while the network generates).

## Demo path (what reviewers should click)

1. **Inject span fault** (right panel) → one ticket appears with span, map pin, PIN, confidence.
2. **Acknowledge → Assign crew**.
3. Try **Mark resolved** while poles are still dark → system refuses.
4. **Repair latest fault** → poles go live → ticket auto-**verified**.
5. **AI dispatch brief** → plain-language briefing (template fallback if no `OPENAI_API_KEY`).
6. Also try **Kill sensor** and **Run scheduled outage** → no fault tickets.

## Public URL / demo video

- **GitHub:** https://github.com/shwetgaur/propel-ai-fault-localization
- **Live URL:** https://propel-fault-localization.onrender.com
  - Render free Docker web service (may cold-start ~30–60s when waking).
  - Dashboard: https://dashboard.render.com/web/srv-d9odc96417fc73f3560g
- **Local:** http://localhost:8080 after `docker compose up --build`
- **Demo video:** Record a 5-minute Loom/YouTube unlisted walkthrough before emailing (gate G6).

### Hosting notes

- Re-deploy: [![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/shwetgaur/propel-ai-fault-localization)
- Free services sleep when idle; first request after sleep can take up to a minute.
- Single-image `Dockerfile` serves API + UI on port **7860** (Render sets `PORT`).
- Hugging Face Docker Spaces need PRO — skipped in favor of Render.

## Docs

| File | Purpose |
|------|---------|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Data flow, localization algorithm, API, UI, AI feature |
| [`DEPLOYMENT.md`](DEPLOYMENT.md) | Runbook, env vars, troubleshooting |
| [`DECISIONS.md`](DECISIONS.md) | Decision log and assumptions |
| [`AI-WORKFLOW.md`](AI-WORKFLOW.md) | How AI was used to build this |

## Stack

- **Backend:** FastAPI + SQLAlchemy + SQLite
- **Frontend:** React + Vite + Leaflet (OSM tiles, no API key)
- **Packaging:** Docker Compose (local) or single Dockerfile (PaaS / HF PRO)

## API

Interactive docs at `/docs` when the stack is up. Core routes under `/api/*`.
