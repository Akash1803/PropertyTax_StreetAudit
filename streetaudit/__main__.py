"""Command line: run the audit stage by stage.

    python -m streetaudit -c configs/ward44.toml panoramas
    python -m streetaudit -c configs/ward44.toml views
    (ortho chips: tools/export_ortho_chips.py, run with QGIS's Python)
    python -m streetaudit -c configs/ward44.toml images
    python -m streetaudit -c configs/ward44.toml llm
    python -m streetaudit -c configs/ward44.toml results

Street level (on a hand-digitised road layer, see docs/street-level-design.md):
    python -m streetaudit -c configs/ward44.toml streets
    python -m streetaudit -c configs/ward44.toml street-images --stretch-ids ...
    python -m streetaudit -c configs/ward44.toml street-export --stretch-ids ... --name s1

Every stage keeps what earlier runs produced, so a run can be interrupted and continued, and a stage
can be limited to some buildings with --ids, --ids-file, --line, --stretch-ids or --streets.
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

from . import data, evidence, export, imaging, llm, panoramas, results, review, rules, street_layer, visibility
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


def _ids(args) -> list[str] | None:
    ids = None
    if args.ids:
        ids = [i.strip() for i in args.ids.split(",") if i.strip()]
    if args.ids_file:
        ids = (ids or []) + [ln.strip() for ln in Path(args.ids_file).read_text(encoding="utf-8-sig").splitlines() if ln.strip()]
    return ids


def _split(text: str | None) -> list[str] | None:
    return [t.strip() for t in text.split(",") if t.strip()] if text else None


def _street_plan(s: Settings) -> dict:
    path = street_layer.work(s) / "plan.json"
    if not path.exists():
        raise SystemExit("no street plan yet: run the `streets` stage first")
    return street_layer.read_json(path, {})


def _chosen_stretches(args, s: Settings, plan: dict) -> list[str]:
    sids = street_layer.select_stretches(plan, _split(args.stretch_ids), _split(args.streets), _ids(args))
    if not sids:
        raise SystemExit("the selection holds no stretch")
    return sids


def _selection(args, s: Settings, units: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    ids = _ids(args)
    if args.stretch_ids or args.streets:
        # the buildings that belong to the chosen stretches
        plan = _street_plan(s)
        sids = set(street_layer.select_stretches(plan, _split(args.stretch_ids), _split(args.streets)))
        ids = (ids or []) + [u for u, a in plan["assign"].items() if a["stretch_id"] in sids]
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
            _write_json(path, {"key": key, "prompt": llm.prompt_version(), "runs": runs})
        first = runs[0].get("answer")
        if first and len(runs) < 2 and s.second_opinion != "none":
            near = units.iloc[tree.query(unit.geometry, predicate="dwithin", distance=s.door_unique_m)]
            neighbours = {b: doors.get(b, set()) for b in set(near["building_id"]) if b != unit.building_id}
            core_ok = any(v.get("core_span") for v in info["views"])
            if s.second_opinion == "all" or rules.needs_second_opinion(
                    first, doors.get(unit.building_id, set()), neighbours, core_ok):
                runs.append(client.ask(parts))
                _write_json(path, {"key": key, "prompt": llm.prompt_version(), "runs": runs})
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
                                         s, GoogleStreetView, ocr_doors={u: (r.get("merged") or {}).get("doors") or []
                                                                         for u, r in _ocr_records(s).items()})
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
    if _selected(args):
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


def _answers(s: Settings, aimed: dict, units) -> dict:
    return {u: [r["answer"] for r in _llm_runs(s.work_dir / "llm" / f"{u}.json", llm.request_key(aimed[u]))
                if r.get("answer")] for u in units if aimed.get(u, {}).get("views")}


def _ocr_records(s: Settings) -> dict:
    """unit id -> OCR record, for every building read so far."""
    return {p.stem: _read_json(p, {}) for p in (s.work_dir / "ocr").glob("*.json")}


def _street_of_units(s: Settings) -> dict:
    """unit id -> (stretch id, street name), when the street plan exists."""
    path = street_layer.work(s) / "plan.json"
    if not path.exists():
        return {}
    plan = street_layer.read_json(path, {})
    return {u: (a["stretch_id"], street_layer.street_name(plan["stretches"][a["stretch_id"]]))
            for u, a in plan["assign"].items()}


def _selected(args) -> bool:
    return bool(args.ids or args.ids_file or args.line or args.limit or args.stretch_ids or args.streets)


def stage_export(args, s: Settings) -> None:
    aimed = _read_json(s.work_dir / "aimed.json", {})
    only = None
    if _selected(args):
        only = set(_selection(args, s, data.load_units(s))["unit_id"])
    _, res, _, _ = results.load_result(s)
    units = res["unit_id"] if only is None else [u for u in res["unit_id"] if u in only]
    answers = _answers(s, aimed, units)
    out = s.run_dir / f"{s.run_dir.name}_AI_check{('_' + args.name) if args.name else ''}.geojson"
    corrections = _read_json(s.work_dir / "corrections.json", {})
    others = sorted(p for p in s.run_dir.glob(f"{s.run_dir.name}_AI_check*.geojson") if p != out)
    n = export.export_geojson(res, aimed, answers, out, s.evidence_dir, only, corrections, _street_of_units(s), others,
                              _ocr_records(s))
    style = out.with_suffix(".qml")           # same name as the layer: QGIS applies it when the layer is added
    if not style.exists():
        shutil.copyfile(Path(export.__file__).with_name("ai_check_style.qml"), style)
    size = sum(p.stat().st_size for p in (out.parent / "images").glob("*.jpg")) / 1e6
    say(f"layer written: {out} ({n} buildings) | pictures: {out.parent / 'images'} ({size:.1f} MB)")


def stage_prune(args, s: Settings) -> None:
    """Delete the full-size pictures and chips. The layer keeps its small pictures; the rest can be fetched again."""
    freed = 0
    for folder, patterns in ((s.evidence_dir, ("*.jpg", "*.html", "*.js")), (s.work_dir / "chips", ("*.jpg",)),
                             (s.evidence_dir / "streets", ("*.jpg",)), (s.work_dir / "street_chips", ("*.jpg",))):
        for pattern in patterns:
            for p in folder.glob(pattern):
                freed += p.stat().st_size
                p.unlink()
    say(f"freed {freed / 1e6:.0f} MB (full-size street views, zooms, along-road pictures, ortho chips, evidence pages)")


def stage_streets(args, s: Settings) -> None:
    """Stretches from the road layer, the buildings on each, picture points and front gaps. Local and free."""
    units = data.load_units(s)
    view_plan = _read_json(s.work_dir / "views.json", {})
    if not view_plan:
        raise SystemExit("no planned views yet: run the `views` stage first")
    blockers = data.load_blockers(s, units)
    panos = visibility.usable_panoramas(gpd.read_file(s.work_dir / "panoramas.gpkg"), blockers, s)
    plan = street_layer.plan_streets(s, units, view_plan, panos, blockers)
    street_layer.write_json(street_layer.work(s) / "plan.json", plan)
    sts = plan["stretches"]
    _write_json(s.work_dir / "street_chips_todo.json", street_layer.chips_todo(plan, list(sts), s.metric_epsg))
    roles = [a["role"] for a in plan["assign"].values()]
    n_pics = sum(1 for info in sts.values() for p in info["points"] if p["pano"])
    say(f"{plan['roads_count']} road lines -> {len(sts)} stretches, {sum(i['length_m'] for i in sts.values()) / 1000:.1f} km | "
        f"buildings on a drawn road: {roles.count('seen')} seen from it, {roles.count('cannot see')} cannot be seen | "
        f"along-road pictures possible: {n_pics} | no name drawn: "
        f"{sum(1 for i in sts.values() if not i['name_drawn'])}")
    for sid, info in sts.items():
        members = [u for u, a in plan["assign"].items() if a["stretch_id"] == sid]
        print(f"  {sid}  {street_layer.street_name(info) or '-':32.32}  {info['length_m']:6.0f} m  "
              f"buildings {len(members):3d}  pictures {sum(1 for p in info['points'] if p['pano'])}")


def stage_street_images(args, s: Settings) -> None:
    """Fetch the along-road pictures of the chosen stretches, mark the ortho and write the reading sheets."""
    plan = _street_plan(s)
    sids = _chosen_stretches(args, s, plan)
    source = GoogleStreetView(require_key("GOOGLE_MAPS_API_KEY"))
    path = street_layer.work(s) / "pictures.json"
    pictures = street_layer.read_json(path, {})
    pictures.update(street_layer.fetch_pictures(s, plan, sids, source, pictures))
    chips = _read_json(s.work_dir / "street_chips" / "chips.json", {})
    units = data.load_units(s)
    no_chip = []
    for sid in sids:
        overview = None
        chip = chips.get(sid)
        if chip and Path(chip["file"]).exists():
            overview = s.evidence_dir / "streets" / f"{sid}_ortho.jpg"
            street_layer.mark_overview(chip, sid, plan, pictures[sid]["pictures"], units).save(overview, quality=86)
        else:
            no_chip.append(sid)
        pictures[sid]["ortho_image"] = overview.name if overview else None
        pictures[sid]["sheets"] = street_layer.write_sheets(s, sid, plan["stretches"][sid], pictures[sid]["pictures"], overview)
    street_layer.write_json(path, pictures)
    fetched = sum(1 for sid in sids for p in pictures[sid]["pictures"] if p.get("image"))
    say(f"pictures for {len(sids)} stretches: {fetched} | sheets in {s.evidence_dir / 'streets'}")
    if no_chip:
        say(f"no ortho chip for {len(no_chip)} stretches; cut them with tools/export_ortho_chips.py --todo street_chips")


def stage_street_export(args, s: Settings) -> None:
    """The street layers for the chosen stretches, from the building check, the zones and the street readings."""
    plan = _street_plan(s)
    sids = _chosen_stretches(args, s, plan)
    pictures = street_layer.read_json(street_layer.work(s) / "pictures.json", {})
    readings = street_layer.load_readings(s, pictures)
    aimed = _read_json(s.work_dir / "aimed.json", {})
    _, res, _, _ = results.load_result(s)
    res = res.to_crs(s.metric_epsg)
    corrections = _read_json(s.work_dir / "corrections.json", {})
    rows = export.building_rows(res, aimed, _answers(s, aimed, res["unit_id"]), s.evidence_dir, None, None, corrections)
    bldg_rows = dict(zip(res["unit_id"], rows))
    lines, points = street_layer.street_rows(s, plan, pictures, readings, bldg_rows, street_layer.assessment_zones(s),
                                             sids, s.run_dir / "images" / "streets", _ocr_records(s))
    suffix = ("_" + args.name) if args.name else ""
    out_lines = s.run_dir / f"{s.run_dir.name}_streets{suffix}.geojson"
    out_points = s.run_dir / f"{s.run_dir.name}_street_views{suffix}.geojson"
    n_lines, n_points, orphans = street_layer.export_streets(s, lines, points, out_lines, out_points, set(plan["stretches"]))
    for out, style in ((out_lines, "street_style.qml"), (out_points, "street_views_style.qml")):
        src = Path(export.__file__).with_name(style)
        if src.exists() and not out.with_suffix(".qml").exists():
            shutil.copyfile(src, out.with_suffix(".qml"))
    say(f"streets layer: {out_lines} ({n_lines} stretches, {sum(1 for r in lines if r['check'] == 'Read')} read) | "
        f"street views: {out_points} ({n_points} pictures)")
    if orphans:
        say(f"notes on {len(orphans)} stretches that no longer exist (a new road split them): "
            f"{street_layer.work(s) / 'orphan_notes.json'}")


def stage_import_screenshots(args, s: Settings) -> None:
    """Pictures a checker took in the Street View viewer, named by building id, become the building's views."""
    from . import screenshots
    if len(args.files) != 1 or not Path(args.files[0]).is_dir():
        raise SystemExit("give the folder with the screenshots, e.g. import-screenshots D:\\...\\screenshots --by Akash")
    folder, by = Path(args.files[0]), args.by or "checker"
    units = data.load_units(s)
    plan = _read_json(s.work_dir / "views.json", {})
    aimed = _read_json(s.work_dir / "aimed.json", {})
    parts = {}
    for u in units.itertuples():
        parts.setdefault(u.building_id, []).append(u.unit_id)
    found, unknown = screenshots.match_files(folder, set(plan), parts)
    clicks = screenshots.load_clicks(s.work_dir / "clicks.log")
    by_click, rejected = screenshots.match_by_clicks(unknown, clicks, set(plan), parts) if clicks else ({}, [(f, "no click log") for f in unknown])
    for u, fs in by_click.items():
        found.setdefault(u, []).extend(fs)
    chips = _read_json(s.work_dir / "chips" / "chips.json", {})
    sheets_dir = s.evidence_dir / "screenshot_sheets"
    done, not_facing = [], []
    for unit_id, files in found.items():
        info = plan[unit_id]
        if info.get("access") != "street-facing":
            not_facing.append(unit_id)
        base = {**info, "unit_id": unit_id, "views": (aimed.get(unit_id) or info).get("views") or info.get("views") or []}
        views = screenshots.as_views(base, files, s, by)
        aimed[unit_id] = {**info, "views": views, "zoom": None, "ortho_image": None, "screenshots": True}
        screenshots.sheet(s, unit_id, views, chips.get(unit_id), units, sheets_dir)
        for f in files:
            screenshots.clean_folder_copy(f, s.work_dir / "screenshots_in")
        done.append(unit_id)
    _write_json(s.work_dir / "aimed.json", aimed)
    have = {u for u, a in aimed.items() if a.get("screenshots")} | {
        u for u in plan if (s.work_dir / "llm" / f"{u}.json").exists()}
    missing = screenshots.missing_list(plan, have)
    (s.work_dir / "screenshots_missing.txt").write_text("\n".join(missing) + "\n", encoding="utf-8")
    say(f"screenshots imported for {len(done)} buildings ({sum(len(v) for v in found.values())} pictures) | "
        f"sheets: {sheets_dir} | still without a picture: {len(missing)} (work\\screenshots_missing.txt)")
    if by_click:
        say(f"{sum(len(v) for v in by_click.values())} time-named files were matched through the click log")
    for f, why in rejected:
        say(f"  skipped {f.name}: {why}")
    if not_facing:
        say(f"{len(not_facing)} buildings the planner marked not visible got a picture anyway (kept): " + ", ".join(not_facing[:10]))
    if chips and any(u not in chips for u in done):
        say("some buildings have no ortho chip on the sheet; cut them with tools/export_ortho_chips.py <ortho> <run> ids.txt")



