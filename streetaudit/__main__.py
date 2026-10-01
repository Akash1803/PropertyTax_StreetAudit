"""Command line: run the audit stage by stage.

    python -m streetaudit -c configs/ward44.toml panoramas
    python -m streetaudit -c configs/ward44.toml views
    (ortho chips: tools/export_ortho_chips.py, run with QGIS's Python)
    python -m streetaudit -c configs/ward44.toml images
    python -m streetaudit -c configs/ward44.toml llm
    python -m streetaudit -c configs/ward44.toml results

Every stage keeps what earlier runs produced, so a run can be interrupted and continued, and a stage
can be limited to some buildings with --ids, --ids-file or --line.
"""
from __future__ import annotations

import argparse
import dataclasses
import glob
import json
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import geopandas as gpd
import pandas as pd
import shapely

from . import data, evidence, export, imaging, llm, panoramas, results, review, rules, visibility
from .config import Settings, require_key
from .sources import GoogleStreetView


def say(text: str) -> None:
    print(time.strftime("%H:%M:%S"), text, flush=True)


def _read_json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _llm_runs(path: Path, key: str) -> list[dict]:
    """The LLM answers on file for a building, unless they were given for other images or another prompt."""
    saved = _read_json(path, {})
    return saved.get("runs", []) if isinstance(saved, dict) and saved.get("key") == key else []


