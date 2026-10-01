"""Cut ortho chips. Run with QGIS's Python, because only its GDAL reads ECW:

    "C:\\Program Files\\QGIS 3.40.10\\bin\\python-qgis-ltr.bat" tools\\export_ortho_chips.py <ortho file> <run_dir> [ids.txt] [--todo NAME]

Reads <run_dir>/work/NAME_todo.json and writes JPEG chips plus <run_dir>/work/NAME/chips.json.
NAME is `chips` (one chip per building, listed by the `views` stage) or `street_chips` (one chip per
stretch of road, listed by the `streets` stage). Chips already on disk are kept, so the script can be
rerun. With an ids file (one id per line) only those get a chip.
"""
import argparse
import json
from pathlib import Path

import numpy as np
from osgeo import gdal

gdal.UseExceptions()


def read_chip(ds, c: dict, px: int):
    """The window c (map coordinates) as a px-by-px RGB array, white where it lies outside the ortho.

    The window is read in one call: reading an ECW strip by strip (as gdal.Translate does) leaves
    horizontal seams in the chip.
    """
    x0, dx, _, y0, _, dy = ds.GetGeoTransform()
    col0, col1 = (c["xmin"] - x0) / dx, (c["xmax"] - x0) / dx
    row0, row1 = (c["ymax"] - y0) / dy, (c["ymin"] - y0) / dy
    scale = px / (col1 - col0)
    a0, a1 = max(0, int(col0)), min(ds.RasterXSize, int(col1))
    b0, b1 = max(0, int(row0)), min(ds.RasterYSize, int(row1))
    if a1 - a0 < 2 or b1 - b0 < 2:
        return None
    out_w, out_h = max(1, round((a1 - a0) * scale)), max(1, round((b1 - b0) * scale))
    part = ds.ReadAsArray(a0, b0, a1 - a0, b1 - b0, buf_xsize=out_w, buf_ysize=out_h, band_list=[1, 2, 3])
    chip = np.full((3, px, px), 255, dtype=np.uint8)
    ox, oy = min(px - 1, round((a0 - col0) * scale)), min(px - 1, round((b0 - row0) * scale))
    w, h = min(out_w, px - ox), min(out_h, px - oy)
    chip[:, oy:oy + h, ox:ox + w] = part[:, :h, :w]
    return chip


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("ortho")
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("ids_file", nargs="?")
    ap.add_argument("--todo", default="chips", help="chips (buildings) or street_chips (stretches)")
    args = ap.parse_args()
    run_dir = args.run_dir
    todo = json.loads((run_dir / "work" / f"{args.todo}_todo.json").read_text(encoding="utf-8"))
    if args.ids_file:
        wanted = {ln.strip() for ln in Path(args.ids_file).read_text(encoding="utf-8-sig").splitlines() if ln.strip()}
        todo["chips"] = {u: c for u, c in todo["chips"].items() if u in wanted or u.split("_p")[0] in wanted}
    out_dir = run_dir / "work" / args.todo
    out_dir.mkdir(parents=True, exist_ok=True)
    ds = gdal.Open(args.ortho)
    epsg = ds.GetSpatialRef().GetAuthorityCode(None)
    if str(epsg) != str(todo["epsg"]):
        raise SystemExit(f"ortho is EPSG:{epsg} but the run works in EPSG:{todo['epsg']}; reproject the ortho first")
    jpeg, mem = gdal.GetDriverByName("JPEG"), gdal.GetDriverByName("MEM")
    # chips cut earlier stay listed as long as their file is still there (prune deletes the files)
    meta_path = out_dir / "chips.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    meta = {k: v for k, v in meta.items() if Path(v["file"]).exists()}
    outside = 0
    for n, (unit_id, c) in enumerate(todo["chips"].items(), 1):
        px = c.get("px", todo["px"])
        c = {k: v for k, v in c.items() if k != "px"}
        out = out_dir / f"{unit_id}.jpg"
        if not out.exists():
            chip = read_chip(ds, c, px)
            if chip is None:
                outside += 1
                continue
            tmp = mem.Create("", px, px, 3, gdal.GDT_Byte)
            tmp.WriteArray(chip)
            # write under another name first: a chip cut short by an interruption must not look finished
            part = out_dir / f"{unit_id}.part"
            jpeg.CreateCopy(str(part), tmp, options=["QUALITY=88"])
            aux = Path(str(part) + ".aux.xml")
            if aux.exists():
                aux.unlink()
            part.replace(out)
        meta[unit_id] = {"file": str(out), "px": px, **c}
        if n % 200 == 0:
            print(f"chips: {n}/{len(todo['chips'])}", flush=True)
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    print(f"chips listed: {len(meta)} ({len(todo['chips'])} asked for), outside the ortho: {outside}")


if __name__ == "__main__":
    main()