def stage_ocr(args, s: Settings) -> None:
    """Read shop names, door numbers, pin codes and road names off the pictures on file. Offline, free."""
    from . import ocr
    aimed = _read_json(s.work_dir / "aimed.json", {})
    units = data.load_units(s)
    sel = _selection(args, s, units) if _selected(args) else units
    out_dir = s.work_dir / "ocr"
    todo, skipped = [], 0
    for u in sel["unit_id"]:
        info = aimed.get(u) or {}
        imgs = [s.evidence_dir / v["image"] for v in info.get("views", []) if v.get("image")]
        if info.get("zoom", {}) and info["zoom"].get("image"):
            imgs.append(s.evidence_dir / info["zoom"]["image"])
        imgs = [i for i in imgs if i.exists()]
        if not imgs:
            thumb = s.run_dir / "images" / f"{u}.jpg"       # after prune only the layer's small picture is left
            imgs = [thumb] if thumb.exists() else []
        if not imgs:
            continue
        if (out_dir / f"{u}.json").exists() and not args.retry_failed:
            skipped += 1
            continue
        todo.append((u, imgs))
    say(f"OCR on {len(todo)} buildings ({sum(len(i) for _, i in todo)} pictures), {skipped} already done; "
        "first run downloads the Tamil + English models")
    for n, (u, imgs) in enumerate(todo, 1):
        rec = ocr.read_unit(u, imgs, out_dir / f"{u}.json")
        m = rec["merged"]
        if n % 10 == 0 or n == len(todo):
            say(f"  {n}/{len(todo)}  {u}: roads {m['road_names']} doors {m['doors']} shops {m['shop_lines'][:2]}")


