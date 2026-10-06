"""Street View links for every building: up to four viewpoints within reach, spread around the building.

No image is fetched and no API is called: the links are built from the panorama positions already on
file and open the free Street View website. Buildings no panorama can see get links to the nearest
panoramas anyway (marked "no line of sight") so a checker can still look around.
"""
from __future__ import annotations

import math

import geopandas as gpd
import numpy as np
import shapely

from .config import Settings
from .geometry import aim, bearing
from .visibility import see

COMPASS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


def compass(deg: float) -> str:
    return COMPASS[int(((deg % 360) + 22.5) // 45) % 8]


def _spread(cands: list[dict], max_links: int, sector: float) -> list[dict]:
    """Closest first, each new link at least `sector` degrees away (as seen from the building) from the chosen ones."""
    chosen: list[dict] = []
    for c in sorted(cands, key=lambda c: c["dist"]):
        if len(chosen) >= max_links:
            break
        if all(abs((c["side"] - k["side"] + 180) % 360 - 180) >= sector for k in chosen):
            chosen.append(c)
    if len(chosen) < max_links:                     # same side is better than no link
        for c in sorted(cands, key=lambda c: c["dist"]):
            if len(chosen) >= max_links:
                break
            if c not in chosen:
                chosen.append(c)
    return chosen


def link_views(poly, storeys, panos: gpd.GeoDataFrame, tree: shapely.STRtree, s: Settings) -> list[dict]:
    """Up to `s.max_links` viewpoints within `s.link_dist_m`: first those with a line of sight, then the nearest others."""
    c = poly.centroid
    seen, blind = [], []
    for pano in panos.itertuples():
        cam = (pano.geometry.x, pano.geometry.y)
        side = bearing((c.x, c.y), cam)              # which side of the building the camera is on
        v = see(poly, cam, tree, s)
        if v is not None and v["visible_m"] >= s.min_visible_m and v["dist_near"] >= s.min_view_dist:
            a = aim(v, storeys or 1, s.camera_height_m)
            seen.append({"pano_id": pano.pano_id, "date": pano.date, "px": cam[0], "py": cam[1], "dist": v["dist_near"],
                         "side": side, "heading": a["heading"], "fov": a["fov"], "pitch": a["pitch"], "los": True})
        else:
            d = poly.distance(pano.geometry)
            blind.append({"pano_id": pano.pano_id, "date": pano.date, "px": cam[0], "py": cam[1], "dist": round(d, 1),
                          "side": side, "heading": round(bearing(cam, (c.x, c.y)), 2), "fov": 90.0,
                          "pitch": round(min(25.0, math.degrees(math.atan2(6.0, max(d, 1.0)))), 1), "los": False})
    links = _spread(seen, s.max_links, s.link_sector_deg)
    if len(links) < s.max_links:
        links += _spread(blind, s.max_links - len(links), s.link_sector_deg)
    for n, k in enumerate(links, 1):
        k["n"] = n
        k["from"] = compass(k["side"])
    return links


def plan_links(units: gpd.GeoDataFrame, panos: gpd.GeoDataFrame, blockers: np.ndarray, s: Settings,
               progress=None) -> dict[str, list[dict]]:
    tree = shapely.STRtree(blockers)
    pano_tree = shapely.STRtree(panos.geometry.values)
    out = {}
    for n, unit in enumerate(units.itertuples(), 1):
        near = panos.iloc[pano_tree.query(unit.geometry, predicate="dwithin", distance=s.link_dist_m)]
        out[unit.unit_id] = link_views(unit.geometry, unit.storeys, near, tree, s)
        if progress and n % 250 == 0:
            progress(f"links planned for {n}/{len(units)} buildings")
    return out
