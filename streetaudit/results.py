"""Turning the stages' outputs into the result layers, the evidence pages and a summary."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import geopandas as gpd
import pandas as pd
import shapely
from shapely.geometry import LineString

from . import evidence, rules
from .config import Settings
from .doors import norm_door

REVIEW_FIELDS = ("review", "reviewer", "review_note")


def build_rows(units: gpd.GeoDataFrame, plan: dict, llm_runs: dict, doors: dict, assess: dict,
               s: Settings, source) -> tuple[list[dict], list[dict]]:
    """One result row per unit and one row per view. Also writes each unit's evidence page."""
    s.evidence_dir.mkdir(parents=True, exist_ok=True)
    tree = shapely.STRtree(units.geometry.values)
    rows, view_rows = [], []
    for unit in units.itertuples():
        info = plan.get(unit.unit_id)
        if info is None:
            continue
        runs = llm_runs.get(unit.unit_id, [])
        answers = [r["answer"] for r in runs if r.get("answer")]
        error = next((r["error"] for r in runs if r.get("error")), "")
        near = units.iloc[tree.query(unit.geometry, predicate="dwithin", distance=s.door_unique_m)]
        neighbours = {b: doors.get(b, set()) for b in set(near["building_id"]) if b != unit.building_id}
        own = doors.get(unit.building_id, set())
        core_ok = any(v.get("core_span") for v in info["views"])
        second_required = s.second_opinion != "none"
        verdict, why = rules.verdict(info["access"], answers, error, own, neighbours, core_ok, second_required)
        dates = sorted({v["pano_date"] for v in info["views"] if v.get("pano_date")})
        checked = verdict in ("Verified", "Unsure") and bool(answers)
        cmp = rules.compare(answers, info["record"], checked, dates[-1] if dates else None, s.old_imagery_before)
        first = answers[0] if answers else {}
        cls = first.get("classification") or {}
        conf = cls.get("confidence") or {}
        own_hits, _ = rules.door_evidence(answers, own, neighbours)
        row = {
            "unit_id": unit.unit_id, "building_id": unit.building_id, "part": unit.part, "road": unit.road,
            "access": info["access"], "verdict": verdict, "verdict_why": why, "flag": cmp["flag"],
            "llm_floors": cmp["llm_floors"], "rec_floors": cmp["rec_floors"], "floor_flag": cmp["floor_flag"],
            "floors_note": cls.get("floors_note"), "terrace": cls.get("terrace_structures"),
            "llm_usage": cmp["llm_usage"], "rec_usage": unit.usage, "assess_usage": assess.get(unit.building_id),
            "usage_flag": cmp["usage_flag"],
            "poss_shop": bool(cls.get("possible_shop")) if checked else None,
            "shutters": cls.get("shutters_on_ground_floor"),
            "trade_evid": "; ".join(map(str, cls.get("trade_evidence") or [])),
            "other_boards": "; ".join(map(str, cls.get("other_boards") or [])),
            "front_struct": cls.get("attached_front_structure"), "constr": cls.get("construction_status"),
            "roof": cls.get("roof_type"), "extras": "; ".join(map(str, cls.get("extras") or [])),
            "door_read": ", ".join(sorted({norm_door(n) for n in rules.numbers_read(answers) if n.strip()})),
            "door_record": ", ".join(sorted(own)), "door_match": ", ".join(own_hits),
            "n_views": len(info["views"]), "views_clear": len(rules.usable_views(first)),
            "views_agree": len(rules.agreeing_views(first)),
            "views_other": ", ".join(str(v.get("view")) for v in rules.usable_views(first)
                                     if v.get("same_building_as_reference") == "no"),
            "llm_runs": len(answers), "runs_agree": cmp["runs_agree"],
            "conf_ident": first.get("identity_confidence"), "conf_floors": conf.get("floors"), "conf_usage": conf.get("usage"),
            "pano_dates": ", ".join(dates), "llm_note": cls.get("notes"),
            "model": next((r.get("model") for r in runs if r.get("model")), None),
            "evidence": None, "review": None, "reviewer": None, "review_note": None,
            "geometry": unit.geometry,
        }
        if info["views"]:
            row["evidence"] = evidence.write_page(s.evidence_dir, row, info, answers, source)
        rows.append(row)
        said = first.get("views") or []
        for n, v in enumerate(info["views"], 1):
            lv = said[n - 1] if n - 1 < len(said) else {}
            mid = ((v["left"][0] + v["right"][0]) / 2, (v["left"][1] + v["right"][1]) / 2)
            view_rows.append({
                "unit_id": unit.unit_id, "view": n, "pano_id": v["pano_id"], "pano_date": v["pano_date"],
                "heading": v.get("aim", {}).get("heading"), "fov": v.get("aim", {}).get("fov"),
                "pitch": v.get("aim", {}).get("pitch"), "dist_m": v["dist_near"],
                "visible": lv.get("target_visible"), "hidden_by": lv.get("hidden_by"),
                "geometry": LineString([(v["px"], v["py"]), mid]),
            })
    return rows, view_rows


