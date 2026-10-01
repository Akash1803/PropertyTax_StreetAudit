"""Street level: stretches of road, the buildings on each, along-road pictures and the street summary.

The road layer is the analyst's own line layer and is only read. Its lines are split at junctions into
stretches, and every building is assigned to the stretch it is photographed from. The summary of a
stretch is worked out here, in code, from the building check, the survey's zones and the reading of
the along-road pictures.
"""
from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from difflib import SequenceMatcher

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely.geometry import LineString, Point
from shapely.ops import substring

from .geometry import bearing

# the answers a street reading may give; write_street_readings refuses anything else
ROAD_TYPES = ("main road", "street", "lane", "dead end")
SURFACES = ("tar", "concrete", "paver", "mud", "mixed", "cannot tell")
WIDTHS = ("1 lane", "2 lanes", "wide", "cannot tell")
FOOTPATHS = ("both sides", "one side", "none", "cannot tell")
DRAINS = ("open", "covered", "none seen", "cannot tell")
LIGHTS = ("yes", "none seen", "cannot tell")
SHOPS = ("none", "few", "about half", "mostly", "cannot tell")
TRADE = ("Commercial", "Mixed", "Office / Lodge / Theater / Restaurants")


# ----------------------------------------------------------------------------------------- stretches

def load_roads(path, epsg: int, name_field: str) -> gpd.GeoDataFrame:
    """The road lines in the metric CRS: one row per single line, with the drawn name (or None)."""
    roads = gpd.read_file(path).to_crs(epsg)
    roads = roads[roads.geometry.notna() & ~roads.geometry.is_empty]
    roads = roads.explode(index_parts=False)
    roads = roads[roads.geom_type == "LineString"]
    names = roads[name_field] if name_field in roads.columns else pd.Series(None, index=roads.index)
    clean = pd.Series([str(n).strip() if isinstance(n, str) and n.strip() else None for n in names], dtype=object)
    return gpd.GeoDataFrame({"road_fid": roads.index.astype(int), "road_name": clean.values},
                            geometry=roads.geometry.values, crs=epsg).reset_index(drop=True)


def junction_cuts(lines: list[LineString], tol: float) -> list[list[float]]:
    """For every line, the distances along it where another line crosses it or ends on it."""
    tree = shapely.STRtree(lines)
    cuts: list[list[float]] = [[] for _ in lines]
    for i, a in enumerate(lines):
        for j in tree.query(a, predicate="dwithin", distance=tol):
            if j == i:
                continue
            b = lines[j]
            pts = []
            inter = a.intersection(b)
            for g in getattr(inter, "geoms", [inter]):
                if g.is_empty:
                    continue
                if g.geom_type == "Point":
                    pts.append(g)
                elif g.geom_type == "LineString":            # drawn on top of each other: cut at both ends
                    pts += [Point(g.coords[0]), Point(g.coords[-1])]
            for end in (Point(b.coords[0]), Point(b.coords[-1])):
                if end.distance(a) <= tol:                    # a side road drawn a little short of the main road
                    pts.append(end)
            cuts[i] += [a.project(p) for p in pts]
    return cuts


def split_line(line: LineString, cuts: list[float], min_gap: float = 1.0) -> list[LineString]:
    marks = [0.0]
    for d in sorted(cuts):
        if min_gap < d < line.length - min_gap and d - marks[-1] >= min_gap:
            marks.append(d)
    marks.append(line.length)
    return [substring(line, a, b) for a, b in zip(marks, marks[1:])]


def stretch_id(geom: LineString) -> str:
    """An id that stays the same as long as the stretch does: its ends and midpoint, rounded to a metre."""
    ends = sorted((round(x), round(y)) for x, y in (geom.coords[0], geom.coords[-1]))
    mid = geom.interpolate(0.5, normalized=True)
    key = f"{ends}|{round(mid.x)},{round(mid.y)}"
    return "S" + hashlib.sha1(key.encode()).hexdigest()[:6]


