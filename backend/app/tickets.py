"""Ticket lifecycle transitions."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from .db import Pole, Ticket, utcnow
from .localization import _is_energized_effective


ALLOWED = {
    "detected": {"acknowledged", "closed"},
    "acknowledged": {"crew_assigned", "closed"},
    "crew_assigned": {"resolved", "closed"},
    "resolved": {"verified", "crew_assigned"},  # bounce back if not actually fixed
    "verified": {"closed"},
    "closed": set(),
}


class TransitionError(Exception):
    def __init__(self, message: str, code: str = "invalid_transition"):
        super().__init__(message)
        self.code = code


def transition_ticket(db: Session, ticket_id: str, new_status: str, note: str | None = None) -> Ticket:
    ticket = db.get(Ticket, ticket_id)
    if not ticket:
        raise TransitionError("Ticket not found", "not_found")
    if new_status not in ALLOWED.get(ticket.status, set()):
        raise TransitionError(
            f"Cannot move from {ticket.status} to {new_status}",
            "invalid_transition",
        )

    now = utcnow()

    if new_status == "resolved":
        # Must not believe the lineman if poles still dark
        dark_ids = json.loads(ticket.dark_pole_ids or "[]")
        if ticket.fault_type == "feeder":
            poles = db.query(Pole).filter(Pole.feeder_id == ticket.feeder_id, Pole.has_device.is_(True)).all()
        elif ticket.fault_type == "dt":
            poles = db.query(Pole).filter(Pole.dt_id == ticket.dt_id, Pole.has_device.is_(True)).all()
        elif dark_ids:
            poles = db.query(Pole).filter(Pole.id.in_(dark_ids), Pole.has_device.is_(True)).all()
        else:
            poles = []
        still_dark = [p.id for p in poles if _is_energized_effective(p, now) is False]
        if still_dark:
            raise TransitionError(
                f"Telemetry still shows {len(still_dark)} dark poles ({', '.join(still_dark[:5])}…). "
                "Cannot mark resolved until power is measured restored.",
                "telemetry_conflict",
            )
        ticket.resolved_at = now

    if new_status == "acknowledged":
        ticket.acknowledged_at = now
    elif new_status == "crew_assigned":
        ticket.crew_assigned_at = now
    elif new_status == "verified":
        ticket.verified_at = now
    elif new_status == "closed":
        ticket.closed_at = now

    ticket.status = new_status
    ticket.updated_at = now
    if note:
        ticket.operator_note = ((ticket.operator_note or "") + "\n" + note).strip()
    db.commit()
    db.refresh(ticket)
    return ticket
