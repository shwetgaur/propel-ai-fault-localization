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
- **Live URL:** https://274d753b4cea1b.lhr.life
  - Reverse tunnel to local Docker on `:8080` (localhost.run). Keep `docker compose up` running while reviewers need access.
  - If down: restart Compose + tunnel, or deploy the same stack to Render/Railway/Fly (see `DEPLOYMENT.md`).
- **Demo video:** Record a 5-minute Loom/YouTube unlisted walkthrough of the demo path above before emailing (gate G6 insurance).

> Tunnel/free-tier reconnects are possible — wait ~30s or use the demo video.

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
- **Packaging:** Docker Compose, nginx reverse-proxy to API

## API

Interactive docs at `/docs` when the stack is up. Core routes under `/api/*`.
