"""Localization unit tests — the correctness that matters."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure backend package importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import (  # noqa: E402
    Base,
    DistributionTransformer,
    Feeder,
    Pole,
    ScheduledOutage,
    SessionLocal,
    make_engine,
)
from app.localization import localize_dt, localize_feeder  # noqa: E402
from app.topology import Node, infer_parents  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402


@pytest.fixture()
def db():
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = TestSession()
    yield session
    session.close()


def _add_line(db, *, topology_known=True):
    """
    DT -- P1 -- P2 -- P3 -- P4
                 \\
                  P5 -- P6
    """
    db.add(Feeder(id="F-01", substation_id="S-01", name="F1"))
    db.add(
        DistributionTransformer(
            id="D-1",
            feeder_id="F-01",
            lat=12.0,
            lon=77.0,
            capacity_kva=250,
            households_served=300,
            topology_known=topology_known,
        )
    )
    coords = {
        "P1": (12.001, 77.0),
        "P2": (12.002, 77.0),
        "P3": (12.003, 77.0),
        "P4": (12.004, 77.0),
        "P5": (12.002, 77.001),
        "P6": (12.002, 77.002),
    }
    parents = {"P1": "D-1", "P2": "P1", "P3": "P2", "P4": "P3", "P5": "P2", "P6": "P5"}
    seq = {"P1": 1, "P2": 2, "P3": 3, "P4": 4, "P5": 3, "P6": 4}
    now = datetime.now(timezone.utc)
    poles = []
    for pid, (lat, lon) in coords.items():
        poles.append(
            Pole(
                id=pid,
                lat=lat,
                lon=lon,
                feeder_id="F-01",
                dt_id="D-1",
                seq_on_line=seq[pid] if topology_known else None,
                parent_pole_id=parents[pid] if topology_known else None,
                ward="W-1",
                pincode="560001",
                device_id=f"DEV-{pid}",
                has_device=True,
                firmware="1.4.2",
                energized=True,
                last_seq=1,
                last_seen_at=now,
                device_online=True,
            )
        )
    # Always set inferred parents
    nodes = [Node(id=p.id, lat=p.lat, lon=p.lon) for p in poles]
    inferred = infer_parents("D-1", 12.0, 77.0, nodes)
    for p in poles:
        p.inferred_parent_id = inferred[p.id]
    db.add_all(poles)
    db.commit()
    return poles


def test_span_fault_boundary_recorded(db):
    _add_line(db, topology_known=True)
    # Fault between P2 and P3 → P3,P4 dark
    for pid in ("P3", "P4"):
        p = db.get(Pole, pid)
        p.energized = False
    db.commit()
    results = [r for r in localize_dt(db, "D-1") if not r.suppress]
    assert len(results) == 1
    r = results[0]
    assert r.fault_type == "span"
    assert r.downstream_pole_id == "P3"
    assert r.upstream_pole_id == "P2"
    assert "P3" in r.dark_pole_ids and "P4" in r.dark_pole_ids
    assert "P5" not in r.dark_pole_ids


def test_multiple_simultaneous_span_faults(db):
    _add_line(db, topology_known=True)
    # Fault P2-P3 and P2-P5
    for pid in ("P3", "P4", "P5", "P6"):
        db.get(Pole, pid).energized = False
    db.commit()
    results = [r for r in localize_dt(db, "D-1") if not r.suppress and r.fault_type == "span"]
    downs = {r.downstream_pole_id for r in results}
    assert downs == {"P3", "P5"}


def test_sensor_failure_suppressed(db):
    _add_line(db, topology_known=True)
    db.get(Pole, "P2").energized = False  # children P3.. still live
    db.commit()
    results = localize_dt(db, "D-1")
    assert all(r.suppress or r.fault_type == "sensor" for r in results)
    assert not any(not r.suppress and r.fault_type == "span" for r in results)


def test_dt_fault(db):
    _add_line(db, topology_known=True)
    for p in db.query(Pole).all():
        p.energized = False
    db.commit()
    results = [r for r in localize_dt(db, "D-1") if not r.suppress]
    assert len(results) == 1
    assert results[0].fault_type == "dt"


def test_scheduled_outage_suppresses(db):
    _add_line(db, topology_known=True)
    now = datetime.now(timezone.utc)
    db.add(
        ScheduledOutage(
            id="SO-1",
            scope="dt",
            target_id="D-1",
            start=now - timedelta(minutes=10),
            end=now + timedelta(hours=1),
            reason="Load shedding",
        )
    )
    for p in db.query(Pole).all():
        p.energized = False
    db.commit()
    results = localize_dt(db, "D-1")
    assert results
    assert all(r.suppress for r in results)


def test_inferred_topology_still_localizes(db):
    _add_line(db, topology_known=False)
    for pid in ("P3", "P4"):
        db.get(Pole, pid).energized = False
    db.commit()
    results = [r for r in localize_dt(db, "D-1") if not r.suppress]
    assert len(results) >= 1
    assert results[0].topology_source == "inferred"
    assert results[0].confidence < 0.85
