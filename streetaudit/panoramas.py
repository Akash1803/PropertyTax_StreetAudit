"""Listing every panorama around the selected buildings."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import geopandas as gpd
import numpy as np
import shapely

from .config import Settings


def grid_points(units: gpd.GeoDataFrame, blockers: np.ndarray, s: Settings) -> np.ndarray:
    """Points `grid_m` apart on the open ground within `search_m` of the units (not inside any footprint)."""
    area = shapely.union_all(shapely.buffer(units.geometry.values, s.search_m))
    minx, miny, maxx, maxy = area.bounds
    xs = np.arange(minx, maxx + s.grid_m, s.grid_m)
    ys = np.arange(miny, maxy + s.grid_m, s.grid_m)
    pts = shapely.points(*np.meshgrid(xs, ys)).ravel()
    shapely.prepare(area)
    pts = pts[shapely.contains(area, pts)]
    inside = shapely.STRtree(blockers).query(pts, predicate="within")[0]
    return np.delete(pts, np.unique(inside))


def find_panoramas(units: gpd.GeoDataFrame, blockers: np.ndarray, source, s: Settings,
                   workers: int = 6, progress=None) -> gpd.GeoDataFrame:
    """Ask the source for its nearest panorama at every grid point and keep each panorama once.

    Columns: pano_id, date, own (the source's own imagery), inside (plots inside a footprint), geometry.
    """
    pts = grid_points(units, blockers, s)
    ll = gpd.GeoSeries(pts, crs=s.metric_epsg).to_crs(4326)
    found: dict[str, dict] = {}

    def ask(p):
        return source.nearest_panorama(p.y, p.x, s.grid_radius_m)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for n, pano in enumerate(pool.map(ask, ll), 1):
            if pano:
                found[pano["pano_id"]] = pano
            if progress and n % 2000 == 0:
                progress(f"panorama search: {n}/{len(ll)} points, {len(found)} panoramas")
    rows = list(found.values())
    if not rows:
        return gpd.GeoDataFrame({"pano_id": [], "date": [], "own": [], "inside": []}, geometry=[], crs=s.metric_epsg)
    panos = gpd.GeoDataFrame(
        rows, geometry=gpd.points_from_xy([r["lon"] for r in rows], [r["lat"] for r in rows]), crs=4326,
    ).to_crs(s.metric_epsg)
    panos = panos.drop(columns=["lat", "lon"])
    hits = shapely.STRtree(blockers).query(panos.geometry.values, predicate="within")[0]
    panos["inside"] = False
    panos.iloc[np.unique(hits), panos.columns.get_loc("inside")] = True
    return panos