def make_stretches(roads: gpd.GeoDataFrame, tol: float, min_len: float) -> gpd.GeoDataFrame:
    """Split every road line at every junction. Pieces shorter than `min_len` and repeats are dropped."""
    lines = list(roads.geometry.values)
    rows, seen = [], set()
    for line, cuts, r in zip(lines, junction_cuts(lines, tol), roads.itertuples()):
        for piece in split_line(line, cuts):
            if piece.length < min_len:
                continue
            sid = stretch_id(piece)
            if sid in seen:                                   # the same piece drawn twice
                continue
            seen.add(sid)
            rows.append({"stretch_id": sid, "road_fid": r.road_fid,
                         "name_drawn": r.road_name if isinstance(r.road_name, str) else None,
                         "length_m": round(piece.length, 1), "geometry": piece})
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=roads.crs)


# ----------------------------------------------------------------------------------------- buildings

def assign_units(units: gpd.GeoDataFrame, plan: dict, stretches: gpd.GeoDataFrame, cam_m: float,
                 backlot_m: float, street_keys: dict[str, set[str]] | None = None) -> dict[str, dict]:
    """unit id -> {"stretch_id", "role"} for the units that belong to a drawn stretch.

    A unit seen from the street belongs to the stretch nearest to the camera of its closest planned view;
    a unit no street view can check to the stretch nearest to its footprint (role "cannot see").
    A corner house photographed from two roads goes to the road of its address: when `street_keys`
    (stretch id -> name keys of that stretch) names the unit's survey road for one of the stretches
    within reach of its cameras (or of its footprint), that stretch is taken.
    """
    tree = shapely.STRtree(stretches.geometry.values)
    ids = stretches["stretch_id"].to_numpy()
    geoms = stretches.geometry.values
    out = {}
    for u in units.itertuples():
        info = plan.get(u.unit_id)
        if info is None:
            continue
        views = info.get("views") or []
        if info.get("access") == "street-facing" and views:
            v = min(views, key=lambda v: v["dist_near"])
            where, reach, role = Point(v["px"], v["py"]), cam_m, "seen"
            around = [Point(w["px"], w["py"]) for w in views]
        else:
            where, reach, role = u.geometry, backlot_m, "cannot see"
            around = [u.geometry]
        hit = tree.query_nearest(where, max_distance=reach)
        if not len(hit):
            continue
        chosen = int(hit[0])
        if street_keys and isinstance(u.road, str) and u.road.strip():
            own = name_key(u.road)
            cands = {int(i) for g in around for i in tree.query(g, predicate="dwithin", distance=reach)}
            named = [i for i in cands if any(similar_keys(own, k) for k in street_keys.get(str(ids[i]), set()))]
            if named and chosen not in named:
                chosen = min(named, key=lambda i: geoms[i].distance(where))
        out[u.unit_id] = {"stretch_id": str(ids[chosen]), "role": role}
    return out


def name_key(raw) -> str:
    """A key that is the same for the survey's spellings of one street name.

    'Chinnamal street', 'CHINNAMMAL STREET' and 'Chinnammal St' give the same key.
    """
    s = re.sub(r"[^a-z0-9]", "", str(raw).lower())
    s = re.sub(r"(street|st|road|rd)(\d*)$", r"\2", s) or s       # 'Chinthamani St-3' = 'Chinthamanistreet 3'
    return re.sub(r"(.)\1+", r"\1", s).replace("dh", "th")


def similar_keys(a: str, b: str) -> bool:
    """Name keys of the same street: equal, or one spelling slip apart ('alagana' / 'alaganan')."""
    if a == b:
        return True
    if re.sub(r"\D", "", a) != re.sub(r"\D", "", b):               # 3rd street is not 4th street
        return False
    return min(len(a), len(b)) >= 4 and SequenceMatcher(None, a, b).ratio() >= 0.85


def _names(raw_names) -> list[str]:
    return [str(r).strip() for r in raw_names if isinstance(r, str) and r.strip()]


