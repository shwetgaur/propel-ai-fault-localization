"""Generate a realistic synthetic subdivision network and seed the DB."""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from .db import (
    DistributionTransformer,
    Feeder,
    Pole,
    ScheduledOutage,
    SimulatorScenario,
    TelemetryEvent,
    Ticket,
)
from .topology import Node, infer_parents

RNG = random.Random(42)

# Bangalore-ish bounding box for visual map
BASE_LAT = 12.935
BASE_LON = 77.580


def _offset(lat: float, lon: float, north_m: float, east_m: float) -> tuple[float, float]:
    dlat = north_m / 111_320.0
    dlon = east_m / (111_320.0 * math.cos(math.radians(lat)))
    return lat + dlat, lon + dlon


def generate_and_seed(db: Session, *, poles_target: int = 3200) -> dict:
    db.query(TelemetryEvent).delete()
    db.query(Ticket).delete()
    db.query(Pole).delete()
    db.query(DistributionTransformer).delete()
    db.query(Feeder).delete()
    db.query(ScheduledOutage).delete()
    db.query(SimulatorScenario).delete()
    db.commit()

    substations = [f"S-{i:02d}" for i in range(1, 5)]
    feeders: list[Feeder] = []
    feeder_ids: list[str] = []
    for i in range(1, 21):
        ss = substations[(i - 1) % 4]
        fid = f"F-{ss[-2:]}-{i:02d}"
        feeder_ids.append(fid)
        feeders.append(Feeder(id=fid, substation_id=ss, name=f"Feeder {fid}"))
    db.add_all(feeders)

    # ~50 DTs, ~60% missing topology
    n_dts = 48
    poles_budget = poles_target
    dts: list[DistributionTransformer] = []
    all_poles: list[Pole] = []
    pole_counter = 1

    for di in range(n_dts):
        feeder_id = feeder_ids[di % len(feeder_ids)]
        row, col = divmod(di, 8)
        dt_lat, dt_lon = _offset(BASE_LAT, BASE_LON, row * 450 + RNG.uniform(-40, 40), col * 520 + RNG.uniform(-40, 40))
        topology_known = di % 5 != 0 and di % 5 != 1  # ~40% known (2 of 5), ~60% missing
        # Actually: 0,1 missing of every 5 = 40% missing. Need 60% missing.
        topology_known = di % 5 in (0, 1)  # only 40% known

        households = RNG.randint(80, 420)
        capacity = RNG.choice([100, 160, 250, 315])
        dt_id = f"D-{di + 1:04d}"
        dt = DistributionTransformer(
            id=dt_id,
            feeder_id=feeder_id,
            lat=dt_lat,
            lon=dt_lon,
            capacity_kva=capacity,
            households_served=households,
            topology_known=topology_known,
        )
        dts.append(dt)

        # poles per DT: vary 25–110 to hit ~3200
        remaining_dts = n_dts - di
        n_poles = max(20, min(110, poles_budget // remaining_dts + RNG.randint(-8, 12)))
        poles_budget -= n_poles

        # Build a radial line with 1–3 spurs
        main_len = int(n_poles * 0.7)
        spur_lens = []
        leftover = n_poles - main_len
        while leftover > 3 and len(spur_lens) < 3:
            sl = min(leftover, RNG.randint(4, max(5, leftover // 2 + 1)))
            spur_lens.append(sl)
            leftover -= sl
        main_len += leftover

        heading = RNG.uniform(0, 2 * math.pi)
        poles_local: list[tuple[str, float, float, int | None, str | None]] = []
        # (pole_id, lat, lon, seq, parent)

        prev_id = None
        for seq in range(1, main_len + 1):
            step = RNG.uniform(28, 55)
            lat, lon = _offset(
                dt_lat,
                dt_lon,
                math.cos(heading) * step * seq + RNG.uniform(-3, 3),
                math.sin(heading) * step * seq + RNG.uniform(-3, 3),
            )
            pid = f"P-{pole_counter:06d}"
            pole_counter += 1
            poles_local.append((pid, lat, lon, seq if topology_known else None, prev_id if topology_known else None))
            prev_id = pid

        # Spurs branch from random main poles
        main_ids = [p[0] for p in poles_local]
        for si, slen in enumerate(spur_lens):
            branch_from = main_ids[RNG.randint(2, max(2, len(main_ids) - 2))]
            branch_pole = next(p for p in poles_local if p[0] == branch_from)
            spur_heading = heading + RNG.choice([-1, 1]) * RNG.uniform(0.6, 1.4)
            prev_id = branch_from if topology_known else None
            for j in range(1, slen + 1):
                step = RNG.uniform(28, 50)
                lat, lon = _offset(
                    branch_pole[1],
                    branch_pole[2],
                    math.cos(spur_heading) * step * j + RNG.uniform(-3, 3),
                    math.sin(spur_heading) * step * j + RNG.uniform(-3, 3),
                )
                pid = f"P-{pole_counter:06d}"
                pole_counter += 1
                seq = (branch_pole[3] or 0) + j if topology_known else None
                poles_local.append((pid, lat, lon, seq, prev_id if topology_known else None))
                prev_id = pid

        # Materialize poles
        dt_poles: list[Pole] = []
        for pid, lat, lon, seq, parent in poles_local:
            has_device = RNG.random() > 0.09
            fw = "1.2.4" if (has_device and RNG.random() < 0.08) else RNG.choice(["1.3.1", "1.4.0", "1.4.2"])
            pincode = None if RNG.random() < 0.03 else str(560001 + (di % 80))
            device_id = f"KSPDB-{feeder_id}-{dt_id}-{pid[-4:]}" if has_device else None
            pole = Pole(
                id=pid,
                lat=lat,
                lon=lon,
                feeder_id=feeder_id,
                dt_id=dt_id,
                seq_on_line=seq,
                parent_pole_id=parent,
                pole_type=RNG.choice(["LT-9m-PCC", "LT-8m-Steel", "LT-9m-PSC"]),
                ward=f"W-{(di % 40) + 1:03d}",
                pincode=pincode,
                device_id=device_id,
                has_device=has_device,
                firmware=fw if has_device else "n/a",
                energized=True,
                last_seq=0,
                last_seen_at=datetime.now(timezone.utc),
                device_online=has_device,
            )
            dt_poles.append(pole)
            all_poles.append(pole)

        # Always compute inferred parents for unknown (and as backup for known)
        nodes = [Node(id=p.id, lat=p.lat, lon=p.lon) for p in dt_poles]
        inferred = infer_parents(dt_id, dt_lat, dt_lon, nodes)
        for p in dt_poles:
            p.inferred_parent_id = inferred.get(p.id)

        db.add(dt)
        db.add_all(dt_poles)

    # Sample scheduled outages (one active-window friendly, one future)
    now = datetime.now(timezone.utc)
    outages = [
        ScheduledOutage(
            id="SO-SEED-001",
            scope="feeder",
            target_id=feeder_ids[0],
            start=now - timedelta(hours=1),
            end=now + timedelta(hours=2),
            reason="Planned maintenance - jumper replacement",
            cancelled=False,
        ),
        ScheduledOutage(
            id="SO-SEED-002",
            scope="dt",
            target_id="D-0003",
            start=now + timedelta(hours=6),
            end=now + timedelta(hours=8),
            reason="Load shedding",
            cancelled=False,
        ),
        ScheduledOutage(
            id="SO-SEED-003",
            scope="dt",
            target_id="D-0005",
            start=now - timedelta(hours=3),
            end=now - timedelta(hours=1),
            reason="Completed maintenance",
            cancelled=False,
        ),
    ]
    db.add_all(outages)
    db.add(SimulatorScenario(active_faults="[]", last_action="seeded"))
    db.commit()

    known = sum(1 for d in dts if d.topology_known)
    return {
        "feeders": len(feeders),
        "transformers": len(dts),
        "poles": len(all_poles),
        "topology_known_dts": known,
        "topology_missing_dts": len(dts) - known,
        "devices": sum(1 for p in all_poles if p.has_device),
    }
