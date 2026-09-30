"""Cut one ortho chip per building. Run with QGIS's Python, because only its GDAL reads ECW:

    "C:\\Program Files\\QGIS 3.40.10\\bin\\python-qgis-ltr.bat" tools\\export_ortho_chips.py <ortho file> <run_dir>

Reads <run_dir>/work/chips_todo.json (written by the `views` stage) and writes JPEG chips plus
<run_dir>/work/chips/chips.json. Chips already on disk are kept, so the script can be rerun.
"""
import json
import sys
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
    ortho, run_dir = sys.argv[1], Path(sys.argv[2])
    todo = json.loads((run_dir / "work" / "chips_todo.json").read_text(encoding="utf-8"))
    out_dir = run_dir / "work" / "chips"
    out_dir.mkdir(parents=True, exist_ok=True)
    ds = gdal.Open(ortho)
    epsg = ds.GetSpatialRef().GetAuthorityCode(None)
    if str(epsg) != str(todo["epsg"]):
        raise SystemExit(f"ortho is EPSG:{epsg} but the run works in EPSG:{todo['epsg']}; reproject the ortho first")
    px = todo["px"]
    jpeg, mem = gdal.GetDriverByName("JPEG"), gdal.GetDriverByName("MEM")
    meta, outside = {}, 0
    for n, (unit_id, c) in enumerate(todo["chips"].items(), 1):
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
    (out_dir / "chips.json").write_text(json.dumps(meta), encoding="utf-8")
    print(f"chips written: {len(meta)}, outside the ortho: {outside}")


if __name__ == "__main__":
    main()