def common_spellings(raw_names) -> dict[str, str]:
    """name key -> its most common spelling, over a whole ward, so every stretch of a street is named alike."""
    raw = _names(raw_names)
    out = {}
    for key in {name_key(r) for r in raw}:
        spelling = Counter(r for r in raw if name_key(r) == key).most_common(1)[0][0].strip(" ,.;-")
        out[key] = spelling.title() if spelling.isupper() or spelling.islower() else spelling
    return out


def suggest_name(raw_names, spellings: dict[str, str] | None = None) -> str | None:
    """The most common street name among a stretch's buildings."""
    raw = _names(raw_names)
    if not raw:
        return None
    best = Counter(name_key(r) for r in raw).most_common(1)[0][0]
    return (spellings or common_spellings(raw)).get(best) or common_spellings(raw)[best]


def front_gaps(geom: LineString, footprints: np.ndarray, tree: shapely.STRtree, step: float,
               reach: float) -> dict:
    """The gap between the building fronts on the two sides, sampled every `step` metres.

    Not the road width: a set-back house or an open plot makes the gap wider than the road.
    """
    widths, samples = [], 0
    for d in np.arange(step / 2, geom.length, step):
        p = geom.interpolate(d)
        a, b = geom.interpolate(max(0.0, d - 1)), geom.interpolate(min(geom.length, d + 1))
        tx, ty = b.x - a.x, b.y - a.y
        n = math.hypot(tx, ty)
        if n == 0:
            continue
        nx, ny = -ty / n, tx / n
        samples += 1
        sides = []
        for sign in (1, -1):
            ray = LineString([(p.x, p.y), (p.x + sign * nx * reach, p.y + sign * ny * reach)])
            hits = [p.distance(footprints[i].intersection(ray)) for i in tree.query(ray, predicate="intersects")]
            sides.append(min(hits) if hits else None)
        if None not in sides:
            widths.append(sides[0] + sides[1])
    return {"gap_med_m": round(float(np.median(widths)), 1) if widths else None,
            "gap_min_m": round(float(min(widths)), 1) if widths else None,
            "gap_samples": samples, "gap_built_both": len(widths)}


# ------------------------------------------------------------------------------- along-road pictures

def picture_points(geom: LineString, step: float) -> list[dict]:
    """Points spread evenly along the stretch, about `step` apart, with the road's direction there."""
    n = max(1, round(geom.length / step))
    out = []
    for k in range(n):
        d = (k + 0.5) * geom.length / n
        p = geom.interpolate(d)
        a, b = geom.interpolate(max(0.0, d - 2)), geom.interpolate(min(geom.length, d + 2))
        out.append({"n": k + 1, "along_m": round(d, 1), "x": p.x, "y": p.y,
                    "bearing": round(bearing((a.x, a.y), (b.x, b.y)), 1)})
    return out


def choose_panorama(point: dict, index: int, stretch_tree: shapely.STRtree, panos: gpd.GeoDataFrame,
                    pano_tree: shapely.STRtree, max_m: float, taken: set[str]) -> dict | None:
    """The newest panorama within `max_m` of the point that stands on this stretch's road.

    'On this road' means no other stretch is nearer to it, so a camera on a crossing road is not used.
    """
    p = Point(point["x"], point["y"])
    best = None
    for i in pano_tree.query(p, predicate="dwithin", distance=max_m):
        pano = panos.iloc[i]
        if pano.pano_id in taken:
            continue
        nearest = stretch_tree.query_nearest(pano.geometry, all_matches=True)
        if index not in set(int(j) for j in nearest):
            continue
        cand = (str(pano.date or ""), -p.distance(pano.geometry))
        if best is None or cand > best[0]:
            best = (cand, pano)
    if best is None:
        return None
    pano = best[1]
    return {"pano_id": pano.pano_id, "pano_date": pano.date, "px": pano.geometry.x, "py": pano.geometry.y,
            "heading": point["bearing"]}