def write_layers(rows: list[dict], view_rows: list[dict], panos: gpd.GeoDataFrame, s: Settings) -> Path:
    """Write result.gpkg in the run folder. Reviewers' entries in an existing result are carried over."""
    out = s.run_dir / "result.gpkg"
    res = gpd.GeoDataFrame(rows, crs=s.metric_epsg).to_crs(4326)
    for col in ("poss_shop", "runs_agree"):                  # yes / no / empty reads better in QGIS than 1 / 0
        res[col] = res[col].map({True: "yes", False: "no"})
    for col in ("llm_floors", "rec_floors", "shutters"):
        res[col] = pd.to_numeric(res[col], errors="coerce").astype("Int64")
    if out.exists():
        old = gpd.read_file(out, layer="buildings_result", ignore_geometry=True)
        old = old[["unit_id", *REVIEW_FIELDS]].dropna(how="all", subset=list(REVIEW_FIELDS)).set_index("unit_id")
        for f in REVIEW_FIELDS:
            res[f] = res["unit_id"].map(old[f]) if len(old) else None
        out.unlink()
    res.to_file(out, layer="buildings_result", driver="GPKG")
    if view_rows:
        gpd.GeoDataFrame(view_rows, crs=s.metric_epsg).to_crs(4326).to_file(out, layer="views", driver="GPKG")
    used = {v["pano_id"] for v in view_rows}
    p = panos.copy()
    p["used"] = p["pano_id"].isin(used)
    p.to_crs(4326).to_file(out, layer="panoramas", driver="GPKG")
    back = len(gpd.read_file(out, layer="buildings_result", ignore_geometry=True))
    if back != len(res):
        raise RuntimeError(f"result layer has {back} rows, expected {len(res)}")
    return out


def summarise(rows: list[dict]) -> dict:
    """The counts a reviewer needs first."""
    df = pd.DataFrame(rows).drop(columns="geometry")
    checked = df[df["verdict"].isin(["Verified", "Unsure"]) & df["llm_usage"].notna()]
    ver = df[df["verdict"] == "Verified"]

    def share(frame, col, value):
        known = frame[frame[col] != "Cannot tell"]
        return None if not len(known) else round(100 * (known[col] == value).mean(), 1)

    return {
        "buildings": len(df),
        "verdict": dict(Counter(df["verdict"])),
        "flag": dict(Counter(df["flag"])),
        "verified_floors_same_pct": share(ver, "floor_flag", "Same"),
        "verified_usage_same_pct": share(ver, "usage_flag", "Same"),
        "checked_floors_same_pct": share(checked, "floor_flag", "Same"),
        "checked_usage_same_pct": share(checked, "usage_flag", "Same"),
        "possible_shops": int((df["poss_shop"] == True).sum()),  # noqa: E712
        "door_numbers_matched": int((df["door_match"] != "").sum()),
        "unsure_reasons": dict(Counter(df[df["verdict"] == "Unsure"]["verdict_why"].str.replace(r"\(.*?\)|:.*$", "", regex=True).str.strip()).most_common(8)),
    }


def write_summary(rows: list[dict], s: Settings) -> dict:
    summary = summarise(rows)
    (s.run_dir / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8")
    return summary
