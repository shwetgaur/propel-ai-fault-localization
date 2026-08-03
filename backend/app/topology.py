"""Geographic topology inference for DTs missing recorded pole order.

Strategy: build a radial spanning tree rooted at the DT. Each pole's parent is
the nearest already-attached node (DT or pole) that is closer to the DT than
the pole itself. This recovers the true tree when poles are spaced along
roads; it fails when two branches run parallel within GPS noise (~4m) or when
a spur folds back toward the transformer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


@dataclass
class Node:
    id: str
    lat: float
    lon: float


def infer_parents(
    dt_id: str,
    dt_lat: float,
    dt_lon: float,
    poles: list[Node],
) -> dict[str, str | None]:
    """Return pole_id -> parent_id (parent may be another pole_id or the DT sentinel)."""
    if not poles:
        return {}

    remaining = {p.id: p for p in poles}
    attached: dict[str, Node] = {}
    parents: dict[str, str | None] = {}
    dist_to_dt = {
        p.id: haversine_m(p.lat, p.lon, dt_lat, dt_lon) for p in poles
    }

    # Seed: attach nearest pole to DT
    first = min(remaining.values(), key=lambda p: dist_to_dt[p.id])
    parents[first.id] = dt_id
    attached[first.id] = first
    del remaining[first.id]

    while remaining:
        best_pole = None
        best_parent = None
        best_cost = float("inf")
        for pid, pole in remaining.items():
            # Prefer parents closer to DT than this pole (radial growth)
            candidates = [(dt_id, dt_lat, dt_lon)] + [
                (aid, a.lat, a.lon) for aid, a in attached.items()
                if dist_to_dt[aid] <= dist_to_dt[pid] + 15.0  # 15m slack for GPS
            ]
            for cid, clat, clon in candidates:
                d = haversine_m(pole.lat, pole.lon, clat, clon)
                # Soft penalty for long jumps (unlikely adjacent spans)
                cost = d + max(0.0, d - 80.0) * 2.0
                if cost < best_cost:
                    best_cost = cost
                    best_pole = pole
                    best_parent = cid
        assert best_pole is not None and best_parent is not None
        parents[best_pole.id] = best_parent
        attached[best_pole.id] = best_pole
        del remaining[best_pole.id]

    return parents


def children_map(parents: dict[str, str | None], root_id: str) -> dict[str, list[str]]:
    children: dict[str, list[str]] = {root_id: []}
    for child, parent in parents.items():
        children.setdefault(child, [])
        if parent is None:
            continue
        children.setdefault(parent, []).append(child)
    return children


def descendants(children: dict[str, list[str]], node_id: str) -> set[str]:
    out: set[str] = set()
    stack = list(children.get(node_id, []))
    while stack:
        n = stack.pop()
        if n in out:
            continue
        out.add(n)
        stack.extend(children.get(n, []))
    return out