def plan_pictures(stretches: gpd.GeoDataFrame, panos: gpd.GeoDataFrame, step: float, max_m: float) -> dict[str, list]:
    """stretch id -> its picture points, each with the panorama to use (or None)."""
    stretch_tree = shapely.STRtree(stretches.geometry.values)
    pano_tree = shapely.STRtree(panos.geometry.values)
    out = {}
    for index, st in enumerate(stretches.itertuples()):
        taken: set[str] = set()
        points = []
        for pt in picture_points(st.geometry, step):
            pano = choose_panorama(pt, index, stretch_tree, panos, pano_tree, max_m, taken)
            if pano:
                taken.add(pano["pano_id"])
            points.append({**pt, "pano": pano})
        out[st.stretch_id] = points
    return out


def picture_key(pictures: list[dict]) -> str:
    """Ties a street reading to the pictures it was made from."""
    shown = [[p["pano_id"], p["heading"]] for p in pictures if p.get("image")]
    return hashlib.sha1(repr(shown).encode()).hexdigest()[:16]


# ------------------------------------------------------------------------------------------- summary

def norm_zone(z) -> str | None:
    """'ZONE-B' -> 'B'; empty, '#N/A' and '0' -> None."""
    m = re.fullmatch(r"\s*(?:zone[-\s]*)?([abc])\s*", str(z or ""), flags=re.I)
    return m.group(1).upper() if m else None


def main_zone(zones: list) -> tuple[str | None, float | None]:
    """The most common zone and its share."""
    zs = [z for z in zones if z]
    if not zs:
        return None, None
    z, n = Counter(zs).most_common(1)[0]
    return z, round(n / len(zs), 2)


def building_summary(rows: list[dict]) -> dict:
    """Counts over the building rows (as in the building layer) of one stretch."""
    seen = [r for r in rows if r["role"] == "seen"]
    read = [r for r in rows if r.get("check") in ("Verified", "Unsure") and r.get("bldg_type")
            and r.get("bldg_type") != "cannot tell"]
    # "type unclear (upper floors not seen)" is not counted as a difference
    differ = [r for r in read if "differ" in (r.get("vs_survey") or "")]
    types = Counter(r["bldg_type"] for r in read)
    trade = sum(types[t] for t in TRADE)
    return {
        "bldgs": len(rows), "seen": len(seen), "cant_see": len(rows) - len(seen),
        "read": len(read), "bldg_verified": sum(r.get("check") == "Verified" for r in read),
        "bldg_unsure": sum(r.get("check") == "Unsure" for r in read),
        "pending": sum(r.get("check") == "Pending" for r in seen),
        "residential": types["Residential"], "commercial": types["Commercial"], "mixed": types["Mixed"],
        "other_type": len(read) - types["Residential"] - types["Commercial"] - types["Mixed"],
        "trade_pct": round(100 * trade / len(read)) if read else None,
        "shops": sum(r.get("shop_gf") == "yes" for r in read),
        "poss_shops": sum(r.get("shop_gf") == "possible" for r in read),
        "differ": len(differ),
        "differ_type": sum("type differs" in r["vs_survey"] for r in differ),
        "differ_floors": sum("floors differ" in r["vs_survey"] for r in differ),
        "type_unclear": sum("type unclear" in (r.get("vs_survey") or "") for r in read),
        "differ_pct": round(100 * len(differ) / len(read)) if read else None,
        "differ_ids": ", ".join(sorted({r["gis_id"] for r in differ})) or None,
    }


def zone_fit(zone: str | None, reading: dict | None, trade_pct, n_read: int) -> str:
    """Does the street look like its zone? A provisional rule, to be tuned with the analyst's corrections."""
    if not zone:
        return "no zone"
    if not reading:
        return "not read"
    shops = reading.get("shops")
    main_look = (reading.get("road_type") == "main road" or shops in ("about half", "mostly")
                 or (n_read >= 5 and trade_pct is not None and trade_pct >= 50))
    lane_look = (reading.get("road_type") in ("lane", "dead end") and reading.get("width") == "1 lane"
                 and shops in ("none", "few") and (trade_pct is None or trade_pct < 20))
    if main_look and zone in ("B", "C"):
        return "zone may be low"
    if lane_look and zone == "A":
        return "zone may be high"
    return "fits"