def _selection(args, s: Settings, units: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    ids = None
    if args.ids:
        ids = [i.strip() for i in args.ids.split(",") if i.strip()]
    if args.ids_file:
        ids = (ids or []) + [ln.strip() for ln in Path(args.ids_file).read_text(encoding="utf-8").splitlines() if ln.strip()]
    line = None
    if args.line:
        line = gpd.GeoSeries([shapely.from_wkt(args.line)], crs=4326).to_crs(s.metric_epsg).iloc[0]
    sel = data.select_units(units, ids, line, args.buffer, args.max_length)
    if args.limit:
        sel = sel.head(args.limit)
    if sel.empty:
        raise SystemExit("the selection holds no building")
    return sel


def stage_panoramas(args, s: Settings) -> None:
    units = data.load_units(s)
    sel = _selection(args, s, units)
    blockers = data.load_blockers(s, units)
    source = GoogleStreetView(require_key("GOOGLE_MAPS_API_KEY"))
    say(f"searching panoramas around {len(sel)} buildings")
    found = panoramas.find_panoramas(sel, blockers, source, s, progress=say)
    cache = s.work_dir / "panoramas.gpkg"
    if cache.exists():
        old = gpd.read_file(cache)
        found = gpd.GeoDataFrame(
            pd.concat([old[~old["pano_id"].isin(found["pano_id"])], found], ignore_index=True), crs=s.metric_epsg)
        cache.unlink()
    s.work_dir.mkdir(parents=True, exist_ok=True)
    found.to_file(cache, driver="GPKG")
    say(f"panoramas known: {len(found)} | the source's own: {int(found['own'].sum())} | "
        f"plotting inside a footprint: {int(found['inside'].sum())}")


def stage_views(args, s: Settings) -> None:
    units = data.load_units(s)
    sel = _selection(args, s, units)
    blockers = data.load_blockers(s, units)
    panos = gpd.read_file(s.work_dir / "panoramas.gpkg")
    plan = _read_json(s.work_dir / "views.json", {})
    plan.update(visibility.plan_views(sel, panos, blockers, s, progress=say))
    _write_json(s.work_dir / "views.json", plan)
    chips = {}
    for unit_id, info in plan.items():
        if info["views"]:
            cx, cy = info["centroid"]
            chips[unit_id] = {"xmin": cx - s.chip_half_m, "ymin": cy - s.chip_half_m,
                              "xmax": cx + s.chip_half_m, "ymax": cy + s.chip_half_m}
    _write_json(s.work_dir / "chips_todo.json", {"epsg": s.metric_epsg, "px": s.chip_px, "chips": chips})
    counts = {}
    for unit_id in sel["unit_id"]:
        counts[plan[unit_id]["access"]] = counts.get(plan[unit_id]["access"], 0) + 1
    n_views = sum(len(plan[u]["views"]) for u in sel["unit_id"])
    say(f"views planned for {len(sel)} buildings: {counts} | views to fetch: {n_views}")


def stage_images(args, s: Settings) -> None:
    units = data.load_units(s)
    sel = _selection(args, s, units)
    plan = _read_json(s.work_dir / "views.json", {})
    chips = _read_json(s.work_dir / "chips" / "chips.json", {})
    todo = {u: plan[u] for u in sel["unit_id"] if u in plan}
    source = GoogleStreetView(require_key("GOOGLE_MAPS_API_KEY"))
    say(f"fetching and marking images for {sum(1 for i in todo.values() if i['views'])} buildings")
    aimed = _read_json(s.work_dir / "aimed.json", {})
    aimed.update(imaging.build_evidence(todo, units, chips, source, s, previous=aimed, progress=say))
    _write_json(s.work_dir / "aimed.json", aimed)
    failed = sum(1 for u in todo for v in aimed[u]["views"] if not v.get("image"))
    say(f"images done | views that could not be fetched: {failed} | buildings without an ortho chip: "
        f"{sum(1 for u in todo if aimed[u]['views'] and not aimed[u].get('ortho_image'))}")


def stage_llm(args, s: Settings) -> None:
    units = data.load_units(s)
    sel = _selection(args, s, units)
    aimed = _read_json(s.work_dir / "aimed.json", {})
    doors = data.load_doors(s)
    client = llm.GeminiClient(require_key("GEMINI_API_KEY"), s.llm_models)
    out_dir = s.work_dir / "llm"
    out_dir.mkdir(parents=True, exist_ok=True)
    tree = shapely.STRtree(units.geometry.values)

    def one(unit) -> tuple[str, int, str]:
        path = out_dir / f"{unit.unit_id}.json"
        info = aimed.get(unit.unit_id)
        parts = llm.build_request(info, s.evidence_dir) if info else None
        if parts is None:
            return unit.unit_id, 0, "no image"
        key = llm.request_key(info)
        runs = _llm_runs(path, key)
        if args.retry_failed:
            runs = [r for r in runs if r.get("answer")]
        if not runs:
            runs.append(client.ask(parts))
            _write_json(path, {"key": key, "runs": runs})
        first = runs[0].get("answer")
        if first and len(runs) < 2 and s.second_opinion != "none":
            near = units.iloc[tree.query(unit.geometry, predicate="dwithin", distance=s.door_unique_m)]
            neighbours = {b: doors.get(b, set()) for b in set(near["building_id"]) if b != unit.building_id}
            core_ok = any(v.get("core_span") for v in info["views"])
            if s.second_opinion == "all" or rules.needs_second_opinion(
                    first, doors.get(unit.building_id, set()), neighbours, core_ok):
                runs.append(client.ask(parts))
                _write_json(path, {"key": key, "runs": runs})
        return unit.unit_id, len(runs), next((r["error"] for r in runs if r.get("error")), "")

    todo = [u for u in sel.itertuples() if aimed.get(u.unit_id, {}).get("views")]
    say(f"asking the LLM about {len(todo)} buildings with {s.llm_workers} workers, models {list(s.llm_models)}")
    calls = errors = 0
    with ThreadPoolExecutor(max_workers=s.llm_workers) as pool:
        for n, (unit_id, n_runs, error) in enumerate(pool.map(one, todo), 1):
            calls += n_runs
            if error:
                errors += 1
                say(f"  {unit_id}: {error}")
            if n % 25 == 0 or n == len(todo):
                say(f"LLM: {n}/{len(todo)} buildings, {calls} answers on file, {errors} with an error")


def stage_results(args, s: Settings) -> None:
    # always the whole run: the result layer must not shrink to the buildings of the last batch
    units = data.load_units(s)
    sel = units
    aimed = _read_json(s.work_dir / "aimed.json", {})
    plan = _read_json(s.work_dir / "views.json", {})
    merged = {u: aimed.get(u, plan[u]) for u in plan}
    llm_runs = {u: _llm_runs(s.work_dir / "llm" / f"{u}.json", llm.request_key(merged[u]))
                for u in sel["unit_id"] if merged.get(u, {}).get("views")}
    rows, view_rows = results.build_rows(sel, merged, llm_runs, data.load_doors(s), data.load_assessment_usages(s),
                                         s, GoogleStreetView)
    if any(r["evidence"] for r in rows):            # no pages once the full-size pictures are pruned
        evidence.write_index(s.evidence_dir, rows, f"Street audit - {s.run_dir.name}")
    out = results.write_layers(rows, view_rows, gpd.read_file(s.work_dir / "panoramas.gpkg"), s)
    summary = results.write_summary(rows, s)
    say(f"result written: {out}")
    print(json.dumps(summary, indent=1, ensure_ascii=False))


def stage_review_page(args, s: Settings) -> None:
    aimed = _read_json(s.work_dir / "aimed.json", {})
    tags = set()
    if args.tag_file:
        tags = {ln.strip() for ln in Path(args.tag_file).read_text(encoding="utf-8").splitlines() if ln.strip()}
    only = None
    if args.ids or args.ids_file or args.line or args.limit:
        only = set(_selection(args, s, data.load_units(s))["unit_id"])
    out, n = review.write_page(s, aimed, tags, only)
    say(f"review page written for {n} buildings: {out}")
    say("open it in Chrome or Edge; each reviewer downloads their answers and you bring them in with import-review")


def stage_import_review(args, s: Settings) -> None:
    if not args.files:
        raise SystemExit("give the answer files to import, e.g. import-review review_ward44_Akash_*.json")
    # PowerShell does not expand wildcards for programs, so patterns are expanded here
    files = [Path(p) for f in args.files for p in (sorted(glob.glob(f)) if any(c in f for c in "*?") else [f])]
    if not files:
        raise SystemExit(f"no file matches {args.files}")
    out, counts = review.import_answers(s, files)
    say(f"answers imported into {out}: {counts}")
    _, res, _, _ = results.load_result(s)
    print(json.dumps(results.review_scores(res.drop(columns="geometry")), indent=1, ensure_ascii=False))


def stage_export(args, s: Settings) -> None:
    aimed = _read_json(s.work_dir / "aimed.json", {})
    only = None
    if args.ids or args.ids_file or args.line or args.limit:
        only = set(_selection(args, s, data.load_units(s))["unit_id"])
    _, res, _, _ = results.load_result(s)
    units = res["unit_id"] if only is None else [u for u in res["unit_id"] if u in only]
    answers = {u: [r["answer"] for r in _llm_runs(s.work_dir / "llm" / f"{u}.json", llm.request_key(aimed[u]))
                   if r.get("answer")] for u in units if aimed.get(u, {}).get("views")}
    out = s.run_dir / f"{s.run_dir.name}_AI_check.geojson"
    n = export.export_geojson(res, aimed, answers, out, s.evidence_dir, only)
    style = out.with_suffix(".qml")           # same name as the layer: QGIS applies it when the layer is added
    if not style.exists():
        shutil.copyfile(Path(export.__file__).with_name("ai_check_style.qml"), style)
    size = sum(p.stat().st_size for p in (out.parent / "images").glob("*.jpg")) / 1e6
    say(f"layer written: {out} ({n} buildings) | pictures: {out.parent / 'images'} ({size:.1f} MB)")


def stage_prune(args, s: Settings) -> None:
    """Delete the full-size pictures and chips. The layer keeps its small pictures; the rest can be fetched again."""
    freed = 0
    for folder, patterns in ((s.evidence_dir, ("*.jpg", "*.html", "*.js")), (s.work_dir / "chips", ("*.jpg",))):
        for pattern in patterns:
            for p in folder.glob(pattern):
                freed += p.stat().st_size
                p.unlink()
    say(f"freed {freed / 1e6:.0f} MB (full-size street views, zooms, ortho chips, evidence pages)")


STAGES = {"panoramas": stage_panoramas, "views": stage_views, "images": stage_images, "llm": stage_llm,
          "results": stage_results, "export": stage_export, "prune": stage_prune,
          "review-page": stage_review_page, "import-review": stage_import_review}


def main(argv: list[str] | None = None) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="streetaudit", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-c", "--config", required=True, help="TOML settings file of the run")
    ap.add_argument("stage", choices=STAGES)
    ap.add_argument("--ids", help="comma-separated building ids to work on")
    ap.add_argument("--ids-file", help="text file with one building id per line")
    ap.add_argument("--line", help="a street as WKT LINESTRING in EPSG:4326; buildings near it are worked on")
    ap.add_argument("--buffer", type=float, default=20.0, help="distance from --line in metres (default 20)")
    ap.add_argument("--max-length", type=float, help="use only the first metres of --line")
    ap.add_argument("--limit", type=int, help="work on the first N buildings of the selection")
    ap.add_argument("--retry-failed", action="store_true", help="llm stage: ask again where the last attempt failed")
    ap.add_argument("--tag-file", help="review-page: building ids to mark as the review set")
    ap.add_argument("files", nargs="*", help="import-review: answer files downloaded from the review page")
    ap.add_argument("--second-opinion", choices=["verify", "all", "none"],
                    help="override the setting: ask the LLM a second time before verifying (verify), always, or never")
    args = ap.parse_args(argv)
    s = Settings.load(args.config)
    if args.second_opinion:
        s = dataclasses.replace(s, second_opinion=args.second_opinion)
    STAGES[args.stage](args, s)


if __name__ == "__main__":
    main()
