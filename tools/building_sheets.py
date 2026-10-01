"""Reading sheets for checking buildings by eye, with extra views where they help.

For every view the sheet shows
  - the target bracket "T" with the camera's distance to the building (near-far),
  - lettered brackets for each neighbouring footprint the camera can see, with its distance,
and the ortho chip carries the same letters, so a view that lands on a neighbour (44WN1679) shows.
Up to N extra views from unused panoramas on other sides are fetched and added to the run's views
(work/aimed.json), so the readings stored afterwards are tied to them.

usage: python tools/building_sheets.py -c configs/ward44.toml --ids-file ids.txt --out <sheet folder> [--extras 2]

Run the `images` stage (and the ortho chips) for the same buildings first.
"""
import argparse
import io
import json
import math
import string
import sys
from pathlib import Path

import geopandas as gpd
import shapely
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from streetaudit import data, visibility  # noqa: E402
from streetaudit.config import Settings, require_key  # noqa: E402
from streetaudit.geometry import aim, core_span, project, rel  # noqa: E402
from streetaudit.imaging import _font, mark_street  # noqa: E402
from streetaudit.sources import GoogleStreetView  # noqa: E402

YELLOW, CYAN, MAGENTA = (255, 235, 0), (0, 230, 255), (255, 0, 255)


