# AI workflow

## Tools used

- **Cursor (Composer)** — primary build agent: scaffold, localization, simulator, UI, Docker, docs
- Manual review of localization edge cases and assignment self-check list

## What was delegated vs written carefully

| Area | Approach |
|------|----------|
| Localization & topology inference | Specified algorithm tightly; iterated on tests |
| Simulator physics (70% dying msg, fw 1.2) | Specified from brief; verified via API flows |
| FastAPI routes / React console | Mostly AI-generated, then trimmed |
| Docs | AI first draft, edited for honesty on performance |

## Where AI was wrong (and how caught)

1. **Simulator pre-incremented `last_seq` then sent the same seq** → ingest treated messages as duplicates / no-ops. Caught when span inject created zero tickets; fixed by sending `last_seq + 1` without mutating before ingest.
2. **Early instinct to “just use an LLM for localization”** — rejected against the rubric; kept graph boundary detection.
3. **Over-scoped auth / crew routing** — pulled back to match “what we are not asking for.”

## How much of the final code is AI-generated?

Roughly **80–90%** of lines were produced with AI assistance. The localization rules, noise policy, and decision tradeoffs were human-directed and tested.

## Best prompt pattern

> Implement localization as live/dark frontier on a DT tree. Recorded parents when present; else inferred radial tree from GPS. Suppress isolated dark poles with live children. Group one dark subtree = one ticket. Add pytest for span boundary, dual faults, sensor fail, DT fault, scheduled suppress, inferred mode.

That constrained the solution space and produced testable code instead of a dashboard-first mess.

## Expectation for the follow-up call

I can walk through `localize_dt`, `infer_parents`, ticket `transition` telemetry guard, and the simulator’s intentional message loss without notes.
