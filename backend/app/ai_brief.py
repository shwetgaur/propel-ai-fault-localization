"""AI feature: plain-language dispatch brief for operators.

Localization stays deterministic. The LLM (or template fallback) turns a
structured ticket into a 2 a.m.–friendly briefing — what broke, where to drive,
how bad, and what confidence means. This is where an LLM earns its keep.
"""

from __future__ import annotations

import json

import httpx
from sqlalchemy.orm import Session

from .config import settings
from .db import Ticket, utcnow


def _template_brief(ticket: Ticket) -> str:
    pin = ticket.pincode or "PIN unknown"
    topo = (
        "Wiring order is on file for this line."
        if ticket.topology_source == "recorded"
        else "Wiring order was inferred from GPS — treat the exact span as a best estimate and confirm on site."
    )
    return (
        f"FAULT BRIEF — {ticket.id}\n"
        f"What: {ticket.asset_label} ({ticket.fault_type} fault).\n"
        f"Where: navigate to {ticket.lat:.5f}, {ticket.lon:.5f} (PIN {pin}).\n"
        f"Impact: ~{ticket.affected_poles} poles dark, ~{ticket.affected_households_est} households estimated.\n"
        f"Confidence: {ticket.confidence:.0%}. {ticket.confidence_reason}\n"
        f"Topology: {topo}\n"
        f"Next: acknowledge, assign crew with ladder/jumpers for LT span work, "
        f"do not close until poles report live again."
    )


async def generate_brief(db: Session, ticket_id: str, force: bool = False) -> dict:
    ticket = db.get(Ticket, ticket_id)
    if not ticket:
        raise ValueError("ticket not found")
    if ticket.ai_brief and not force:
        return {"brief": ticket.ai_brief, "source": "cached", "model": None}

    if not settings.openai_api_key:
        brief = _template_brief(ticket)
        ticket.ai_brief = brief
        ticket.updated_at = utcnow()
        db.commit()
        return {"brief": brief, "source": "template_fallback", "model": None}

    payload = {
        "model": settings.openai_model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You write terse dispatch briefs for electricity control-room operators. "
                    "No fluff. 6-10 short lines. Include coords, PIN, impact, confidence caveat, next action. "
                    "Never invent assets not in the JSON."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "id": ticket.id,
                        "fault_type": ticket.fault_type,
                        "asset_label": ticket.asset_label,
                        "lat": ticket.lat,
                        "lon": ticket.lon,
                        "pincode": ticket.pincode,
                        "affected_poles": ticket.affected_poles,
                        "affected_households_est": ticket.affected_households_est,
                        "confidence": ticket.confidence,
                        "confidence_reason": ticket.confidence_reason,
                        "topology_source": ticket.topology_source,
                        "status": ticket.status,
                    }
                ),
            },
        ],
        "temperature": 0.2,
        "max_tokens": 350,
    }
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                json=payload,
            )
            resp.raise_for_status()
            brief = resp.json()["choices"][0]["message"]["content"].strip()
            ticket.ai_brief = brief
            ticket.updated_at = utcnow()
            db.commit()
            return {"brief": brief, "source": "openai", "model": settings.openai_model}
    except Exception as exc:  # noqa: BLE001
        brief = _template_brief(ticket)
        ticket.ai_brief = brief
        ticket.updated_at = utcnow()
        db.commit()
        return {
            "brief": brief,
            "source": "template_fallback",
            "model": None,
            "error": str(exc),
        }
