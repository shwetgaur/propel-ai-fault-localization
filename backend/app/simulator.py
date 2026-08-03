"""Fault & noise simulator that produces realistic telemetry side-effects."""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from .db import DistributionTransformer, Pole, ScheduledOutage, SimulatorScenario, utcnow
from .ingest import TelemetryPayload, ingest_batch
from .localization import run_detection_for_affected, verify_restorations
from .topology import children_map, descendants

RNG = random.Random()


def _sim_state(db: Session) -> SimulatorScenario:
    row = db.query(SimulatorScenario).first()
    if not row:
        row = SimulatorScenario(active_faults="[]")
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def _children_for_dt(db: Session, dt: DistributionTransformer) -> dict[str, list[str]]:
    poles = db.query(Pole).filter(Pole.dt_id == dt.id).all()
    if dt.topology_known:
        parents = {p.id: (p.parent_pole_id or dt.id) for p in poles}
    else:
        parents = {p.id: (p.inferred_parent_id or dt.id) for p in poles}
    return children_map(parents, dt.id), poles


def _subtree_poles(db: Session, dt_id: str, downstream_pole_id: str) -> list[Pole]:
    dt = db.get(DistributionTransformer, dt_id)
    children, poles = _children_for_dt(db, dt)
    ids = {downstream_pole_id} | descendants(children, downstream_pole_id)
    return [p for p in poles if p.id in ids]


def _emit_power_lost(pole: Pole, now: datetime, seq_bump: int = 1) -> TelemetryPayload | None:
    if not pole.has_device or not pole.device_id:
        return None
    # fw 1.2.x does not send power_lost
    if pole.firmware.startswith("1.2"):
        pole.energized = False
        pole.device_online = False
        return None
    # 30% dying messages never arrive
    if RNG.random() < 0.30:
        pole.energized = False
        pole.device_online = False
        return None
    next_seq = pole.last_seq + seq_bump
    return TelemetryPayload(
        device_id=pole.device_id,
        pole_id=pole.id,
        event="power_lost",
        energized=False,
        ts=now + timedelta(seconds=RNG.uniform(-5, 25)),  # skew / reordering
        seq=next_seq,
        battery_mv=RNG.randint(3100, 3600),
        rssi=RNG.randint(-110, -70),
        fw=pole.firmware,
    )


def _emit_restore(pole: Pole, now: datetime) -> list[TelemetryPayload]:
    if not pole.has_device or not pole.device_id:
        pole.energized = True
        return []
    base = pole.last_seq
    return [
        TelemetryPayload(
            device_id=pole.device_id,
            pole_id=pole.id,
            event="boot",
            energized=True,
            ts=now + timedelta(seconds=RNG.uniform(0, 10)),
            seq=base + 1,
            battery_mv=RNG.randint(3600, 4100),
            rssi=RNG.randint(-100, -60),
            fw=pole.firmware,
        ),
        TelemetryPayload(
            device_id=pole.device_id,
            pole_id=pole.id,
            event="power_restored",
            energized=True,
            ts=now + timedelta(seconds=RNG.uniform(5, 20)),
            seq=base + 2,
            battery_mv=RNG.randint(3600, 4100),
            rssi=RNG.randint(-100, -60),
            fw=pole.firmware,
        ),
    ]

def inject_span_fault(db: Session, downstream_pole_id: str | None = None) -> dict[str, Any]:
    """Fault on span upstream of downstream_pole_id (or pick a good candidate)."""
    now = utcnow()
    if downstream_pole_id:
        pole = db.get(Pole, downstream_pole_id)
        if not pole:
            raise ValueError("pole not found")
        dt_id = pole.dt_id
        target = pole
    else:
        # Prefer a mid-line pole on a known-topology DT with devices downstream
        candidates = (
            db.query(Pole)
            .filter(Pole.has_device.is_(True), Pole.parent_pole_id.isnot(None))
            .limit(200)
            .all()
        )
        if not candidates:
            candidates = db.query(Pole).filter(Pole.has_device.is_(True)).limit(200).all()
        target = RNG.choice(candidates)
        dt_id = target.dt_id
        downstream_pole_id = target.id

    affected = _subtree_poles(db, dt_id, downstream_pole_id)
    payloads: list[TelemetryPayload] = []
    for p in affected:
        msg = _emit_power_lost(p, now)
        if msg:
            payloads.append(msg)
            # occasional duplicate
            if RNG.random() < 0.08:
                payloads.append(msg.model_copy())

    # Shuffle for out-of-order delivery
    RNG.shuffle(payloads)
    result = ingest_batch(db, payloads)

    # Also force state for silent devices
    for p in affected:
        if not p.energized:
            continue
        # devices that didn't send still go dark physically
        if p.id in {x.id for x in affected}:
            p.energized = False
    db.commit()
    tickets = run_detection_for_affected(db, pole_ids=[p.id for p in affected])

    fault = {
        "type": "span",
        "dt_id": dt_id,
        "downstream_pole_id": downstream_pole_id,
        "affected_pole_ids": [p.id for p in affected],
        "injected_at": now.isoformat(),
    }
    state = _sim_state(db)
    faults = json.loads(state.active_faults)
    faults.append(fault)
    state.active_faults = json.dumps(faults)
    state.last_action = f"inject_span {downstream_pole_id}"
    state.updated_at = now
    db.commit()

    return {
        "fault": fault,
        "telemetry": result,
        "tickets": [t.id for t in tickets],
        "affected_poles": len(affected),
    }


