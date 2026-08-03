"""Fault localization from pole energized state.

Core idea: sensors report node liveness; faults live on edges. The fault is the
frontier between the live connected component (rooted at the DT) and the dark
subtree(s) beyond it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy.orm import Session

from .config import settings
from .db import DistributionTransformer, Pole, ScheduledOutage, Ticket, utcnow
from .topology import children_map, descendants, haversine_m


ACTIVE_STATUSES = {"detected", "acknowledged", "crew_assigned", "resolved"}


@dataclass
class LocalizationResult:
    fault_type: str
    feeder_id: str
    dt_id: str | None
    upstream_pole_id: str | None
    downstream_pole_id: str | None
    asset_label: str
    lat: float
    lon: float
    pincode: str | None
    affected_poles: int
    affected_households_est: int
    confidence: float
    confidence_reason: str
    topology_source: str
    dark_pole_ids: list[str]
    evidence: dict = field(default_factory=dict)
    suppress: bool = False
    suppress_reason: str | None = None


def _effective_parent(pole: Pole, topology_source: str) -> str | None:
    if topology_source == "recorded":
        return pole.parent_pole_id
    return pole.inferred_parent_id


def _build_tree(
    dt: DistributionTransformer,
    poles: list[Pole],
) -> tuple[dict[str, list[str]], dict[str, Pole], str]:
    by_id = {p.id: p for p in poles}
    if dt.topology_known and all(p.parent_pole_id or p.seq_on_line == 1 for p in poles if True):
        # Prefer recorded parents when DT is marked known and parents present
        has_recorded = sum(1 for p in poles if p.parent_pole_id) >= max(1, int(0.8 * len(poles)))
        if has_recorded:
            parents = {p.id: p.parent_pole_id or dt.id for p in poles}
            # poles with seq 1 and no parent attach to DT
            for p in poles:
                if p.parent_pole_id is None:
                    parents[p.id] = dt.id
            return children_map(parents, dt.id), by_id, "recorded"

    parents = {p.id: (p.inferred_parent_id or dt.id) for p in poles}
    return children_map(parents, dt.id), by_id, "inferred"


def _is_energized_effective(pole: Pole, now: datetime) -> bool | None:
    """True/False known; None means unknown (no device / stale)."""
    if not pole.has_device:
        return None
    if pole.last_seen_at is None:
        return None
    last = pole.last_seen_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    stale_after = timedelta(minutes=settings.stale_telemetry_minutes)
    # If device is silent beyond heartbeat window and we think it was live,
    # treat as unknown rather than dark — silence ≠ confirmed dark unless
    # we also see downstream confirmation. Localization uses confirmed dark.
    if not pole.device_online and (now - last) > stale_after:
        return None if pole.energized else False
    return pole.energized


def _scheduled_suppression(
    db: Session,
    feeder_id: str,
    dt_id: str,
    now: datetime,
) -> ScheduledOutage | None:
    grace = timedelta(minutes=settings.scheduled_outage_grace_minutes)
    rows = (
        db.query(ScheduledOutage)
        .filter(ScheduledOutage.cancelled.is_(False))
        .all()
    )
    for so in rows:
        start = so.start if so.start.tzinfo else so.start.replace(tzinfo=timezone.utc)
        end = so.end if so.end.tzinfo else so.end.replace(tzinfo=timezone.utc)
        if now < start - grace or now > end + grace:
            continue
        if so.scope == "feeder" and so.target_id == feeder_id:
            return so
        if so.scope == "dt" and so.target_id == dt_id:
            return so
    return None


def _estimate_households(dt: DistributionTransformer, dark_count: int, total_poles: int) -> int:
    if total_poles <= 0:
        return 0
    return max(1, int(dt.households_served * (dark_count / total_poles)))


def _pincode_for(poles: Iterable[Pole], fallback_poles: list[Pole]) -> str | None:
    for p in poles:
        if p.pincode:
            return p.pincode
    for p in fallback_poles:
        if p.pincode:
            return p.pincode
    return None


def localize_dt(db: Session, dt_id: str, now: datetime | None = None) -> list[LocalizationResult]:
    now = now or utcnow()
    dt = db.get(DistributionTransformer, dt_id)
    if not dt:
        return []
    poles = db.query(Pole).filter(Pole.dt_id == dt_id).all()
    if not poles:
        return []

    children, by_id, topo_source = _build_tree(dt, poles)
    results: list[LocalizationResult] = []

    # Feeder-level check is done separately; here we find boundaries under this DT.
    confirmed_dark = []
    confirmed_live = []
    unknown = []
    for p in poles:
        state = _is_energized_effective(p, now)
        if state is True:
            confirmed_live.append(p.id)
        elif state is False:
            confirmed_dark.append(p.id)
        else:
            unknown.append(p.id)

    if not confirmed_dark:
        return []

    # Sensor failure: isolated dark pole whose children (if any reporting) are live
    dark_set = set(confirmed_dark)
    live_set = set(confirmed_live)

    # Find dark roots: dark poles whose parent is live, DT, or unknown-but-not-dark
    dark_roots: list[str] = []
    for pid in confirmed_dark:
        pole = by_id[pid]
        parent = _effective_parent(pole, topo_source) if topo_source == "recorded" else (pole.inferred_parent_id or dt.id)
        if parent == dt.id:
            # parent is DT — if DT fault, all children dark; handled below
            dark_roots.append(pid)
            continue
        parent_pole = by_id.get(parent)
        if parent_pole is None:
            dark_roots.append(pid)
            continue
        parent_state = _is_energized_effective(parent_pole, now)
        if parent_state is True or parent_state is None:
            dark_roots.append(pid)

    # Deduplicate nested roots: keep only roots not descendants of another dark root
    filtered_roots = []
    for r in dark_roots:
        if any(r in descendants(children, other) for other in dark_roots if other != r):
            continue
        filtered_roots.append(r)

    so = _scheduled_suppression(db, dt.feeder_id, dt.id, now)

    # DT fault: every known-state pole under DT is dark (no live poles)
    if confirmed_live == [] and len(confirmed_dark) >= max(2, int(0.5 * len([p for p in poles if p.has_device]))):
        # Check not just one sensor
        if so:
            return [
                LocalizationResult(
                    fault_type="dt",
                    feeder_id=dt.feeder_id,
                    dt_id=dt.id,
                    upstream_pole_id=None,
                    downstream_pole_id=None,
                    asset_label=f"DT {dt.id}",
                    lat=dt.lat,
                    lon=dt.lon,
                    pincode=_pincode_for(poles, poles),
                    affected_poles=len(poles),
                    affected_households_est=dt.households_served,
                    confidence=0.9,
                    confidence_reason="All reporting poles under DT are dark",
                    topology_source=topo_source,
                    dark_pole_ids=confirmed_dark,
                    evidence={"scheduled_outage": so.id},
                    suppress=True,
                    suppress_reason=f"Scheduled outage {so.id}: {so.reason}",
                )
            ]
        return [
            LocalizationResult(
                fault_type="dt",
                feeder_id=dt.feeder_id,
                dt_id=dt.id,
                upstream_pole_id=None,
                downstream_pole_id=None,
                asset_label=f"Distribution transformer {dt.id} / HT fuse",
                lat=dt.lat,
                lon=dt.lon,
                pincode=_pincode_for(poles, poles),
                affected_poles=len(poles),
                affected_households_est=dt.households_served,
                confidence=0.88 if topo_source == "recorded" else 0.72,
                confidence_reason=(
                    "No live poles remain under this DT; consistent with DT or HT fuse failure"
                    + ("" if topo_source == "recorded" else " (topology inferred)")
                ),
                topology_source=topo_source,
                dark_pole_ids=confirmed_dark,
                evidence={"live": confirmed_live, "dark": confirmed_dark, "unknown": unknown},
            )
        ]

    for root in filtered_roots:
        pole = by_id[root]
        subtree = {root} | descendants(children, root)
        subtree_dark = [pid for pid in subtree if pid in dark_set]
        subtree_live = [pid for pid in subtree if pid in live_set]

        # Isolated sensor failure: dark pole with live descendants
        if subtree_live and root in dark_set:
            # physically impossible as line fault
            results.append(
                LocalizationResult(
                    fault_type="sensor",
                    feeder_id=dt.feeder_id,
                    dt_id=dt.id,
                    upstream_pole_id=_effective_parent(pole, topo_source),
                    downstream_pole_id=root,
                    asset_label=f"Sensor anomaly at {root}",
                    lat=pole.lat,
                    lon=pole.lon,
                    pincode=pole.pincode,
                    affected_poles=1,
                    affected_households_est=0,
                    confidence=0.95,
                    confidence_reason="Pole reports dark while downstream poles remain live — impossible for a span fault",
                    topology_source=topo_source,
                    dark_pole_ids=[root],
                    evidence={"live_descendants": subtree_live},
                    suppress=True,
                    suppress_reason="Dead sensor / lamp circuit, not an outage",
                )
            )
            continue

        # Single dark with no device neighbors confirming — low confidence, may suppress if alone
        if len(subtree_dark) == 1 and not subtree_live:
            kids = children.get(root, [])
            # If it has children without devices, still could be real; keep with low confidence
            pass

        parent_id = pole.inferred_parent_id if topo_source == "inferred" else (pole.parent_pole_id or dt.id)
        if topo_source == "recorded":
            parent_id = pole.parent_pole_id or dt.id

        if parent_id == dt.id:
            upstream_label = f"DT {dt.id}"
            ulat, ulon = dt.lat, dt.lon
            upstream_pole_id = None
        else:
            parent_pole = by_id.get(parent_id)
            if parent_pole:
                upstream_label = parent_id
                ulat, ulon = parent_pole.lat, parent_pole.lon
                upstream_pole_id = parent_id
            else:
                upstream_label = parent_id or "unknown"
                ulat, ulon = pole.lat, pole.lon
                upstream_pole_id = parent_id

        # Fault location: midpoint of span
        lat = (ulat + pole.lat) / 2
        lon = (ulon + pole.lon) / 2
        span_m = haversine_m(ulat, ulon, pole.lat, pole.lon)

        conf = 0.9 if topo_source == "recorded" else 0.62
        reason_parts = [
            f"Live/dark boundary between {upstream_label} and {root}",
            f"{len(subtree_dark)} poles dark in subtree",
        ]
        if topo_source == "inferred":
            reason_parts.append("Topology inferred from GPS; span may be adjacent to true edge")
            if span_m > 120:
                conf -= 0.1
                reason_parts.append(f"Long inferred span ({span_m:.0f} m) — possible missing intermediate pole")
        if len(subtree_dark) == 1:
            conf -= 0.15
            reason_parts.append("Single dark pole — could be late telemetry from neighbors")

        # Poles without devices on boundary widen uncertainty
        if not pole.has_device:
            conf -= 0.1
            reason_parts.append("Boundary pole has no device; location is a range estimate")

        conf = max(0.35, min(0.95, conf))

        if so:
            results.append(
                LocalizationResult(
                    fault_type="span",
                    feeder_id=dt.feeder_id,
                    dt_id=dt.id,
                    upstream_pole_id=upstream_pole_id,
                    downstream_pole_id=root,
                    asset_label=f"Span {upstream_label} -> {root}",
                    lat=lat,
                    lon=lon,
                    pincode=_pincode_for([pole], poles),
                    affected_poles=len(subtree_dark),
                    affected_households_est=_estimate_households(dt, len(subtree_dark), len(poles)),
                    confidence=conf,
                    confidence_reason="; ".join(reason_parts),
                    topology_source=topo_source,
                    dark_pole_ids=subtree_dark,
                    evidence={"scheduled_outage": so.id},
                    suppress=True,
                    suppress_reason=f"Scheduled outage {so.id}: {so.reason}",
                )
            )
            continue

        results.append(
            LocalizationResult(
                fault_type="span",
                feeder_id=dt.feeder_id,
                dt_id=dt.id,
                upstream_pole_id=upstream_pole_id,
                downstream_pole_id=root,
                asset_label=f"Span {upstream_label} -> {root}",
                lat=lat,
                lon=lon,
                pincode=_pincode_for([pole], poles),
                affected_poles=len(subtree),
                affected_households_est=_estimate_households(dt, len(subtree_dark), len(poles)),
                confidence=conf,
                confidence_reason="; ".join(reason_parts),
                topology_source=topo_source,
                dark_pole_ids=sorted(subtree_dark),
                evidence={
                    "upstream": upstream_label,
                    "downstream": root,
                    "span_m": round(span_m, 1),
                    "unknown_in_subtree": [pid for pid in subtree if pid in unknown],
                },
            )
        )

    return results


def localize_feeder(db: Session, feeder_id: str, now: datetime | None = None) -> LocalizationResult | None:
    now = now or utcnow()
    dts = db.query(DistributionTransformer).filter(DistributionTransformer.feeder_id == feeder_id).all()
    if not dts:
        return None
    dark_dts = 0
    total_dark = 0
    total_poles = 0
    for dt in dts:
        poles = db.query(Pole).filter(Pole.dt_id == dt.id).all()
        total_poles += len(poles)
        states = [_is_energized_effective(p, now) for p in poles]
        live = sum(1 for s in states if s is True)
        dark = sum(1 for s in states if s is False)
        total_dark += dark
        if live == 0 and dark >= 2:
            dark_dts += 1
    if dark_dts < max(2, int(0.7 * len(dts))):
        return None

    so = None
    for dt in dts:
        so = _scheduled_suppression(db, feeder_id, dt.id, now)
        if so and so.scope == "feeder":
            break
        so = None

    lat = sum(d.lat for d in dts) / len(dts)
    lon = sum(d.lon for d in dts) / len(dts)
    hh = sum(d.households_served for d in dts)
    return LocalizationResult(
        fault_type="feeder",
        feeder_id=feeder_id,
        dt_id=None,
        upstream_pole_id=None,
        downstream_pole_id=None,
        asset_label=f"11 kV feeder {feeder_id}",
        lat=lat,
        lon=lon,
        pincode=None,
        affected_poles=total_poles,
        affected_households_est=hh,
        confidence=0.85,
        confidence_reason=f"{dark_dts}/{len(dts)} DTs fully dark on feeder",
        topology_source="recorded",
        dark_pole_ids=[],
        evidence={"dark_dts": dark_dts, "dts": len(dts)},
        suppress=bool(so),
        suppress_reason=(f"Scheduled outage {so.id}: {so.reason}" if so else None),
    )


def ticket_signature(result: LocalizationResult) -> str:
    if result.fault_type == "feeder":
        return f"feeder:{result.feeder_id}"
    if result.fault_type == "dt":
        return f"dt:{result.dt_id}"
    return f"span:{result.dt_id}:{result.upstream_pole_id}:{result.downstream_pole_id}"


def find_open_ticket(db: Session, result: LocalizationResult) -> Ticket | None:
    q = db.query(Ticket).filter(Ticket.status.in_(list(ACTIVE_STATUSES)))
    if result.fault_type == "feeder":
        return q.filter(Ticket.fault_type == "feeder", Ticket.feeder_id == result.feeder_id).first()
    if result.fault_type == "dt":
        return q.filter(Ticket.fault_type == "dt", Ticket.dt_id == result.dt_id).first()
    return (
        q.filter(
            Ticket.fault_type == "span",
            Ticket.dt_id == result.dt_id,
            Ticket.downstream_pole_id == result.downstream_pole_id,
        ).first()
    )


def create_ticket_from_result(db: Session, result: LocalizationResult) -> Ticket | None:
    if result.suppress or result.fault_type == "sensor":
        return None
    existing = find_open_ticket(db, result)
    if existing:
        # Refresh counts
        existing.affected_poles = result.affected_poles
        existing.affected_households_est = result.affected_households_est
        existing.confidence = result.confidence
        existing.confidence_reason = result.confidence_reason
        existing.dark_pole_ids = json.dumps(result.dark_pole_ids)
        existing.updated_at = utcnow()
        db.commit()
        db.refresh(existing)
        return existing

    tid = f"T-{utcnow().strftime('%Y%m%d%H%M%S')}-{result.fault_type[:1].upper()}{abs(hash(ticket_signature(result))) % 10000:04d}"
    ticket = Ticket(
        id=tid,
        status="detected",
        fault_type=result.fault_type,
        feeder_id=result.feeder_id,
        dt_id=result.dt_id,
        upstream_pole_id=result.upstream_pole_id,
        downstream_pole_id=result.downstream_pole_id,
        asset_label=result.asset_label,
        lat=result.lat,
        lon=result.lon,
        pincode=result.pincode,
        affected_poles=result.affected_poles,
        affected_households_est=result.affected_households_est,
        confidence=result.confidence,
        confidence_reason=result.confidence_reason,
        topology_source=result.topology_source,
        dark_pole_ids=json.dumps(result.dark_pole_ids),
        evidence=json.dumps(result.evidence),
    )
    db.add(ticket)
    db.commit()
    db.refresh(ticket)
    return ticket


def run_detection_for_affected(
    db: Session,
    pole_ids: list[str] | None = None,
    feeder_ids: list[str] | None = None,
) -> list[Ticket]:
    """Run localization for DTs/feeders touched by recent changes."""
    created: list[Ticket] = []
    dt_ids: set[str] = set()
    feeders: set[str] = set(feeder_ids or [])

    if pole_ids:
        poles = db.query(Pole).filter(Pole.id.in_(pole_ids)).all()
        for p in poles:
            dt_ids.add(p.dt_id)
            feeders.add(p.feeder_id)
    elif not feeders:
        # Full scan — used by simulator after bulk inject
        for (dt_id,) in db.query(Pole.dt_id).distinct():
            dt_ids.add(dt_id)
        for (fid,) in db.query(Pole.feeder_id).distinct():
            feeders.add(fid)

    # Feeder-level first
    feeder_faults = set()
    for fid in feeders:
        fr = localize_feeder(db, fid)
        if fr and not fr.suppress:
            feeder_faults.add(fid)
            t = create_ticket_from_result(db, fr)
            if t:
                created.append(t)

    for dt_id in dt_ids:
        dt = db.get(DistributionTransformer, dt_id)
        if dt and dt.feeder_id in feeder_faults:
            continue  # covered by feeder ticket
        for result in localize_dt(db, dt_id):
            t = create_ticket_from_result(db, result)
            if t:
                created.append(t)
    return created


def verify_restorations(db: Session) -> list[Ticket]:
    """Auto-verify tickets when affected poles are live again."""
    updated: list[Ticket] = []
    now = utcnow()
    tickets = db.query(Ticket).filter(Ticket.status.in_(["detected", "acknowledged", "crew_assigned", "resolved"])).all()
    for ticket in tickets:
        dark_ids = json.loads(ticket.dark_pole_ids or "[]")
        if ticket.fault_type == "feeder":
            poles = db.query(Pole).filter(Pole.feeder_id == ticket.feeder_id).all()
        elif ticket.fault_type == "dt":
            poles = db.query(Pole).filter(Pole.dt_id == ticket.dt_id).all()
        elif dark_ids:
            poles = db.query(Pole).filter(Pole.id.in_(dark_ids)).all()
        else:
            continue

        reporting = [p for p in poles if p.has_device]
        if not reporting:
            continue
        still_dark = [p for p in reporting if _is_energized_effective(p, now) is False]
        if still_dark:
            continue
        # Enough live confirmation
        live = [p for p in reporting if _is_energized_effective(p, now) is True]
        if len(live) < max(1, int(0.6 * len(reporting))):
            continue

        ticket.status = "verified"
        ticket.verified_at = now
        ticket.updated_at = now
        ticket.operator_note = (ticket.operator_note or "") + "\n[auto] Restoration verified from telemetry."
        updated.append(ticket)
    if updated:
        db.commit()
    return updated
