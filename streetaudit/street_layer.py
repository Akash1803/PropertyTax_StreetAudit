"""The street-level stages: plan the stretches, fetch and mark the along-road pictures, export the layers.

Work files, all in <run>/work/streets/:
  plan.json       stretches (with their picture points and front gaps) and the building -> stretch assignment
  pictures.json   the along-road pictures fetched per stretch, the marked ortho and the reading sheets
  readings/       one reading per stretch, written by tools/write_street_readings.py
"""
from __future__ import annotations

import io
import json
import time
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from PIL import Image, ImageDraw
from shapely.geometry import LineString, Point

from . import streets as st
from .config import Settings
from .export import kept_verification, thumbnail, write_layer
from .imaging import CYAN, YELLOW, _caption, _font, _line
from .sources import GoogleStreetView

MAGENTA, GREY = (255, 60, 255), (170, 170, 170)
SHEET_TILE, SHEET_COLS, PICTURES_PER_SHEET = 600, 3, 8


def work(s: Settings) -> Path:
    return s.work_dir / "streets"


def read_json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def compass(heading: float) -> str:
    return ("N", "NE", "E", "SE", "S", "SW", "W", "NW")[int(((heading % 360) + 22.5) // 45) % 8]


# ---------------------------------------------------------------------------------------------- plan

def plan_streets(s: Settings, units: gpd.GeoDataFrame, view_plan: dict, panos: gpd.GeoDataFrame,
                 blockers: np.ndarray) -> dict:
    """Stretches, their picture points and front gaps, and which stretch every building belongs to."""
    if s.roads is None:
        raise SystemExit("no road layer: set `roads` in the run's settings")
    roads = st.load_roads(s.roads, s.metric_epsg, s.road_name_field)
    stretches = st.make_stretches(roads, s.junction_tol_m, s.min_stretch_m)
    road_of = dict(zip(units["unit_id"], units["road"]))
    spellings = st.common_spellings(units["road"])
    # first by camera alone; then corner houses go to the road of their address, named from the first pass
    assign = st.assign_units(units, view_plan, stretches, s.street_cam_m, s.backlot_m)
    keys = {}
    for row in stretches.itertuples():
        members = [u for u, a in assign.items() if a["stretch_id"] == row.stretch_id]
        names = [row.name_drawn, st.suggest_name([road_of.get(u) for u in members], spellings)]
        keys[row.stretch_id] = {st.name_key(n) for n in names if isinstance(n, str)}
    assign = st.assign_units(units, view_plan, stretches, s.street_cam_m, s.backlot_m, keys)
    pictures = st.plan_pictures(stretches, panos, s.street_step_m, s.street_pano_m)
    tree = shapely.STRtree(blockers)
    out = {}
    for row in stretches.itertuples():
        members = [u for u, a in assign.items() if a["stretch_id"] == row.stretch_id]
        out[row.stretch_id] = {
            "road_fid": int(row.road_fid), "name_drawn": row.name_drawn if isinstance(row.name_drawn, str) else None,
            "name_suggested": st.suggest_name([road_of.get(u) for u in members], spellings),
            "length_m": row.length_m, "coords": [list(c) for c in row.geometry.coords],
            **st.front_gaps(row.geometry, blockers, tree, s.gap_step_m, s.gap_reach_m),
            "points": pictures[row.stretch_id],
        }
    return {"roads": str(s.roads), "made": time.strftime("%Y-%m-%d %H:%M"), "stretches": out, "assign": assign,
            "roads_count": len(roads)}


def street_name(info: dict) -> str | None:
    return info.get("name_drawn") or info.get("name_suggested")


def select_stretches(plan: dict, stretch_ids: list[str] | None = None, names: list[str] | None = None,
                     unit_ids: list[str] | None = None) -> list[str]:
    """Stretch ids by id, by street name (drawn or suggested, any spelling) or by the buildings on them."""
    sts = plan["stretches"]
    chosen = set()
    if stretch_ids:
        unknown = [i for i in stretch_ids if i not in sts]
        if unknown:
            raise SystemExit(f"unknown stretch ids: {unknown}")
        chosen |= set(stretch_ids)
    if names:
        keys = {st.name_key(n) for n in names}
        chosen |= {sid for sid, info in sts.items()
                   if st.name_key(info.get("name_drawn") or "") in keys or st.name_key(info.get("name_suggested") or "") in keys}
    if unit_ids:
        wanted = set(unit_ids)
        chosen |= {a["stretch_id"] for u, a in plan["assign"].items() if u in wanted or u.split("_p")[0] in wanted}
    if not (stretch_ids or names or unit_ids):
        chosen = set(sts)
    return [sid for sid in sts if sid in chosen]


def chips_todo(plan: dict, sids: list[str], epsg: int) -> dict:
    """Square ortho windows around each stretch, about half a metre per pixel."""
    chips = {}
    for sid in sids:
        line = LineString(plan["stretches"][sid]["coords"])
        minx, miny, maxx, maxy = line.bounds
        side = max(maxx - minx, maxy - miny) + 50
        cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
        chips[sid] = {"xmin": cx - side / 2, "ymin": cy - side / 2, "xmax": cx + side / 2, "ymax": cy + side / 2,
                      "px": int(min(1400, max(700, side * 2)))}
    return {"epsg": epsg, "px": 900, "chips": chips}


# ------------------------------------------------------------------------------------------ pictures

def fetch_pictures(s: Settings, plan: dict, sids: list[str], source, previous: dict) -> dict:
    """Fetch the along-road pictures of the stretches. A picture on disk is reused for the same panorama and aim."""
    folder = s.evidence_dir / "streets"
    folder.mkdir(parents=True, exist_ok=True)
    out = {}
    for sid in sids:
        points = plan["stretches"][sid]["points"]
        old = {(p["pano_id"], p["heading"]): p for p in (previous.get(sid) or {}).get("pictures", [])}
        pictures = []
        total = sum(1 for p in points if p["pano"])
        for p in points:
            pano = p["pano"]
            if not pano:
                continue
            n = len(pictures) + 1
            pic = {"n": n, "along_m": p["along_m"], "pano_id": pano["pano_id"], "pano_date": pano["pano_date"],
                   "px": pano["px"], "py": pano["py"], "heading": pano["heading"], "fov": s.street_fov, "pitch": 0.0}
            name = f"{sid}_p{n}.jpg"
            before = old.get((pano["pano_id"], pano["heading"]))
            if before and before.get("image") == name and (folder / name).exists():
                pic["image"] = name
            else:
                data = source.image(pano["pano_id"], pano["heading"], s.street_fov, 0.0, s.image_px)
                pic["image"] = None
                if data is not None:
                    img = Image.open(io.BytesIO(data)).convert("RGB")
                    _caption(ImageDraw.Draw(img), f"P{n}/{total}  {pano['pano_date']}  looking {compass(pano['heading'])}", 330)
                    img.save(folder / name, quality=88)
                    pic["image"] = name
            pictures.append(pic)
        out[sid] = {"pictures": pictures}
    return out


def mark_overview(chip: dict, sid: str, plan: dict, pictures: list[dict], units: gpd.GeoDataFrame) -> Image.Image:
    """Ortho of the stretch: the stretch in yellow, other roads in white, its buildings, the cameras numbered."""
    img = Image.open(chip["file"]).convert("RGB")
    draw = ImageDraw.Draw(img)
    scale = img.size[0] / (chip["xmax"] - chip["xmin"])

    def px(x, y):
        return (x - chip["xmin"]) * scale, (chip["ymax"] - y) * scale

    box = shapely.box(chip["xmin"], chip["ymin"], chip["xmax"], chip["ymax"])
    roles = {u: a["role"] for u, a in plan["assign"].items() if a["stretch_id"] == sid}
    near = units[units.intersects(box)]
    for row in near.itertuples():
        role = roles.get(row.unit_id)
        if role:
            draw.line([px(x, y) for x, y in row.geometry.exterior.coords], fill=CYAN if role == "seen" else GREY,
                      width=3 if role == "seen" else 2)
    for other, info in plan["stretches"].items():
        if other != sid and LineString(info["coords"]).intersects(box):
            draw.line([px(x, y) for x, y in info["coords"]], fill=(255, 255, 255), width=2)
    _line(draw, [px(x, y) for x, y in plan["stretches"][sid]["coords"]], YELLOW, 5)
    font = _font(16)
    for p in pictures:
        c = px(p["px"], p["py"])
        h = np.radians(p["heading"])
        tip = (c[0] + 26 * np.sin(h), c[1] - 26 * np.cos(h))
        draw.line([c, tip], fill=MAGENTA, width=4)
        draw.ellipse([c[0] - 11, c[1] - 11, c[0] + 11, c[1] + 11], fill=MAGENTA, outline=(0, 0, 0), width=2)
        draw.text((c[0] - 5 * len(str(p["n"])), c[1] - 10), str(p["n"]), fill=(0, 0, 0), font=font)
    _caption(draw, "ORTHO north up | yellow = stretch | cyan = its buildings | grey = cannot see | numbers = pictures", 760)
    return img


def write_sheets(s: Settings, sid: str, info: dict, pictures: list[dict], overview: Path | None) -> list[str]:
    """Reading sheets: up to eight pictures and the ortho per sheet, with a header naming the stretch."""
    folder = s.evidence_dir / "streets"
    shown = [p for p in pictures if p.get("image")]
    chunks = [shown[i:i + PICTURES_PER_SHEET] for i in range(0, len(shown), PICTURES_PER_SHEET)] or [[]]
    names = []
    for k, chunk in enumerate(chunks, 1):
        tiles = [Image.open(folder / p["image"]).convert("RGB") for p in chunk]
        if overview is not None and overview.exists():
            tiles.append(Image.open(overview).convert("RGB"))
        rows = max(1, (len(tiles) + SHEET_COLS - 1) // SHEET_COLS)
        sheet = Image.new("RGB", (SHEET_COLS * SHEET_TILE, rows * SHEET_TILE + 40), "white")
        draw = ImageDraw.Draw(sheet)
        draw.rectangle([0, 0, sheet.size[0], 40], fill=(0, 0, 0))
        gap = f"front gap median {info['gap_med_m']} m, narrowest {info['gap_min_m']} m" if info.get("gap_med_m") else "front gap: -"
        draw.text((8, 8), f"{sid}  {street_name(info) or '(no name)'}  {info['length_m']:.0f} m  | {len(shown)} pictures "
                          f"| {gap} | sheet {k}/{len(chunks)}", fill=YELLOW, font=_font(20))
        for i, t in enumerate(tiles):
            sheet.paste(t.resize((SHEET_TILE, SHEET_TILE)), ((i % SHEET_COLS) * SHEET_TILE, 40 + (i // SHEET_COLS) * SHEET_TILE))
        name = f"{sid}_sheet{k}.jpg"
        sheet.save(folder / name, quality=85)
        names.append(name)
    return names


# -------------------------------------------------------------------------------------------- export

def load_readings(s: Settings, pictures: dict) -> dict:
    """stretch id -> reading, for readings made from the pictures now on file."""
    out = {}
    for sid, info in pictures.items():
        saved = read_json(work(s) / "readings" / f"{sid}.json", None)
        if saved and saved.get("key") == st.picture_key(info["pictures"]):
            out[sid] = saved
    return out


def assessment_zones(s: Settings) -> dict[str, list]:
    """GIS_ID -> the zones of its assessments."""
    g = gpd.read_file(s.geocodes, ignore_geometry=True, columns=[s.id_field, s.zone_field])
    g[s.id_field] = g[s.id_field].astype(str)
    out: dict[str, list] = {}
    for gid, z in zip(g[s.id_field], g[s.zone_field]):
        out.setdefault(gid, []).append(st.norm_zone(z))
    return out


def _picture_for_layer(pictures: list[dict], reading: dict | None) -> dict | None:
    """The clear picture nearest the middle of the stretch, else the fetched one nearest the middle."""
    shown = [p for p in pictures if p.get("image")]
    if not shown:
        return None
    said = {q["n"]: q.get("usable") for q in ((reading or {}).get("reading") or {}).get("pictures", [])}
    mid = (len(shown) + 1) / 2
    clear = [p for p in shown if said.get(p["n"]) == "clear"] or shown
    return min(clear, key=lambda p: abs(p["n"] - mid))


def street_rows(s: Settings, plan: dict, pictures: dict, readings: dict, bldg_rows: dict, zones: dict,
                sids: list[str], images: Path | None, ocr: dict | None = None) -> tuple[list[dict], list[dict]]:
    """Rows of the streets layer and of the street-views layer for the chosen stretches."""
    sts, assign = plan["stretches"], plan["assign"]
    members: dict[str, list[str]] = {}
    for u, a in assign.items():
        members.setdefault(a["stretch_id"], []).append(u)

    def zones_of(units_):
        return [z for u in units_ for z in zones.get(u.split("_p")[0], [])]

    # zone of the whole street: every stretch with the same name, spelling slips included
    by_name: dict[str, list[str]] = {}
    canon: dict[str, str] = {}
    for sid, info in sts.items():
        if street_name(info):
            key = st.name_key(street_name(info))
            canon.setdefault(key, next((k for k in by_name if st.similar_keys(key, k)), key))
            by_name.setdefault(canon[key], []).append(sid)
    from . import ocr as ocr_rules
    ocr = ocr or {}
    lines, points = [], []
    for sid in sids:
        info = sts[sid]
        name = street_name(info)
        units_ = members.get(sid, [])
        evidence = [{"name": n, "kind": (ocr[u].get("merged") or {}).get("kind"), "unit_id": u, "dist_m": 0}
                    for u in units_ if u in ocr for n in ((ocr[u].get("merged") or {}).get("road_names") or [])]
        named = ocr_rules.decide_name(name, evidence) if evidence else None
        rows_b = [{**bldg_rows[u], "role": assign[u]["role"]} for u in units_ if u in bldg_rows]
        summary = st.building_summary(rows_b)
        here, _ = st.main_zone(zones_of(units_))
        same_street = by_name.get(canon.get(st.name_key(name), ""), [sid]) if name else [sid]
        street_zone, share = st.main_zone(zones_of([u for x in same_street for u in members.get(x, [])]))
        odd = sorted({u.split("_p")[0] for u in units_ for z in zones.get(u.split("_p")[0], [])
                      if z and street_zone and z != street_zone})
        n_odd = sum(1 for z in zones_of(units_) if z and street_zone and z != street_zone)
        saved = readings.get(sid)
        reading = (saved or {}).get("reading")
        pics = (pictures.get(sid) or {}).get("pictures", [])
        fit = st.zone_fit(street_zone, reading, summary["trade_pct"], summary["read"])
        shown = [p for p in pics if p.get("image")]
        check = "Read" if reading else ("No picture" if not any(p["pano"] for p in info["points"]) else "Not read")
        said = {q["n"]: q for q in (reading or {}).get("pictures", [])}
        for p in shown:
            image = None
            if images is not None and thumbnail(s.evidence_dir / "streets" / p["image"], images / p["image"]):
                image = f"images/streets/{p['image']}"
            p["_thumb"] = image
            points.append({
                "stretch_id": sid, "street": name, "pic": p["n"], "of_pics": len(shown), "along_m": p["along_m"],
                "photo_date": p["pano_date"], "heading": p["heading"], "looking": compass(p["heading"]),
                "usable": (said.get(p["n"]) or {}).get("usable"), "pic_note": (said.get(p["n"]) or {}).get("note"),
                "image": image,
                "streetview": GoogleStreetView.viewer_url(p["pano_id"], p["heading"], p["fov"], p["pitch"]),
                "geometry": Point(p["px"], p["py"]),
            })
        best = _picture_for_layer(pics, saved)
        lines.append({
            "stretch_id": sid, "street": name, "name_drawn": info.get("name_drawn"),
            "name_suggested": info.get("name_suggested"), "length_m": info["length_m"], "check": check,
            "road_type": (reading or {}).get("road_type"), "surface": (reading or {}).get("surface"),
            "width_seen": (reading or {}).get("width"), "footpath": (reading or {}).get("footpath"),
            "drain": (reading or {}).get("drain"), "lights": (reading or {}).get("lights"),
            "shops_seen": (reading or {}).get("shops"), "street_note": (reading or {}).get("note"),
            "street_conf": (reading or {}).get("conf"),
            "pictures": len(shown), "pictures_ok": sum(1 for q in said.values() if q.get("usable") == "clear"),
            "photo_date": max((p["pano_date"] or "" for p in shown), default=None) or None,
            "image": best.get("_thumb") if best else None,
            "streetview": GoogleStreetView.viewer_url(best["pano_id"], best["heading"], best["fov"], best["pitch"]) if best else None,
            **summary,
            "assess": len(zones_of(units_)), "zone_here": here, "zone_street": street_zone, "zone_share": share,
            "odd_zone": n_odd, "odd_zone_ids": ", ".join(odd) or None, "zone_fit": fit,
            "zone_flag": "yes" if fit.startswith("zone may") or n_odd else "no",
            "gap_med_m": info.get("gap_med_m"), "gap_min_m": info.get("gap_min_m"),
            "ocr_name": named["name"] if named else None, "ocr_status": named["status"] if named else "No sign read",
            "ocr_rule": named["rule"] if named else None,
            "ocr_fix": ("yes" if named["fix"] else "no") if named else None, "ocr_signs": len(evidence),
            "checked_by": (saved or {}).get("by"), "checked_on": (saved or {}).get("made"),
            "geometry": LineString(info["coords"]),
        })
    return lines, points


INT_FIELDS = ("pictures", "pictures_ok", "bldgs", "seen", "cant_see", "read", "bldg_verified", "bldg_unsure", "pending",
              "residential", "commercial", "mixed", "other_type", "trade_pct", "shops", "poss_shops", "differ",
              "differ_type", "differ_floors", "type_unclear", "differ_pct", "assess", "odd_zone", "ocr_signs")


def export_streets(s: Settings, lines: list[dict], points: list[dict], out_lines: Path, out_points: Path,
                   all_ids: set[str]) -> tuple[int, int, dict]:
    """Write both layers. Notes typed on stretches that no longer exist are kept in orphan_notes.json."""
    kept = kept_verification(out_lines, ("stretch_id",))
    for row in lines:
        row["verified"], row["verify_note"] = kept.get(row["stretch_id"], (None, None))
    orphans = {sid: {"verified": v, "verify_note": n} for sid, (v, n) in kept.items() if sid not in all_ids}
    if orphans:
        path = work(s) / "orphan_notes.json"
        write_json(path, {**read_json(path, {}), **orphans})
    gl = gpd.GeoDataFrame(lines, geometry="geometry", crs=s.metric_epsg).to_crs(4326)
    for col in INT_FIELDS:
        gl[col] = pd.to_numeric(gl[col], errors="coerce").astype("Int64")
    gp = gpd.GeoDataFrame(points, geometry="geometry", crs=s.metric_epsg).to_crs(4326) if points else None
    n_lines = write_layer(gl, out_lines)
    n_points = write_layer(gp, out_points) if gp is not None else 0
    return n_lines, n_points, orphans