STAGES = {"panoramas": stage_panoramas, "views": stage_views, "images": stage_images, "llm": stage_llm,
          "import-screenshots": stage_import_screenshots, "ocr": stage_ocr,
          "results": stage_results, "export": stage_export, "prune": stage_prune,
          "review-page": stage_review_page, "import-review": stage_import_review,
          "streets": stage_streets, "street-images": stage_street_images, "street-export": stage_street_export}


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
    ap.add_argument("--stretch-ids", help="comma-separated stretch ids (see the `streets` stage); building stages "
                                          "then work on the buildings of those stretches")
    ap.add_argument("--streets", help="comma-separated street names, any spelling; like --stretch-ids")
    ap.add_argument("--retry-failed", action="store_true", help="llm: ask again where the last attempt failed; ocr: read again")
    ap.add_argument("--tag-file", help="review-page: building ids to mark as the review set")
    ap.add_argument("--name", help="export: suffix for the layer name, e.g. batch2 -> <run>_AI_check_batch2.geojson")
    ap.add_argument("files", nargs="*", help="import-review: answer files; import-screenshots: the folder")
    ap.add_argument("--by", help="import-screenshots: who took the pictures")
    ap.add_argument("--second-opinion", choices=["verify", "all", "none"],
                    help="override the setting: ask the LLM a second time before verifying (verify), always, or never")
    args = ap.parse_args(argv)
    s = Settings.load(args.config)
    if args.second_opinion:
        s = dataclasses.replace(s, second_opinion=args.second_opinion)
    STAGES[args.stage](args, s)


if __name__ == "__main__":
    main()
