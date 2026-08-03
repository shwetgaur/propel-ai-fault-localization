from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from .ai_brief import generate_brief
from .db import (
    DistributionTransformer,
    Feeder,
    Pole,
    ScheduledOutage,
    Ticket,
    get_db,
)
from .ingest import TelemetryPayload, ingest_batch, ingest_one
from .localization import run_detection_for_affected, verify_restorations
from .seed import generate_and_seed
from .simulator import (
    inject_dead_sensor,
    inject_dt_fault,
    inject_feeder_fault,
    inject_scheduled_outage_effect,
    inject_span_fault,
    list_sim_candidates,
    repair_fault,
)
from .tickets import TransitionError, transition_ticket

router = APIRouter()


class TransitionBody(BaseModel):
    status: str
    note: str | None = None


class SimSpanBody(BaseModel):
    downstream_pole_id: str | None = None


class SimDtBody(BaseModel):
    dt_id: str | None = None


class SimFeederBody(BaseModel):
    feeder_id: str | None = None


class SimSensorBody(BaseModel):
    pole_id: str | None = None


class SimOutageBody(BaseModel):
    outage_id: str | None = None


class SimRepairBody(BaseModel):
    fault_index: int = -1


def ticket_dict(t: Ticket) -> dict[str, Any]:
    return {
        "id": t.id,
        "status": t.status,
        "fault_type": t.fault_type,
        "feeder_id": t.feeder_id,
        "dt_id": t.dt_id,
        "upstream_pole_id": t.upstream_pole_id,
        "downstream_pole_id": t.downstream_pole_id,
        "asset_label": t.asset_label,
        "lat": t.lat,
        "lon": t.lon,
        "pincode": t.pincode,
        "affected_poles": t.affected_poles,
        "affected_households_est": t.affected_households_est,
        "confidence": t.confidence,
        "confidence_reason": t.confidence_reason,
        "topology_source": t.topology_source,
        "dark_pole_ids": json.loads(t.dark_pole_ids or "[]"),
        "evidence": json.loads(t.evidence or "{}"),
        "ai_brief": t.ai_brief,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
        "acknowledged_at": t.acknowledged_at.isoformat() if t.acknowledged_at else None,
        "crew_assigned_at": t.crew_assigned_at.isoformat() if t.crew_assigned_at else None,
        "resolved_at": t.resolved_at.isoformat() if t.resolved_at else None,
        "verified_at": t.verified_at.isoformat() if t.verified_at else None,
        "closed_at": t.closed_at.isoformat() if t.closed_at else None,
        "operator_note": t.operator_note,
    }


@router.get("/health")
def health():
    return {"ok": True, "service": "faultloc"}


@router.get("/stats")
def stats(db: Session = Depends(get_db)):
    return {
        "poles": db.query(func.count(Pole.id)).scalar(),
        "devices": db.query(func.count(Pole.id)).filter(Pole.has_device.is_(True)).scalar(),
        "transformers": db.query(func.count(DistributionTransformer.id)).scalar(),
        "feeders": db.query(func.count(Feeder.id)).scalar(),
        "open_tickets": db.query(func.count(Ticket.id))
        .filter(Ticket.status.in_(["detected", "acknowledged", "crew_assigned", "resolved"]))
        .scalar(),
        "topology_known_dts": db.query(func.count(DistributionTransformer.id))
        .filter(DistributionTransformer.topology_known.is_(True))
        .scalar(),
        "topology_missing_dts": db.query(func.count(DistributionTransformer.id))
        .filter(DistributionTransformer.topology_known.is_(False))
        .scalar(),
        "dark_poles": db.query(func.count(Pole.id)).filter(Pole.energized.is_(False)).scalar(),
    }


@router.post("/telemetry")
def post_telemetry(payload: TelemetryPayload, db: Session = Depends(get_db)):
    return ingest_one(db, payload).model_dump()


@router.post("/telemetry/batch")
def post_telemetry_batch(payloads: list[TelemetryPayload], db: Session = Depends(get_db)):
    return ingest_batch(db, payloads)


@router.get("/tickets")
def list_tickets(
    status: str | None = None,
    db: Session = Depends(get_db),
):
    q = db.query(Ticket).order_by(Ticket.created_at.desc())
    if status:
        statuses = [s.strip() for s in status.split(",")]
        q = q.filter(Ticket.status.in_(statuses))
    return [ticket_dict(t) for t in q.limit(200).all()]


@router.get("/tickets/{ticket_id}")
def get_ticket(ticket_id: str, db: Session = Depends(get_db)):
    t = db.get(Ticket, ticket_id)
    if not t:
        raise HTTPException(404, "Ticket not found")
    return ticket_dict(t)


