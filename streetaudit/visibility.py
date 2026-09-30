"""Which panoramas can see a building, and which views to use.

A point on the footprint outline is visible from a panorama when the straight line between them crosses
no footprint. The footprints are the only blockers known: trees, walls and unmapped buildings are not.
"""
from __future__ import annotations

import math

import geopandas as gpd
import numpy as np
import shapely
from shapely.geometry import Polygon

from .config import Settings

SQUARE_ON_COS = 0.42   # a wall point counts as seen square-on within about 65 degrees of its outward direction


def see(poly: Polygon, cam, tree: shapely.STRtree, s: Settings) -> dict | None:
    """What a camera at `cam` (x, y) sees of a footprint: its longest visible stretch of outline.

    Returns None when nothing is visible. The keys starting with '_' are used for view selection only.
    """
    ring = poly.exterior
    n = max(8, int(ring.length / s.outline_step_m))
    q = shapely.line_interpolate_point(ring, np.linspace(0, ring.length, n, endpoint=False))
    qx, qy = shapely.get_x(q), shapely.get_y(q)
    dx, dy = qx - cam[0], qy - cam[1]
    d = np.maximum(np.hypot(dx, dy), 1e-9)
    # stop each sight line 5 cm short of the outline so touching the target itself does not count
    k = np.clip((d - 0.05) / d, 0, 1)
    ends = np.c_[cam[0] + dx * k, cam[1] + dy * k]
    lines = shapely.linestrings(np.stack([np.repeat([[cam[0], cam[1]]], n, axis=0), ends], axis=1))
    visible = np.ones(n, dtype=bool)
    visible[np.unique(tree.query(lines, predicate="intersects")[0])] = False
    if not visible.any():
        return None

    # how square-on each visible point is seen
    tx, ty = np.roll(qx, -1) - np.roll(qx, 1), np.roll(qy, -1) - np.roll(qy, 1)
    tl = np.maximum(np.hypot(tx, ty), 1e-9)
    nx, ny = ty / tl, -tx / tl                       # outward for a counter-clockwise ring
    if not ring.is_ccw:
        nx, ny = -nx, -ny
    cos_inc = (-dx / d) * nx + (-dy / d) * ny
    good = {int(i): float(cos_inc[i]) for i in np.flatnonzero(visible & (cos_inc >= SQUARE_ON_COS))}

    idx = _longest_run(visible)
    bears = np.degrees(np.arctan2(dx[idx], dy[idx])) % 360
    mid = math.degrees(math.atan2(np.sin(np.radians(bears)).mean(), np.cos(np.radians(bears)).mean())) % 360
    off = (bears - mid + 180) % 360 - 180
    i_left, i_right = idx[int(np.argmin(off))], idx[int(np.argmax(off))]
    span = float(off.max() - off.min())
    dist_left, dist_right = float(d[i_left]), float(d[i_right])
    # width of the visible part as it appears from the camera: the distance between its two ends,
    # measured at right angles to the viewing direction
    m = math.radians(mid)
    apparent = abs((qx[i_right] - qx[i_left]) * math.cos(m) - (qy[i_right] - qy[i_left]) * math.sin(m))
    return {
        "_good": good, "_step": ring.length / n,
        "px": float(cam[0]), "py": float(cam[1]),
        "visible_m": round(len(idx) * ring.length / n, 2),
        "left": [float(qx[i_left]), float(qy[i_left])], "right": [float(qx[i_right]), float(qy[i_right])],
        "bearing_left": round((mid + float(off.min())) % 360, 2),
        "bearing_right": round((mid + float(off.max())) % 360, 2),
        "span_deg": round(span, 2),
        "dist_near": round(float(d[idx].min()), 2), "dist_left": round(dist_left, 2), "dist_right": round(dist_right, 2),
        "apparent_m": round(float(apparent), 2),
    }


def _longest_run(visible: np.ndarray) -> np.ndarray:
    """Indices of the longest stretch of True values on a closed ring."""
    n = len(visible)
    if visible.all():
        return np.arange(n)
    start = int(np.argmax(~visible))                 # begin at a hidden point so runs do not wrap
    rolled = np.roll(visible, -start)
    best_len = best_at = cur_len = cur_at = 0
    for i, v in enumerate(rolled):
        if v:
            if cur_len == 0:
                cur_at = i
            cur_len += 1
            if cur_len > best_len:
                best_len, best_at = cur_len, cur_at
        else:
            cur_len = 0
    return (best_at + start + np.arange(best_len)) % n


def candidate_views(poly: Polygon, panos: gpd.GeoDataFrame, tree: shapely.STRtree, s: Settings) -> list[dict]:
    """Usable views of a footprint, best first. `panos` must already be the usable panoramas near it."""
    cands = []
    for pano in panos.itertuples():
        v = see(poly, (pano.geometry.x, pano.geometry.y), tree, s)
        if v is None or v["visible_m"] < s.min_visible_m:
            continue
        if not s.min_span_deg <= v["span_deg"] <= s.max_span_deg or v["dist_near"] < s.min_view_dist:
            continue
        # views from closer than 6 m or farther than 18 m are worth less
        penalty = 0.10 * max(0, 6 - v["dist_near"]) + 0.05 * max(0, v["dist_near"] - 18)
        v["quality"] = round(1 - min(0.8, penalty), 2)
        v["score"] = round(v["apparent_m"] * v["quality"], 2)
        v["pano_id"], v["pano_date"] = pano.pano_id, pano.date
        cands.append(v)
    cands.sort(key=lambda v: -v["score"])
    return cands


