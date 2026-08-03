"""Telemetry ingest with dedup, ordering, and stale rejection."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .db import Pole, TelemetryEvent, utcnow
from .localization import run_detection_for_affected, verify_restorations


class TelemetryPayload(BaseModel):
    device_id: str
    pole_id: str
    event: str
    energized: bool
    ts: datetime
    seq: int
    battery_mv: int | None = None
    rssi: int | None = None
    fw: str | None = None


class IngestResult(BaseModel):
    accepted: bool
    reason: str | None = None
    pole_id: str | None = None
    detection_triggered: bool = False


def _parse_ts(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


def ingest_one(db: Session, payload: TelemetryPayload, *, run_detection: bool = True) -> IngestResult:
    pole = db.get(Pole, payload.pole_id)
    if not pole:
        return IngestResult(accepted=False, reason="unknown_pole")

    # Prefer pole_id for location; device_id may have been swapped
    if pole.device_id and payload.device_id != pole.device_id:
        # Allow swap: update mapping
        pole.device_id = payload.device_id

    ts = _parse_ts(payload.ts)
    now = utcnow()

    # Stale replay: reject power_lost older than 6 hours unless seq is new
    age = now - ts
    if payload.event == "power_lost" and age.total_seconds() > 6 * 3600:
        if payload.seq <= pole.last_seq:
            evt = TelemetryEvent(
                device_id=payload.device_id,
                pole_id=payload.pole_id,
                event=payload.event,
                energized=payload.energized,
                ts=ts,
                seq=payload.seq,
                battery_mv=payload.battery_mv,
                rssi=payload.rssi,
                fw=payload.fw,
                accepted=False,
                drop_reason="stale_replay",
            )
            db.add(evt)
            db.commit()
            return IngestResult(accepted=False, reason="stale_replay", pole_id=pole.id)

    # Dedup by (device_id, seq)
    evt = TelemetryEvent(
        device_id=payload.device_id,
        pole_id=payload.pole_id,
        event=payload.event,
        energized=payload.energized,
        ts=ts,
        seq=payload.seq,
        battery_mv=payload.battery_mv,
        rssi=payload.rssi,
        fw=payload.fw or pole.firmware,
        accepted=True,
    )
    db.add(evt)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return IngestResult(accepted=False, reason="duplicate_seq", pole_id=pole.id)

    # Ordering: only apply state if seq >= last_seq (boot resets allowed)
    apply = False
    if payload.event == "boot" or payload.seq == 0:
        apply = True
        pole.last_seq = payload.seq
    elif payload.seq > pole.last_seq:
        apply = True
        pole.last_seq = payload.seq
    elif payload.seq == pole.last_seq:
        # duplicate already handled; treat as no-op
        evt.accepted = False
        evt.drop_reason = "duplicate_seq"
        db.commit()
        return IngestResult(accepted=False, reason="duplicate_seq", pole_id=pole.id)
    else:
        # Out-of-order older seq — keep for audit, don't rewind state
        evt.accepted = False
        evt.drop_reason = "out_of_order"
        db.commit()
        return IngestResult(accepted=False, reason="out_of_order", pole_id=pole.id)

    if apply:
        if payload.event in ("power_lost", "heartbeat", "power_restored", "boot"):
            pole.energized = payload.energized
        if payload.event == "power_lost":
            pole.energized = False
        if payload.event in ("power_restored", "boot") and payload.energized:
            pole.energized = True
        pole.last_seen_at = now
        pole.device_online = True
        if payload.fw:
            pole.firmware = payload.fw

    db.commit()

    detection_triggered = False
    if run_detection and apply:
        if payload.event in ("power_lost", "power_restored", "boot") or not payload.energized:
            run_detection_for_affected(db, pole_ids=[pole.id])
            detection_triggered = True
        if payload.event in ("power_restored", "boot") or payload.energized:
            verify_restorations(db)

    return IngestResult(accepted=True, pole_id=pole.id, detection_triggered=detection_triggered)


def ingest_batch(db: Session, payloads: list[TelemetryPayload]) -> dict:
    accepted = 0
    rejected = 0
    reasons: dict[str, int] = {}
    touched: set[str] = set()
    for p in payloads:
        r = ingest_one(db, p, run_detection=False)
        if r.accepted:
            accepted += 1
            if r.pole_id:
                touched.add(r.pole_id)
        else:
            rejected += 1
            reasons[r.reason or "unknown"] = reasons.get(r.reason or "unknown", 0) + 1
    tickets = []
    if touched:
        tickets = run_detection_for_affected(db, pole_ids=list(touched))
        verify_restorations(db)
    return {
        "accepted": accepted,
        "rejected": rejected,
        "reasons": reasons,
        "tickets": [t.id for t in tickets],
    }