def inject_dt_fault(db: Session, dt_id: str | None = None) -> dict[str, Any]:
    now = utcnow()
    if dt_id:
        dt = db.get(DistributionTransformer, dt_id)
    else:
        # Prefer DT not under active scheduled feeder outage
        dts = db.query(DistributionTransformer).all()
        dt = RNG.choice(dts)
    if not dt:
        raise ValueError("dt not found")
    poles = db.query(Pole).filter(Pole.dt_id == dt.id).all()
    payloads = []
    for p in poles:
        msg = _emit_power_lost(p, now)
        if msg:
            payloads.append(msg)
    RNG.shuffle(payloads)
    result = ingest_batch(db, payloads)
    for p in poles:
        p.energized = False
    db.commit()
    tickets = run_detection_for_affected(db, pole_ids=[p.id for p in poles])
    fault = {
        "type": "dt",
        "dt_id": dt.id,
        "affected_pole_ids": [p.id for p in poles],
        "injected_at": now.isoformat(),
    }
    state = _sim_state(db)
    faults = json.loads(state.active_faults)
    faults.append(fault)
    state.active_faults = json.dumps(faults)
    state.last_action = f"inject_dt {dt.id}"
    state.updated_at = now
    db.commit()
    return {"fault": fault, "telemetry": result, "tickets": [t.id for t in tickets], "affected_poles": len(poles)}


def inject_feeder_fault(db: Session, feeder_id: str | None = None) -> dict[str, Any]:
    now = utcnow()
    if not feeder_id:
        from .db import Feeder

        feeder_id = RNG.choice([f.id for f in db.query(Feeder).all()])
    poles = db.query(Pole).filter(Pole.feeder_id == feeder_id).all()
    payloads = []
    for p in poles:
        msg = _emit_power_lost(p, now)
        if msg:
            payloads.append(msg)
    RNG.shuffle(payloads)
    result = ingest_batch(db, payloads)
    for p in poles:
        p.energized = False
    db.commit()
    tickets = run_detection_for_affected(db, feeder_ids=[feeder_id], pole_ids=[p.id for p in poles])
    fault = {
        "type": "feeder",
        "feeder_id": feeder_id,
        "affected_pole_ids": [p.id for p in poles],
        "injected_at": now.isoformat(),
    }
    state = _sim_state(db)
    faults = json.loads(state.active_faults)
    faults.append(fault)
    state.active_faults = json.dumps(faults)
    state.last_action = f"inject_feeder {feeder_id}"
    state.updated_at = now
    db.commit()
    return {"fault": fault, "telemetry": result, "tickets": [t.id for t in tickets], "affected_poles": len(poles)}


def inject_dead_sensor(db: Session, pole_id: str | None = None) -> dict[str, Any]:
    """Device dies while power is fine — must NOT create a fault ticket."""
    now = utcnow()
    if pole_id:
        pole = db.get(Pole, pole_id)
    else:
        # Prefer an interior pole that has at least one live child (recorded or inferred)
        poles = db.query(Pole).filter(Pole.has_device.is_(True), Pole.energized.is_(True)).all()
        child_parents = set()
        for p in poles:
            parent = p.parent_pole_id or p.inferred_parent_id
            if parent:
                child_parents.add(parent)
        candidates = [p for p in poles if p.id in child_parents]
        pole = RNG.choice(candidates or poles)
    if not pole or not pole.device_id:
        raise ValueError("no device pole")

    # Mark dark via telemetry but keep downstream live (already live)
    payload = TelemetryPayload(
        device_id=pole.device_id,
        pole_id=pole.id,
        event="power_lost",
        energized=False,
        ts=now,
        seq=pole.last_seq + 1,
        battery_mv=3000,
        rssi=-105,
        fw=pole.firmware,
    )
    result = ingest_batch(db, [payload])
    tickets = run_detection_for_affected(db, pole_ids=[pole.id])
    return {
        "pole_id": pole.id,
        "telemetry": result,
        "tickets": [t.id for t in tickets],
        "note": "Expected: zero fault tickets (sensor anomaly suppressed)",
    }


