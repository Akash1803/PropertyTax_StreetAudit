"""Loading the ward data: audit units (one per footprint part), sight-line blockers, door numbers, selections."""
from __future__ import annotations

import warnings

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely.geometry import LineString
from shapely.ops import substring

from .config import Settings
from .doors import door_sets


def load_units(s: Settings) -> gpd.GeoDataFrame:
    """One row per footprint part, in the metric CRS.

    A multi-part footprint is audited part by part: its parts can be different buildings tens of metres
    apart. Parts smaller than `min_part_m2` are left out unless they are the largest part.
    """
    b = gpd.read_file(s.buildings).to_crs(s.metric_epsg)
    rows = []
    for _, r in b.iterrows():
        gid = str(r[s.id_field])
        parts = sorted(getattr(r.geometry, "geoms", [r.geometry]), key=lambda g: -g.area)
        keep = [p for i, p in enumerate(parts) if i == 0 or p.area >= s.min_part_m2]
        for n, part in enumerate(keep, 1):
            storeys = r[s.storeys_field]
            rows.append({
                "unit_id": gid if len(keep) == 1 else f"{gid}_p{n}",
                "building_id": gid, "part": n, "n_parts": len(keep),
                "road": None if pd.isna(r[s.road_field]) else str(r[s.road_field]),
                "usage": None if pd.isna(r[s.usage_field]) else str(r[s.usage_field]).strip(),
                "storeys": None if pd.isna(storeys) else int(storeys),
                "geometry": part,
            })
    units = gpd.GeoDataFrame(rows, crs=s.metric_epsg)
    if units["unit_id"].duplicated().any():
        raise ValueError(f"duplicate {s.id_field} values in {s.buildings}")
    return units


def load_blockers(s: Settings, units: gpd.GeoDataFrame) -> np.ndarray:
    """Every footprint polygon that can block a sight line: the ward's own and the neighbouring wards'.

    A neighbouring-ward footprint that mostly overlaps one of the ward's own is a second drawing of the
    same building and is dropped.
    """
    own = gpd.read_file(s.buildings).to_crs(s.metric_epsg).explode(index_parts=False).geometry.values
    polys = [own]
    minx, miny, maxx, maxy = units.total_bounds
    box = shapely.box(minx - 80, miny - 80, maxx + 80, maxy + 80)
    own_tree = shapely.STRtree(own)
    for path in s.other_footprints:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            other = gpd.read_file(path).to_crs(s.metric_epsg).explode(index_parts=False).geometry.values
        other = other[shapely.intersects(other, box)]
        keep = []
        for g in other:
            hits = own_tree.query(g, predicate="intersects")
            overlap = sum(g.intersection(own[i]).area for i in hits)
            if overlap <= 0.2 * g.area:
                keep.append(g)
        polys.append(np.array(keep, dtype=object))
    return np.concatenate(polys)


def load_doors(s: Settings) -> dict[str, set[str]]:
    g = gpd.read_file(s.geocodes, ignore_geometry=True, columns=[s.id_field, *s.door_fields])
    g[s.id_field] = g[s.id_field].astype(str)
    return door_sets(g, s.id_field, s.door_fields)


def load_assessment_usages(s: Settings) -> dict[str, str]:
    """Usages of the tax assessments inside each building, joined with '+'."""
    g = gpd.read_file(s.geocodes, ignore_geometry=True, columns=[s.id_field, s.assess_usage_field])
    g[s.id_field] = g[s.id_field].astype(str)
    return g.groupby(s.id_field)[s.assess_usage_field].agg(lambda v: "+".join(sorted({str(x) for x in v.dropna()}))).to_dict()


def select_units(units: gpd.GeoDataFrame, ids: list[str] | None = None, line: LineString | None = None,
                 buffer_m: float = 20.0, max_length_m: float | None = None) -> gpd.GeoDataFrame:
    """Pick the units to run.

    ids: building ids or unit ids. line: a street line in the units' CRS; units within `buffer_m` of it are
    taken, optionally only along the first `max_length_m` of the line. With neither, all units are returned.
    """
    sel = units
    if ids is not None:
        wanted = {str(i) for i in ids}
        sel = sel[sel["unit_id"].isin(wanted) | sel["building_id"].isin(wanted)]
    if line is not None:
        if max_length_m is not None:
            line = substring(line, 0, min(max_length_m, line.length))
        sel = sel[sel.geometry.distance(line) <= buffer_m]
    return sel
