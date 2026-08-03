# Deployment

## Prerequisites

- Docker Engine 24+ and Docker Compose v2
- Ports: **8080** free on the host
- Optional: `OPENAI_API_KEY` for live LLM briefs

## One-command start

```bash
git clone <your-repo-url>
cd propel-ai-fault-localization
cp .env.example .env   # optional
docker compose up --build
```

Open http://localhost:8080

API health: http://localhost:8080/api/health  
OpenAPI: http://localhost:8080/docs

### Verify it worked

1. Header shows pole/DT counts (non-zero).
2. Click **Inject span fault** → an incident appears within a few seconds.
3. Map shows red dark poles and an orange fault marker.

## Environment variables

| Name | Required | Default | Purpose |
|------|----------|---------|---------|
| `DATABASE_URL` | no | `sqlite:////data/faultloc.db` | SQLAlchemy URL (Compose sets this) |
| `SEED_ON_STARTUP` | no | `true` | Seed empty DB on boot |
| `CORS_ORIGINS` | no | `*` | CORS allow list |
| `OPENAI_API_KEY` | no | empty | Enables LLM briefs |
| `OPENAI_MODEL` | no | `gpt-4o-mini` | Chat model |

Commit `.env.example`; do not commit `.env` with secrets.

## Local dev (without Docker)

```bash
# backend
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
mkdir data
uvicorn app.main:app --reload --port 8000

# frontend
cd frontend
npm install
npm run dev
```

Frontend proxies `/api` to `:8000`.

## Tests

```bash
cd backend
pytest -q
```

## Reset to clean state

```bash
docker compose down -v
docker compose up --build
```

Or `POST /api/admin/reseed` (wipes tickets + regenerates network).

## Public deploy (example: Railway / Render / Fly)

Any host that can run Docker Compose or the two images works.

Suggested path:

1. Push this repo to public GitHub.
2. Deploy with a Dockerfile/compose service exposing port 8080 (frontend nginx already proxies `/api`).
3. Free tiers cold-start — note that in the README so reviewers wait ~30–60s.
4. Paste the public URL into `README.md`.

Alternative: expose backend `:8000` and frontend separately; set `VITE_API_URL` at frontend build time to the public API origin.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Compose healthy but UI empty counts | Seed still running | Wait; backend `start_period` is 90s. Check `docker compose logs backend` |
| `port is already allocated` | 8080 in use | Change `"8080:80"` in compose |
| `/api` 502 | Backend not healthy | `docker compose ps`, logs; curl health on backend |
| Tickets never appear on inject | Seed incomplete / bug | Check logs; `POST /api/admin/detect` |
| Map tiles blank | OSM blocked on network | Check outbound HTTPS; tiles are public OSM |
| `telemetry_conflict` on resolve | Poles still dark | Expected — use **Repair latest fault** first |
| ARM Mac build slow / fail | Image platform | Ensure Docker using amd64/arm64 compatible base (official python/node images are multi-arch) |
| Volume permission on SQLite | Rare on Linux bind mounts | Compose uses named volume `/data` — prefer that |
| AI brief always template | No key / API error | Set `OPENAI_API_KEY`; template fallback is intentional |
| CORS errors in local split deploy | Origins | Set `CORS_ORIGINS` to the UI origin |

## Measured targets (local Docker, synthetic network)

Run after `docker compose up` and injecting faults from the UI or API.

| Metric | Target | Observed (dev machine) |
|--------|--------|-------------------------|
| Fault → ticket visible | < 120 s p95 | Typically < 5 s after inject returns |
| Batch ingest | ≥ 500 msg/s | Not load-tested to 500 sustained; batch path is synchronous O(n) — fine for demo scale, would need a queue for production 39 msg/s × N divisions |
| 5k burst / 10 s | no loss | Not formally bench’d; duplicates rejected, accepted rows written transactionally |
| Console refresh | < 2 s | Poll every 4 s; list endpoint is light |
| Restore → verified | < 120 s | Immediate on repair ingest + verify pass |

Honest gap: production ingest would use a queue + workers; this submission prioritizes correct localization and a reviewable demo over a load-tested broker.
