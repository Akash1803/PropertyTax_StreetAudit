"""The team review page and bringing its answers back into the result layer.

The page is a static file in the run's evidence folder, so it works offline and travels with a zipped
run folder. Each reviewer downloads their answers as a JSON file; `import_answers` merges such files
into the review fields of the result layer.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import geopandas as gpd
import pandas as pd

from .config import Settings
from .results import REVIEW_FIELDS, load_result, save_result

PAGE = Path(__file__).with_name("review_page.html")
USAGES = ["Residential", "Commercial", "Mixed", "Industrial", "Educational Institutions", "Government Building",
          "Temple", "Church", "Office / Lodge / Theater / Restaurants", "Under Construction", "Vacant Land", "Others"]
IDENT = ("yes", "no", "cannot tell")


def _floors_text(value) -> str | None:
    if value is None or pd.isna(value):
        return None
    value = int(value)
    return "G" if value == 0 else f"G+{value}"


def review_data(result: gpd.GeoDataFrame, aimed: dict, tags: set[str], run_name: str,
                only: set[str] | None = None) -> dict:
    """What the page needs per building: images, the record, the LLM answer if there is one.

    `only` limits the page to these unit ids, e.g. a first batch of ten.
    """
    res = result if only is None else result[result["unit_id"].isin(only)]
    res = res.copy()
    res["northing"] = res.geometry.to_crs(3857).centroid.y
    units = []
    for r in res.sort_values("northing", ascending=False).itertuples():
        info = aimed.get(r.unit_id) or {}
        views = [{"img": v["image"], "date": v.get("pano_date"), "dist": round(v["dist_near"], 1)}
                 for v in info.get("views", []) if v.get("image")]
        if not views:
            continue
        llm = None
        if r.verdict in ("Verified", "Unsure") and isinstance(r.llm_usage, str):
            llm = {"verdict": r.verdict, "why": r.verdict_why, "floors": _floors_text(r.llm_floors),
                   "usage": r.llm_usage, "shop": r.poss_shop, "flag": r.flag}
        units.append({
            "id": r.unit_id, "b": r.building_id, "road": r.road, "tag": r.building_id in tags or r.unit_id in tags,
            "verdict": r.verdict, "views": views,
            "zoom": (info.get("zoom") or {}).get("image"), "zoom_of": (info.get("zoom") or {}).get("of_view"),
            "ortho": info.get("ortho_image"),
            "rec": {"usage": r.rec_usage, "floors": _floors_text(r.rec_floors), "assess": r.assess_usage,
                    "doors": r.door_record},
            "llm": llm,
        })
    return {"run": run_name, "generated": time.strftime("%Y-%m-%d %H:%M"), "usages": USAGES, "units": units}


def write_page(s: Settings, aimed: dict, tags: set[str], only: set[str] | None = None) -> tuple[Path, int]:
    """Write review.html and its data into the evidence folder. Returns the page and the number of buildings."""
    _, result, _, _ = load_result(s)
    data = review_data(result, aimed, tags, s.run_dir.name, only)
    s.evidence_dir.mkdir(parents=True, exist_ok=True)
    js = "window.REVIEW = " + json.dumps(data, ensure_ascii=False).replace("</", "<\\/") + ";\n"
    (s.evidence_dir / "review_data.js").write_text(js, encoding="utf-8")
    out = s.evidence_dir / "review.html"
    out.write_text(PAGE.read_text(encoding="utf-8"), encoding="utf-8")
    return out, len(data["units"])


def _clean(answer: dict) -> dict:
    """Check one answer from the page and turn it into the result's review fields."""
    ident = answer.get("ident")
    if ident not in IDENT:
        raise ValueError(f"unknown answer for 'right building': {ident!r}")
    floors = answer.get("floors")
    floors = None if floors in (None, "", "cannot tell") else int(floors)
    usage = answer.get("usage") or None
    if usage not in (None, "cannot tell") and usage not in USAGES:
        raise ValueError(f"unknown usage: {usage!r}")
    shop = answer.get("shop")
    summary = [f"right building: {ident}"]
    if ident == "yes":
        summary += [f"floors: {_floors_text(floors) or 'cannot tell'}", f"usage: {usage or 'cannot tell'}"]
        if shop:
            summary.append("shop on ground floor")
    return {"rev_ident": ident,
            "rev_floors": floors if ident == "yes" else None,
            "rev_usage": usage if ident == "yes" else None,
            "rev_shop": ("yes" if shop else "no") if ident == "yes" else None,
            "review": "; ".join(summary),
            "reviewer": answer.get("by") or None,
            "review_note": (answer.get("note") or "").strip() or None}


def import_answers(s: Settings, files: list[Path]) -> tuple[Path, dict]:
    """Merge answer files from the review page into the newest result. Returns (file written, counts).

    When two files answer the same building, the later answer wins and the disagreement is noted.
    """
    path, res, views, panos = load_result(s)
    for f in REVIEW_FIELDS:
        if f not in res.columns:                      # a result written before these fields existed
            res[f] = None
    res = res.set_index("unit_id", drop=False)
    counts = {"files": 0, "answers": 0, "unknown_buildings": 0, "disagreements": 0}
    seen: dict[str, dict] = {}
    batches = []
    for f in files:
        data = json.loads(Path(f).read_text(encoding="utf-8-sig"))
        if data.get("run") not in (None, s.run_dir.name):
            raise ValueError(f"{f} holds answers for run {data.get('run')!r}, not {s.run_dir.name!r}")
        batches.append(data)
        counts["files"] += 1
    answers = sorted(((a.get("at") or "", uid, a) for d in batches for uid, a in d.get("answers", {}).items()))
    for _, unit_id, answer in answers:
        if unit_id not in res.index:
            counts["unknown_buildings"] += 1
            continue
        fields = _clean(answer)
        before = seen.get(unit_id)
        if before and before["reviewer"] != fields["reviewer"] and (
                before["rev_ident"], before["rev_floors"], before["rev_usage"]) != (
                fields["rev_ident"], fields["rev_floors"], fields["rev_usage"]):
            counts["disagreements"] += 1
            note = f"earlier answer by {before['reviewer']}: {before['review']}"
            fields["review_note"] = "; ".join(x for x in (fields["review_note"], note) if x)
        seen[unit_id] = fields
        for k, v in fields.items():
            res.at[unit_id, k] = v
        counts["answers"] += 1
    out = save_result(res.reset_index(drop=True), views, panos, s)
    counts["unique_buildings"] = len(seen)
    return out, counts