@router.post("/tickets/{ticket_id}/transition")
def post_transition(ticket_id: str, body: TransitionBody, db: Session = Depends(get_db)):
    try:
        t = transition_ticket(db, ticket_id, body.status, body.note)
    except TransitionError as e:
        raise HTTPException(409 if e.code == "telemetry_conflict" else 400, detail={"code": e.code, "message": str(e)})
    return ticket_dict(t)


@router.post("/tickets/{ticket_id}/brief")
async def post_brief(ticket_id: str, force: bool = False, db: Session = Depends(get_db)):
    try:
        return await generate_brief(db, ticket_id, force=force)
    except ValueError:
        raise HTTPException(404, "Ticket not found")


@router.get("/network/poles")
def network_poles(
    dt_id: str | None = None,
    feeder_id: str | None = None,
    dark_only: bool = False,
    limit: int = Query(5000, le=20000),
    db: Session = Depends(get_db),
):
    q = db.query(Pole)
    if dt_id:
        q = q.filter(Pole.dt_id == dt_id)
    if feeder_id:
        q = q.filter(Pole.feeder_id == feeder_id)
    if dark_only:
        q = q.filter(Pole.energized.is_(False))
    poles = q.limit(limit).all()
    return [
        {
            "id": p.id,
            "lat": p.lat,
            "lon": p.lon,
            "feeder_id": p.feeder_id,
            "dt_id": p.dt_id,
            "energized": p.energized,
            "has_device": p.has_device,
            "pincode": p.pincode,
            "parent_pole_id": p.parent_pole_id,
            "inferred_parent_id": p.inferred_parent_id,
            "firmware": p.firmware,
        }
        for p in poles
    ]


@router.get("/network/transformers")
def network_transformers(db: Session = Depends(get_db)):
    rows = db.query(DistributionTransformer).all()
    return [
        {
            "id": d.id,
            "feeder_id": d.feeder_id,
            "lat": d.lat,
            "lon": d.lon,
            "capacity_kva": d.capacity_kva,
            "households_served": d.households_served,
            "topology_known": d.topology_known,
        }
        for d in rows
    ]


@router.get("/network/feeders")
def network_feeders(db: Session = Depends(get_db)):
    return [{"id": f.id, "substation_id": f.substation_id, "name": f.name} for f in db.query(Feeder).all()]


@router.get("/scheduled-outages")
def scheduled_outages(
    from_ts: datetime | None = Query(None, alias="from"),
    to_ts: datetime | None = Query(None, alias="to"),
    db: Session = Depends(get_db),
):
    rows = db.query(ScheduledOutage).all()
    out = []
    for r in rows:
        if from_ts and r.end < from_ts:
            continue
        if to_ts and r.start > to_ts:
            continue
        out.append(
            {
                "id": r.id,
                "scope": r.scope,
                "target_id": r.target_id,
                "start": r.start.isoformat(),
                "end": r.end.isoformat(),
                "reason": r.reason,
                "cancelled": r.cancelled,
            }
        )
    return out


@router.post("/admin/reseed")
def admin_reseed(db: Session = Depends(get_db)):
    # Clear tickets too
    db.query(Ticket).delete()
    stats = generate_and_seed(db)
    return {"reseeding": "ok", **stats}


@router.post("/admin/detect")
def admin_detect(db: Session = Depends(get_db)):
    tickets = run_detection_for_affected(db)
    verified = verify_restorations(db)
    return {"tickets": [t.id for t in tickets], "verified": [t.id for t in verified]}


@router.get("/simulator/candidates")
def sim_candidates(db: Session = Depends(get_db)):
    return list_sim_candidates(db)


@router.post("/simulator/span")
def sim_span(body: SimSpanBody, db: Session = Depends(get_db)):
    try:
        return inject_span_fault(db, body.downstream_pole_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/simulator/dt")
def sim_dt(body: SimDtBody, db: Session = Depends(get_db)):
    try:
        return inject_dt_fault(db, body.dt_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/simulator/feeder")
def sim_feeder(body: SimFeederBody, db: Session = Depends(get_db)):
    try:
        return inject_feeder_fault(db, body.feeder_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/simulator/dead-sensor")
def sim_dead_sensor(body: SimSensorBody, db: Session = Depends(get_db)):
    try:
        return inject_dead_sensor(db, body.pole_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/simulator/scheduled-outage")
def sim_scheduled(body: SimOutageBody, db: Session = Depends(get_db)):
    try:
        return inject_scheduled_outage_effect(db, body.outage_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/simulator/repair")
def sim_repair(body: SimRepairBody, db: Session = Depends(get_db)):
    try:
        return repair_fault(db, body.fault_index)
    except ValueError as e:
        raise HTTPException(400, str(e))
