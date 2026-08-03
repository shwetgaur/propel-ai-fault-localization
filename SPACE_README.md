---
title: KSPDB Fault Localization
emoji: ⚡
colorFrom: green
colorTo: yellow
sdk: docker
app_port: 7860
pinned: false
short_description: Propel.ai take-home — outage fault localization console
---

# KSPDB Fault Localization

Control-room demo for the **Propel.ai AI Product Engineer Intern** take-home.

Pole IoT telemetry → localized outage tickets (span / DT / feeder) → operator console → telemetry-verified restore.

## Use it

1. Wait for cold start (~1–2 min on free CPU while the network seeds).
2. Click **Inject span fault** in the right panel.
3. Open the ticket, walk the workflow, then **Repair latest fault** to auto-verify.

Source: https://github.com/shwetgaur/propel-ai-fault-localization