def choose_views(cands: list[dict], s: Settings) -> list[dict]:
    """Up to `max_views` views, each the one that shows the most wall not yet seen square-on.

    A view that faces one wall squarely is preferred to a corner view that sees two walls at a slant:
    the marks then hold one facade, which is easier to judge. Going on to the wall not yet seen makes
    every street-facing side get a view; a shop is often on the short side of a building.
    """
    pool = list(cands)
    chosen: list[dict] = []
    covered: dict[int, float] = {}

    def gain(v):
        return sum(max(0.0, c - covered.get(i, 0.0)) for i, c in v["_good"].items()) * v["_step"]

    while pool and len(chosen) < s.max_views:
        apart = [v for v in pool if all(math.hypot(v["px"] - c["px"], v["py"] - c["py"]) >= 6 for c in chosen)]
        if not apart:
            break
        if not chosen:
            # view 1 is the reference the other views are compared with: it has to be a close one.
            # Far views cross open ground and can land on a building that is not on the map.
            close = [v for v in apart if v["dist_near"] <= s.near_view_dist]
            apart = close or apart
        best = max(apart, key=lambda v: (round(gain(v) * v.get("quality", 1.0), 1), v["score"]))
        new_wall = round(gain(best), 1)
        if chosen and new_wall < s.min_new_wall_m:
            break
        best["new_wall_m"] = new_wall
        chosen.append(best)
        for i, c in best["_good"].items():
            covered[i] = max(covered.get(i, 0.0), c)
        pool.remove(best)
    return [{k: v for k, v in view.items() if not k.startswith("_")} for view in chosen]


def access_class(cands: list[dict], s: Settings) -> str:
    """'street-facing', 'not street-facing' (seen only from beyond `near_view_dist`) or 'not visible'."""
    if not cands:
        return "not visible"
    if min(v["dist_near"] for v in cands) > s.near_view_dist:
        return "not street-facing"
    return "street-facing"


def usable_panoramas(panos: gpd.GeoDataFrame, blockers: np.ndarray, s: Settings) -> gpd.GeoDataFrame:
    """The source's own panoramas, at positions that make sense.

    A panorama that plots inside a footprint cannot be there: in narrow lanes its position is simply off
    by a metre or two. It is moved to the open ground just outside that footprint when the way out is at
    most `max_snap_m`; deeper ones are not used. Without this, the houses of narrow lanes lose exactly
    the panoramas that stand in front of them.
    """
    tree = shapely.STRtree(blockers)
    own = panos[panos["own"]].copy()
    own["snapped"] = False
    keep = np.ones(len(own), dtype=bool)
    pts = own.geometry.values
    hit_pts, hit_polys = tree.query(pts, predicate="within")
    for i, j in zip(hit_pts, hit_polys):
        p, ring = pts[i], blockers[j].exterior
        edge = ring.interpolate(ring.project(p))
        depth = p.distance(edge)
        moved = None
        if 0 < depth <= s.max_snap_m:
            for clear in (0.5, 1.0):
                k = (depth + clear) / depth
                cand = shapely.Point(p.x + (edge.x - p.x) * k, p.y + (edge.y - p.y) * k)
                if len(tree.query(cand, predicate="within")) == 0:
                    moved = cand
                    break
        if moved is None:
            keep[i] = False
        else:
            own.iat[i, own.columns.get_loc("geometry")] = moved
            own.iat[i, own.columns.get_loc("snapped")] = True
    return own[keep]


def plan_views(units: gpd.GeoDataFrame, panos: gpd.GeoDataFrame, blockers: np.ndarray, s: Settings,
               progress=None) -> dict[str, dict]:
    """For every unit: its access class and the views to fetch."""
    tree = shapely.STRtree(blockers)
    usable = usable_panoramas(panos, blockers, s)
    pano_tree = shapely.STRtree(usable.geometry.values)
    plan = {}
    for n, unit in enumerate(units.itertuples(), 1):
        near = usable.iloc[pano_tree.query(unit.geometry, predicate="dwithin", distance=s.max_view_dist)]
        cands = candidate_views(unit.geometry, near, tree, s)
        access = access_class(cands, s)
        plan[unit.unit_id] = {
            "building_id": unit.building_id, "part": unit.part, "n_parts": unit.n_parts,
            "access": access, "n_candidates": len(cands),
            "views": choose_views(cands, s) if access == "street-facing" else [],
            "centroid": [unit.geometry.centroid.x, unit.geometry.centroid.y],
            "record": {"road": unit.road, "usage": unit.usage, "storeys": unit.storeys},
        }
        if progress and n % 250 == 0:
            progress(f"views planned for {n}/{len(units)} buildings")
    return plan
