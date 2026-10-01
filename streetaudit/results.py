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

# filled in by reviewers (review page or QGIS); kept whenever the result is written again
REVIEW_FIELDS = ("review", "reviewer", "review_note", "rev_ident", "rev_floors", "rev_usage", "rev_shop")


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
        # a reading made once, deliberately, by a person or an assistant checking by eye is not re-asked
        second_required = s.second_opinion != "none" and not any(r.get("single_reading") for r in runs)
        verdict, why = rules.verdict(info["access"], answers, error, own, neighbours, core_ok, second_required)
        if any(r.get("single_reading") for r in runs):
            why = why.replace("(LLM asked once)", "(checked once by eye)")
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
            "evidence": None, **{f: None for f in REVIEW_FIELDS},
            "geometry": unit.geometry,
        }
        if info["views"] and info["views"][0].get("image") and (s.evidence_dir / info["views"][0]["image"]).exists():
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


def latest_result(run_dir: Path) -> Path | None:
    """The newest result file of a run: result.gpkg, or result_2.gpkg, result_3.gpkg ... when those exist."""
    files = [p for p in run_dir.glob("result*.gpkg") if p.stem == "result" or p.stem[7:].isdigit()]
    return max(files, key=lambda p: int(p.stem[7:]) if p.stem != "result" else 1, default=None)


def write_layers(rows: list[dict], view_rows: list[dict], panos: gpd.GeoDataFrame, s: Settings) -> Path:
    """Write the result layers into the run folder and return the file written.

    Reviewers' entries in the newest existing result are carried over. The result is written to a new
    file first and then put in place of result.gpkg. When result.gpkg is open in QGIS it cannot be
    replaced (and must not be deleted under QGIS), so the new result is kept beside it as result_<n>.gpkg.
    """
    res = gpd.GeoDataFrame(rows, crs=s.metric_epsg).to_crs(4326)
    for col in ("poss_shop", "runs_agree"):                  # yes / no / empty reads better in QGIS than 1 / 0
        res[col] = res[col].map({True: "yes", False: "no"})
    previous = latest_result(s.run_dir)
    if previous is not None:
        old = gpd.read_file(previous, layer="buildings_result", ignore_geometry=True)
        kept = [f for f in REVIEW_FIELDS if f in old.columns]
        old = old[["unit_id", *kept]].dropna(how="all", subset=kept).set_index("unit_id")
        for f in kept:
            res[f] = res["unit_id"].map(old[f]) if len(old) else None
    views = gpd.GeoDataFrame(view_rows, crs=s.metric_epsg).to_crs(4326) if view_rows else None
    used = {v["pano_id"] for v in view_rows}
    p = panos.copy()
    p["used"] = p["pano_id"].isin(used)
    return save_result(res, views, p.to_crs(4326), s)


def _typed(res: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    res = res.copy()
    for col in ("llm_floors", "rec_floors", "shutters", "rev_floors"):
        if col in res.columns:
            res[col] = pd.to_numeric(res[col], errors="coerce").astype("Int64")
    for col in REVIEW_FIELDS:
        if col in res.columns and col != "rev_floors":
            res[col] = res[col].astype(object).where(res[col].notna(), None)
    return res


def save_result(res: gpd.GeoDataFrame, views: gpd.GeoDataFrame | None, panos: gpd.GeoDataFrame | None,
                s: Settings) -> Path:
    """Write the three layers to a new file and put it in place of the newest result.

    When the newest result is open in QGIS it cannot be replaced (and must not be deleted under QGIS),
    so the new file is kept beside it as result_<n>.gpkg. Returns the file that holds the new result.
    """
    res = _typed(res)
    previous = latest_result(s.run_dir)
    number = 1 if previous is None else (1 if previous.stem == "result" else int(previous.stem[7:])) + 1
    new = s.run_dir / (f"result_{number}.gpkg" if number > 1 else "result.gpkg")
    res.to_file(new, layer="buildings_result", driver="GPKG")
    if views is not None and len(views):
        views.to_file(new, layer="views", driver="GPKG")
    if panos is not None and len(panos):
        panos.to_file(new, layer="panoramas", driver="GPKG")
    back = len(gpd.read_file(new, layer="buildings_result", ignore_geometry=True))
    if back != len(res):
        raise RuntimeError(f"result layer has {back} rows, expected {len(res)}")
    if previous is not None:
        try:                                   # not open anywhere: the new result simply takes its place
            new.replace(previous)
            return previous
        except PermissionError:
            pass                               # open in QGIS: leave it alone, the new file stands beside it
    return new


def load_result(s: Settings) -> tuple[Path, gpd.GeoDataFrame, gpd.GeoDataFrame | None, gpd.GeoDataFrame | None]:
    """The newest result file and its three layers."""
    path = latest_result(s.run_dir)
    if path is None:
        raise FileNotFoundError(f"no result in {s.run_dir}; run the results stage first")
    import pyogrio
    layers = {name for name, _ in pyogrio.list_layers(path)}
    res = gpd.read_file(path, layer="buildings_result")
    views = gpd.read_file(path, layer="views") if "views" in layers else None
    panos = gpd.read_file(path, layer="panoramas") if "panoramas" in layers else None
    return path, res, views, panos


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
        "review": review_scores(df),
    }


def _same_usage(a, b) -> bool:
    return rules._norm_usage(a) == rules._norm_usage(b)


def review_scores(df: pd.DataFrame) -> dict:
    """How the LLM and the survey record compare with what the reviewers saw.

    Only buildings a reviewer confirmed as the right building count for floors and usage.
    """
    if "rev_ident" not in df.columns:
        return {"reviewed": 0}
    rev = df[df["rev_ident"].notna()]
    out = {"reviewed": len(rev), "reviewer_said": dict(Counter(rev["rev_ident"]))}
    ver = rev[rev["verdict"] == "Verified"]
    out["llm_verified_reviewed"] = len(ver)
    out["llm_verified_but_wrong_building"] = int((ver["rev_ident"] == "no").sum())
    right = rev[rev["rev_ident"] == "yes"]

    def pct(frame, test):
        return {"n": len(frame), "pct": None if not len(frame) else round(100 * frame.apply(test, axis=1).mean(), 1)}

    fl = right[pd.to_numeric(right["rev_floors"], errors="coerce").notna()]
    out["floors_llm_vs_reviewer"] = pct(fl[fl["llm_floors"].notna()], lambda r: int(r["llm_floors"]) == int(r["rev_floors"]))
    out["floors_record_vs_reviewer"] = pct(fl[fl["rec_floors"].notna()], lambda r: int(r["rec_floors"]) == int(r["rev_floors"]))
    us = right[right["rev_usage"].notna() & (right["rev_usage"] != "cannot tell")]
    out["usage_llm_vs_reviewer"] = pct(us[us["llm_usage"].notna()], lambda r: _same_usage(r["llm_usage"], r["rev_usage"]))
    out["usage_record_vs_reviewer"] = pct(us[us["rec_usage"].notna()], lambda r: _same_usage(r["rec_usage"], r["rev_usage"]))
    shop = right[right["poss_shop"].isin([True, "yes"])]
    out["possible_shop_confirmed"] = {"flagged": len(shop), "reviewer_saw_shop": int((shop["rev_shop"] == "yes").sum())}
    return out


def write_summary(rows: list[dict], s: Settings) -> dict:
    summary = summarise(rows)
    (s.run_dir / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8")
    return summary
