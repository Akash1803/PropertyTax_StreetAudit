"""The deliverable: one GeoJSON layer with the check result in its attributes and one small picture per building.

The attributes are what the script identified from the street views. The survey values are added at the end
only to show where the survey differs; they are never used as the answer.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import geopandas as gpd
import pandas as pd
from PIL import Image

from . import rules
from .sources import GoogleStreetView

THUMB_PX = 512


def _floors_text(n) -> str | None:
    if n is None or pd.isna(n):
        return None
    n = int(n)
    return "G" if n == 0 else f"G+{n}"


TRADE_USAGES = ("Commercial", "Mixed", "Office / Lodge / Theater / Restaurants")


def _shop(usage, trade: str, possible) -> str:
    """Shop or other trade on the ground floor. A school's or temple's board is not a shop."""
    if usage in TRADE_USAGES:
        return "yes"
    return "possible" if possible in (True, "yes") else "no"


def trade_only_in_other_views(answer: dict) -> bool:
    """True when the shop or office boards were read only in views other than the closest one.

    On 44WN1679 two farther views showed another building with aerobics boards and the reading took
    them for this building. Such readings get a warning in check_note.
    """
    usage = (answer.get("classification") or {}).get("building_usage")
    if usage not in TRADE_USAGES:
        return False
    ref, other = [], []
    for v in answer.get("views", []):
        (ref if v.get("same_building_as_reference") == "this is the reference view" else other).append(v)
    return not any(v.get("boards_read") for v in ref) and any(v.get("boards_read") for v in other)


def _vs_survey(llm_usage, llm_floors, rec_usage, rec_floors) -> str:
    if not llm_usage or llm_usage == "cannot tell":
        return "-"
    parts = []
    if rules._norm_usage(llm_usage) != rules._norm_usage(rec_usage):
        parts.append("type differs")
    if llm_floors is not None and not pd.isna(llm_floors) and rec_floors is not None and not pd.isna(rec_floors) \
            and int(llm_floors) != int(rec_floors):
        parts.append("floors differ")
    return " and ".join(parts) or "same"


def thumbnail(src: Path, dst: Path) -> bool:
    """A small copy of the marked view 1 (about 40 KB) for the layer."""
    if not src.exists():
        return dst.exists()
    img = Image.open(src).convert("RGB")
    img.thumbnail((THUMB_PX, THUMB_PX))
    dst.parent.mkdir(parents=True, exist_ok=True)
    img.save(dst, quality=72, optimize=True)
    return True


def export_geojson(result: gpd.GeoDataFrame, aimed: dict, answers: dict, out: Path, evidence_dir: Path,
                   only: set[str] | None = None, corrections: dict | None = None) -> int:
    """Write the GeoJSON (EPSG:4326) and its images folder beside it. Returns the number of features.

    What the checker typed into `verified` / `verify_note` in an earlier export is carried over.
    `corrections` maps a unit id (e.g. 44WN1073_p2, or the building id for a one-part building) to
    attribute values that replace the reading, e.g. after an older street view was checked.
    """
    images = out.parent / "images"
    kept = {}
    if out.exists():
        old = gpd.read_file(out, ignore_geometry=True)
        for o in old.itertuples():
            if (getattr(o, "verified", None) not in (None, "")) or (getattr(o, "verify_note", None) not in (None, "")):
                kept[(o.gis_id, int(o.part))] = (o.verified, o.verify_note)
    rows = []
    res = result if only is None else result[result["unit_id"].isin(only)]
    for r in res.itertuples():
        info = aimed.get(r.unit_id) or {}
        views = [v for v in info.get("views", []) if v.get("image")]
        first = (answers.get(r.unit_id) or [{}])[0] or {}
        cls = first.get("classification") or {}
        checked = r.verdict in ("Verified", "Unsure") and bool(cls)
        image = streetview = None
        if views:
            v = views[0]
            if thumbnail(evidence_dir / v["image"], images / f"{r.unit_id}.jpg"):
                image = f"images/{r.unit_id}.jpg"
            streetview = GoogleStreetView.viewer_url(v["pano_id"], v["aim"]["heading"], v["aim"]["fov"], v["aim"]["pitch"])
        usage = cls.get("building_usage") if checked else None
        floors = cls.get("floors_above_ground") if checked else None
        floors = floors if isinstance(floors, int) and not isinstance(floors, bool) else None
        trade = "; ".join(map(str, cls.get("trade_evidence") or []))
        note = r.verdict_why
        if checked and trade_only_in_other_views(first):
            note += " | check: shop/office boards seen only in a farther view, make sure it is this building"
        rows.append({
            "gis_id": r.building_id, "part": int(r.part), "road": r.road,
            "check": r.verdict, "check_note": note,
            "bldg_type": usage, "floors": _floors_text(floors), "floor_count": floors,
            "floor_use": "; ".join(f"{_floors_text(f.get('floor'))}: {f.get('usage')}" for f in cls.get("floor_usage") or []) or None,
            "shop_gf": _shop(usage, trade, cls.get("possible_shop")) if checked else None,
            "units_seen": cls.get("units_seen"),
            "construct": cls.get("construction_status"), "roof": cls.get("roof_type"),
            "terrace": cls.get("terrace_structures"), "front": cls.get("attached_front_structure"),
            "boards": trade or None, "other_boards": "; ".join(map(str, cls.get("other_boards") or [])) or None,
            "extras": "; ".join(map(str, cls.get("extras") or [])) or None,
            "ai_note": cls.get("notes") or None,
            "conf_floors": (cls.get("confidence") or {}).get("floors"), "conf_type": (cls.get("confidence") or {}).get("usage"),
            "image": image, "streetview": streetview,
            "photo_date": views[0]["pano_date"] if views else None,
            "survey_type": r.rec_usage, "survey_floors": _floors_text(r.rec_floors),
            "vs_survey": _vs_survey(usage, floors, r.rec_usage, r.rec_floors) if checked else "-",
            "checked_by": (r.model if checked else None), "checked_on": time.strftime("%Y-%m-%d"),
            "verified": kept.get((r.building_id, int(r.part)), (None, None))[0],
            "verify_note": kept.get((r.building_id, int(r.part)), (None, None))[1],
            "geometry": r.geometry,
        })
        fix = (corrections or {}).get(r.unit_id, {})
        for k, v in fix.items():
            if k.startswith("_"):                       # comments such as "_why"
                continue
            if k not in rows[-1] or k == "geometry":
                raise ValueError(f"correction for {r.unit_id}: unknown attribute {k!r}")
            rows[-1][k] = v
        if fix and "vs_survey" not in fix:              # the comparison follows the corrected values
            rows[-1]["vs_survey"] = _vs_survey(rows[-1]["bldg_type"], rows[-1]["floor_count"], r.rec_usage, r.rec_floors)
    gdf = gpd.GeoDataFrame(rows, crs=result.crs).to_crs(4326)
    gdf["floor_count"] = pd.to_numeric(gdf["floor_count"], errors="coerce").astype("Int64")
    gdf["units_seen"] = pd.to_numeric(gdf["units_seen"], errors="coerce").astype("Int64")
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        try:
            out.unlink()
        except PermissionError:
            raise SystemExit(f"{out.name} is open in QGIS; close it there or export under another --name") from None
    gdf.to_file(out, driver="GeoJSON")
    back = len(gpd.read_file(out))
    if back != len(gdf):
        raise RuntimeError(f"{out} has {back} features, expected {len(gdf)}")
    return len(gdf)