def bracket(draw, x0, x1, y, label, colour, font):
    x0, x1 = max(2, min(x0, x1)), min(638, max(x0, x1))
    if x1 - x0 < 4:
        return
    draw.line([(x0, y), (x1, y)], fill=(0, 0, 0), width=6)
    draw.line([(x0, y), (x1, y)], fill=colour, width=3)
    for x in (x0, x1):
        draw.line([(x, y - 7), (x, y + 7)], fill=colour, width=3)
    tx = (x0 + x1) / 2 - 4 * len(label)
    draw.rectangle([tx - 3, y + 6, tx + 8.5 * len(label) + 3, y + 25], fill=(0, 0, 0))
    draw.text((tx, y + 7), label, fill=colour, font=font)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-c", "--config", required=True)
    ap.add_argument("--ids-file", required=True)
    ap.add_argument("--out", required=True, help="folder for the sheets (not the run folder)")
    ap.add_argument("--extras", type=int, default=2, help="extra views per building (default 2)")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    ids = [ln.strip() for ln in Path(args.ids_file).read_text(encoding="utf-8-sig").splitlines() if ln.strip()]
    s = Settings.load(args.config)
    sheet_dir = Path(args.out).resolve()
    if s.run_dir.resolve() in sheet_dir.parents or sheet_dir == s.run_dir.resolve():
        raise SystemExit("write the sheets outside the run folder; they are working copies")
    sheet_dir.mkdir(parents=True, exist_ok=True)

    units = data.load_units(s)
    blockers = data.load_blockers(s, units)
    tree = shapely.STRtree(blockers)
    uidx = units.set_index("unit_id")
    utree = shapely.STRtree(units.geometry.values)
    usable = visibility.usable_panoramas(gpd.read_file(s.work_dir / "panoramas.gpkg"), blockers, s)
    ptree = shapely.STRtree(usable.geometry.values)
    aimed_path = s.work_dir / "aimed.json"
    aimed = json.loads(aimed_path.read_text(encoding="utf-8"))
    chips = json.loads((s.work_dir / "chips" / "chips.json").read_text(encoding="utf-8"))
    src = GoogleStreetView(require_key("GOOGLE_MAPS_API_KEY"))
    font, big = _font(15), _font(20)
    n_req = 0

    for k, uid in enumerate(ids, 1):
        info = aimed.get(uid)
        if not info or not info.get("views") or not all(v.get("image") for v in info["views"]):
            print(f"{uid}: no images; run the images stage for it first")
            continue
        geom = uidx.loc[uid].geometry
        storeys = int(uidx.loc[uid].storeys or 1)
        # ---- extra views from unused panoramas, on other sides
        used = {v["pano_id"] for v in info["views"]}
        near = usable.iloc[ptree.query(geom, predicate="dwithin", distance=s.max_view_dist)]
        cands = [c for c in visibility.candidate_views(geom, near, tree, s) if c["pano_id"] not in used]
        added = 0
        for c in cands:
            if added >= args.extras or len(info["views"]) >= 6:
                break
            if any(math.hypot(c["px"] - v["px"], c["py"] - v["py"]) < 6 for v in info["views"]):
                continue
            c = {key: val for key, val in c.items() if not key.startswith("_")}
            c["extra"] = True
            a = aim(c, storeys, s.camera_height_m)
            c["aim"], c["core_span"] = a, core_span(c, s.position_error_m)
            img = src.image(c["pano_id"], a["heading"], a["fov"], a["pitch"], s.image_px)
            n_req += 1
            if img is None:
                continue
            n = len(info["views"]) + 1
            name = f"{uid}_v{n}.jpg"
            mark_street(Image.open(io.BytesIO(img)).convert("RGB"), c, a, f"VIEW {n}   {c['pano_date']}", s).save(
                s.evidence_dir / name, quality=90)
            c["image"] = name
            info["views"].append(c)
            added += 1
        # ---- neighbours with letters
        nb_idx = [i for i in utree.query(geom.buffer(25)) if units.iloc[i].unit_id != uid]
        nbs = sorted(((units.iloc[i].unit_id, units.iloc[i].geometry) for i in nb_idx),
                     key=lambda t: t[1].distance(geom))[:12]
        letters = {u: string.ascii_uppercase[j] for j, (u, _) in enumerate(nbs)}
        tiles = []
        for n, v in enumerate(info["views"], 1):
            im = Image.open(s.evidence_dir / v["image"]).convert("RGB")
            dr = ImageDraw.Draw(im)
            a = v["aim"]
            cam = (v["px"], v["py"])

            def xpix(b, d, a=a):
                p = project(rel(b, a["heading"]), d, 0.0, a["fov"], a["pitch"], s.image_px)
                return None if p is None else p[0]

            row = 40
            for u, g in nbs:
                nv = visibility.see(g, cam, tree, s)
                if nv is None or nv["visible_m"] < 1.5:
                    continue
                x0, x1 = xpix(nv["bearing_left"], nv["dist_left"]), xpix(nv["bearing_right"], nv["dist_right"])
                if x0 is None or x1 is None or max(x0, x1) < 0 or min(x0, x1) > 640:
                    continue
                bracket(dr, x0, x1, 560 - (row % 120), f"{letters[u]} {nv['dist_near']:.0f}m", CYAN, font)
                row += 30
            x0, x1 = xpix(v["bearing_left"], v["dist_left"]), xpix(v["bearing_right"], v["dist_right"])
            if x0 is not None and x1 is not None:
                far = max(v["dist_left"], v["dist_right"])
                bracket(dr, x0, x1, 600, f"T {v['dist_near']:.0f}-{far:.0f}m", YELLOW, font)
            tag = " EXTRA" if v.get("extra") else ""
            dr.rectangle([300, 0, 640, 26], fill=(0, 0, 0))
            dr.text((306, 3), f"cam {v['dist_near']:.1f} m{tag}", fill=YELLOW, font=big)
            tiles.append(im)
        if info.get("zoom"):
            tiles.append(Image.open(s.evidence_dir / info["zoom"]["image"]).convert("RGB"))
        chip = chips.get(uid)
        if info.get("ortho_image") and chip:
            o = Image.open(s.evidence_dir / info["ortho_image"]).convert("RGB")
            dr = ImageDraw.Draw(o)
            sc = o.size[0] / (chip["xmax"] - chip["xmin"])

            def pxy(x, y, chip=chip, sc=sc):
                return (x - chip["xmin"]) * sc, (chip["ymax"] - y) * sc

            for u, g in nbs:
                c = pxy(g.representative_point().x, g.representative_point().y)
                dr.rectangle([c[0] - 9, c[1] - 11, c[0] + 11, c[1] + 11], fill=(0, 0, 0))
                dr.text((c[0] - 5, c[1] - 10), letters[u], fill=CYAN, font=big)
            for n, v in enumerate(info["views"], 1):
                if v.get("extra"):
                    c = pxy(v["px"], v["py"])
                    for side in ("left", "right"):
                        dr.line([c, pxy(*v[side])], fill=MAGENTA, width=2)
                    dr.ellipse([c[0] - 9, c[1] - 9, c[0] + 9, c[1] + 9], fill=MAGENTA, outline=(0, 0, 0), width=2)
                    dr.text((c[0] - 4, c[1] - 9), str(n), fill=(0, 0, 0), font=font)
            tiles.append(o.resize((640, 640)))
        cols, tile = 3, 600
        rows = (len(tiles) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * tile, rows * tile), "white")
        for j, t in enumerate(tiles):
            sheet.paste(t.resize((tile, tile)), ((j % cols) * tile, (j // cols) * tile))
        sheet.save(sheet_dir / f"{k:02d}_{uid}.jpg", quality=84)
        minx, miny, maxx, maxy = geom.bounds
        print(f"{uid}: {len(info['views'])} views ({added} extra), footprint {geom.area:.0f} m2, "
              f"{maxx - minx:.0f} x {maxy - miny:.0f} m, survey road {uidx.loc[uid].road}, neighbours {''.join(letters.values())}")
        aimed_path.write_text(json.dumps(aimed, ensure_ascii=False), encoding="utf-8")   # after every building
    print("extra image requests:", n_req)


if __name__ == "__main__":
    main()