def inject_scheduled_outage_effect(db: Session, outage_id: str | None = None) -> dict[str, Any]:
    """Darken poles under an active scheduled outage — must not ticket."""
    now = utcnow()
    q = db.query(ScheduledOutage).filter(ScheduledOutage.cancelled.is_(False))
    if outage_id:
        so = db.get(ScheduledOutage, outage_id)
    else:
        # Prefer currently active
        rows = q.all()
        so = None
        for r in rows:
            start = r.start if r.start.tzinfo else r.start.replace(tzinfo=timezone.utc)
            end = r.end if r.end.tzinfo else r.end.replace(tzinfo=timezone.utc)
            if start - timedelta(minutes=30) <= now <= end + timedelta(minutes=30):
                so = r
                break
        if not so:
            so = rows[0] if rows else None
    if not so:
        raise ValueError("no scheduled outage")

    if so.scope == "feeder":
        poles = db.query(Pole).filter(Pole.feeder_id == so.target_id).all()
    else:
        poles = db.query(Pole).filter(Pole.dt_id == so.target_id).all()

    payloads = []
    for p in poles:
        msg = _emit_power_lost(p, now)
        if msg:
            payloads.append(msg)
    RNG.shuffle(payloads)
    result = ingest_batch(db, payloads)
    for p in poles:
        p.energized = False
    db.commit()
    tickets = run_detection_for_affected(db, pole_ids=[p.id for p in poles])
    return {
        "outage": {"id": so.id, "scope": so.scope, "target_id": so.target_id, "reason": so.reason},
        "telemetry": result,
        "tickets": [t.id for t in tickets],
        "note": "Expected: suppressed / no fault tickets",
    }


def repair_fault(db: Session, fault_index: int = -1) -> dict[str, Any]:
    state = _sim_state(db)
    faults = json.loads(state.active_faults)
    if not faults:
        raise ValueError("no active simulated faults")
    fault = faults.pop(fault_index)
    now = utcnow()
    ids = fault.get("affected_pole_ids") or []
    poles = db.query(Pole).filter(Pole.id.in_(ids)).all() if ids else []
    payloads: list[TelemetryPayload] = []
    for p in poles:
        payloads.extend(_emit_restore(p, now))
        p.energized = True
        p.device_online = True
    RNG.shuffle(payloads)
    result = ingest_batch(db, payloads)
    db.commit()
    # ingest_batch may already have auto-verified; collect open→verified results
    from .db import Ticket

    verified = (
        db.query(Ticket)
        .filter(Ticket.status == "verified")
        .order_by(Ticket.verified_at.desc())
        .limit(5)
        .all()
    )
    state.active_faults = json.dumps(faults)
    state.last_action = f"repair {fault.get('type')}"
    state.updated_at = now
    db.commit()
    return {
        "repaired": fault,
        "telemetry": result,
        "verified_tickets": [t.id for t in verified],
        "remaining_faults": len(faults),
    }


def list_sim_candidates(db: Session) -> dict[str, Any]:
    """Helpful poles/DTs for the UI simulator."""
    known_span = (
        db.query(Pole)
        .filter(Pole.parent_pole_id.isnot(None), Pole.has_device.is_(True))
        .limit(30)
        .all()
    )
    inferred_span = (
        db.query(Pole)
        .filter(Pole.parent_pole_id.is_(None), Pole.inferred_parent_id.isnot(None), Pole.has_device.is_(True))
        .limit(30)
        .all()
    )
    dts = db.query(DistributionTransformer).limit(40).all()
    from .db import Feeder

    feeders = db.query(Feeder).all()
    state = _sim_state(db)
    return {
        "span_candidates_recorded": [{"pole_id": p.id, "dt_id": p.dt_id, "feeder_id": p.feeder_id} for p in known_span],
        "span_candidates_inferred": [{"pole_id": p.id, "dt_id": p.dt_id, "feeder_id": p.feeder_id} for p in inferred_span],
        "dts": [
            {
                "id": d.id,
                "feeder_id": d.feeder_id,
                "topology_known": d.topology_known,
                "households_served": d.households_served,
            }
            for d in dts
        ],
        "feeders": [{"id": f.id, "name": f.name} for f in feeders],
        "active_faults": json.loads(state.active_faults),
        "last_action": state.last_action,
    }
