# Decision log

Newest first.

## 2026-08-03 — SQLite in Docker instead of Postgres

**Chose:** SQLite on a named volume.  
**Rejected:** Postgres + Redis for the demo.  
**Why:** One-command reliability and zero migration ops for reviewers. Subdivision-scale demo (~3k poles) fits easily. Documented that production would move ingest to a queue and Postgres.

## 2026-08-03 — Infer topology rather than DT-only fallback

**Chose:** Geographic radial spanning tree at seed; span-level localization with lower confidence + UI label.  
**Rejected:** Only returning DT-level centroids for the 60%; waiting for a survey.  
**Why:** Brief asks what you can ship *today*. Coarse DT pins alone do not meet “drive to the span.” Inference is wrong sometimes — we surface that instead of hiding it.

## 2026-08-03 — Polling UI (4s) instead of WebSockets

**Chose:** HTTP polling.  
**Rejected:** WebSockets.  
**Why:** Classic free-tier proxy failure mode; polling is good enough for control-room tempo and always works behind nginx.

## 2026-08-03 — AI = dispatch brief, not localization

**Chose:** On-demand natural-language brief with template fallback.  
**Rejected:** LLM classifying faults / proposing spans.  
**Why:** Boundary finding is deterministic graph reasoning; an LLM adds latency, cost, and non-determinism where tests and operator trust matter most.

## 2026-08-03 — Suppress scheduled outages with ±30 min grace

**Chose:** Grace window; still allow faults outside.  
**Rejected:** Trusting the feed as exact.  
**Why:** Brief says shutdowns start late and overrun; also ~10% cancelled without update — we honor `cancelled` but do not invent cancellation detection.

## 2026-08-03 — Operator must not click “resolved” while dark

**Chose:** Hard reject on transition when telemetry still dark; auto-verify on restore.  
**Rejected:** Honor lineman button then reconcile later.  
**Why:** Explicit requirement; trust dies if “fixed” lies.

## Assumptions (brief was ambiguous)

1. One subdivision only; synthetic ~3.2k poles is enough shape fidelity.
2. PIN from registry; if missing, show “confirm on site” rather than live geocoder dependency.
3. “One fault” = one live/dark boundary / dark root subtree. Two boundaries → two tickets even on same DT.
4. Silence without confirmed dark does not open tickets (avoid modem-death false positives); simulator forces physical dark for realism when dying messages are dropped.
5. Hardcoded operator identity; no auth.

## With two more weeks

- MQTT consumer + worker queue; measure 500 msg/s and 5k burst properly
- Learn topology from repeated co-outage patterns
- Debounce window to coalesce storm bursts before ticketing
- Richer map: only poles near selected incident to cut payload size
- Playback of historical telemetry for training operators

## Known fragile / wrong

- Inferred parents can pick the wrong adjacent span
- Full pole GeoJSON to the browser (~3k) is fine now, not at 38k
- Synchronous batch ingest will not meet sustained production throughput claims
- Feeder fault heuristic (≥70% DTs dark) may mis-fire in partial telemetry black holes
