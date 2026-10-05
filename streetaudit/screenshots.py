"""Pictures taken by a person in the Street View viewer, standing where the layer's link put them.

The no-key route: the checker opens a building's "Open in Google Street View" link, takes a screenshot
and saves it under the building's id (`44WN1079.png`, `44WN1079_2.png` for a second picture,
`44WN1073_p2.png` for part 2 of a multi-part footprint). `import_screenshots` matches the files to
the run's buildings, stores reduced copies as that building's views and writes reading sheets.
Identity rests on the checker, so these readings are judged by `rules.viewer_placed_verdict`.
"""
from __future__ import annotations

import csv
import re
import shutil
import time
from datetime import datetime
from pathlib import Path

import geopandas as gpd
import numpy as np
import shapely
from PIL import Image, ImageDraw

from .config import Settings
from .imaging import CYAN, MAGENTA_RGB, YELLOW, _caption, _font, _line

NAME = re.compile(r"^(?P<unit>\d{2}[A-Z]{2}\d+(?:_p\d+)?|\d+(?:_p\d+)?)(?:[_-](?P<n>\d+))?$", re.I)
MAX_PX = 1600
EXTS = (".png", ".jpg", ".jpeg", ".webp")


def norm_unit(text: str) -> str:
    """'44wn1073_P2' -> '44WN1073_p2': ids are upper case, the part suffix lower case."""
    return re.sub(r"_P(\d+)$", lambda m: "_p" + m.group(1), text.strip().upper())


def match_files(folder: Path, units: set[str], building_parts: dict[str, list[str]]) -> tuple[dict[str, list[Path]], list[Path]]:
    """unit id -> its picture files in order, and the files that match no building.

    A one-part building is named by its building id; a multi-part one needs `_pN`. A building id
    given for a multi-part footprint is taken as part 1.
    """
    found: dict[str, list[tuple[int, Path]]] = {}
    unknown = []
    for f in sorted(folder.iterdir()):
        if f.suffix.lower() not in EXTS:
            continue
        m = NAME.match(f.stem)
        unit = norm_unit(m.group("unit")) if m else None
        if unit and unit not in units and unit in building_parts:
            unit = building_parts[unit][0]
        if not unit or unit not in units:
            unknown.append(f)
            continue
        found.setdefault(unit, []).append((int(m.group("n") or 1), f))
    return {u: [p for _, p in sorted(v)] for u, v in found.items()}, unknown


CLICK_GAP_MIN, CLICK_GAP_MAX = 2.0, 240.0      # seconds between the click in QGIS and the screenshot


def load_clicks(log: Path) -> list[tuple[float, str]]:
    """The click log the layer action writes: one `unit id,ISO time` line per link opened, in time order."""
    if not log.exists():
        return []
    out = []
    for row in csv.reader(log.read_text(encoding="utf-8").splitlines()):
        if len(row) >= 2 and row[0].strip():
            try:
                out.append((datetime.fromisoformat(row[1].strip()).timestamp(), norm_unit(row[0])))
            except ValueError:
                continue
    return sorted(out)


def match_by_clicks(files: list[Path], clicks: list[tuple[float, str]], units: set[str],
                    building_parts: dict[str, list[str]]) -> tuple[dict[str, list[Path]], list[tuple[Path, str]]]:
    """Files named by a screenshot tool (date/time/counter) go to the building clicked last before them.

    A file is accepted when the last click lies 2 s to 4 min before it and no other click came in
    between; otherwise it is reported with the reason. Several files after one click become pictures 1, 2, ...
    """
    found: dict[str, list[Path]] = {}
    rejected = []
    times = [c[0] for c in clicks]
    for f in sorted(files, key=lambda x: x.stat().st_mtime):
        taken = f.stat().st_mtime
        i = int(np.searchsorted(times, taken, side="right")) - 1
        if i < 0:
            rejected.append((f, "taken before the first click in the log"))
            continue
        gap = taken - times[i]
        if gap < CLICK_GAP_MIN:
            rejected.append((f, f"taken {gap:.0f} s after the click, too soon to be that building"))
            continue
        if gap > CLICK_GAP_MAX:
            rejected.append((f, f"taken {gap / 60:.1f} min after the last click ({clicks[i][1]}); too long"))
            continue
        unit = clicks[i][1]
        if unit not in units and unit in building_parts:
            unit = building_parts[unit][0]
        if unit not in units:
            rejected.append((f, f"click log names unknown building {clicks[i][1]}"))
            continue
        found.setdefault(unit, []).append(f)
    return found, rejected


def crop_chrome(img: Image.Image) -> Image.Image:
    """Cut the browser's title/tab bars and the taskbar off a full-screen capture.

    Rows of window chrome are nearly uniform; photographic rows are not. Only the top and bottom are
    cut: Google's panels overlay the picture itself and are left for the reader.
    """
    a = np.asarray(img.convert("RGB"), dtype=np.float32)
    row_std = a.std(axis=(1, 2))
    busy = np.flatnonzero(row_std > 18)
    if len(busy) < img.size[1] * 0.3:
        return img
    top, bottom = int(busy[0]), int(busy[-1]) + 1
    if bottom - top < img.size[1] * 0.4:
        return img
    return img.crop((0, top, img.size[0], bottom))


def store(src: Path, dst: Path) -> dict:
    """A copy reduced to MAX_PX on the long side (a 4K screenshot is 3-8 MB; 1600 px reads fine)."""
    img = crop_chrome(Image.open(src).convert("RGB"))
    img.thumbnail((MAX_PX, MAX_PX))
    dst.parent.mkdir(parents=True, exist_ok=True)
    img.save(dst, quality=88)
    return {"width": img.size[0], "height": img.size[1],
            "taken": time.strftime("%Y-%m-%d %H:%M", time.localtime(src.stat().st_mtime))}


def as_views(planned: dict, files: list[Path], s: Settings, by: str) -> list[dict]:
    """The screenshots as the building's views, carrying the planned camera they were taken from.

    The aim gets `source: screenshot`, so the request key differs from the API pictures' and old
    readings do not count for them.
    """
    first = (planned.get("views") or [None])[0] or {}
    views = []
    for n, f in enumerate(files, 1):
        name = f"{planned['unit_id']}_s{n}.jpg"
        meta = store(f, s.evidence_dir / name)
        views.append({**{k: first.get(k) for k in ("px", "py", "left", "right", "bearing_left", "bearing_right",
                                                      "dist_near", "dist_left", "dist_right", "span_deg", "pano_id",
                                                      "pano_date")},
                      "aim": {**(first.get("aim") or {}), "source": "screenshot", "n": n},
                      "image": name, "screenshot": True, "taken_by": by, **meta,
                      "original": f.name})
    return views


def sheet(s: Settings, unit_id: str, views: list[dict], chip: dict | None, units: gpd.GeoDataFrame,
          out_dir: Path) -> Path | None:
    """Screenshots (large, stacked) beside the ortho chip: target in yellow, planned camera in magenta."""
    shots = []
    for v in views:
        im = Image.open(s.evidence_dir / v["image"]).convert("RGB")
        im.thumbnail((1250, 900))
        d = ImageDraw.Draw(im)
        _caption(d, f"SCREENSHOT {v['aim']['n']}  by {v.get('taken_by', '?')}  {v.get('taken', '')}", 520)
        shots.append(im)
    ortho = None
    if chip and Path(chip["file"]).exists():
        img = Image.open(chip["file"]).convert("RGB")
        d = ImageDraw.Draw(img)
        scale = img.size[0] / (chip["xmax"] - chip["xmin"])

        def px(x, y):
            return (x - chip["xmin"]) * scale, (chip["ymax"] - y) * scale

        box = shapely.box(chip["xmin"], chip["ymin"], chip["xmax"], chip["ymax"])
        for row in units[units.intersects(box)].itertuples():
            for poly in getattr(row.geometry, "geoms", [row.geometry]):
                pts = [px(x, y) for x, y in poly.exterior.coords]
                if row.unit_id == unit_id:
                    _line(d, pts, YELLOW, 4)
                else:
                    d.line(pts, fill=(255, 255, 255), width=1)
        v = views[0]
        if v.get("px") is not None:
            c = px(v["px"], v["py"])
            for side in ("left", "right"):
                if v.get(side):
                    d.line([c, px(*v[side])], fill=CYAN, width=2)
            d.ellipse([c[0] - 10, c[1] - 10, c[0] + 10, c[1] + 10], fill=MAGENTA_RGB, outline=(0, 0, 0), width=2)
        _caption(d, "ORTHO north up | yellow = target | magenta = spot the link opened", 520)
        img.thumbnail((700, 700))
        ortho = img
    if not shots and ortho is None:
        return None
    left_w = max((im.size[0] for im in shots), default=0)
    width = left_w + (ortho.size[0] if ortho else 0)
    height = max(sum(im.size[1] for im in shots), ortho.size[1] if ortho else 0) + 36
    page = Image.new("RGB", (width, height), "white")
    d = ImageDraw.Draw(page)
    d.rectangle([0, 0, width, 36], fill=(0, 0, 0))
    d.text((8, 6), f"{unit_id}  {len(views)} screenshot(s)", fill=YELLOW, font=_font(20))
    y = 36
    for im in shots:
        page.paste(im, (0, y))
        y += im.size[1]
    if ortho:
        page.paste(ortho, (left_w, 36))
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{unit_id}.jpg"
    page.save(out, quality=84)
    return out


def missing_list(plan: dict, done: set[str]) -> list[str]:
    """Street-facing buildings that still have no screenshot, in id order."""
    return sorted(u for u, info in plan.items() if info.get("access") == "street-facing" and u not in done)


def clean_folder_copy(src: Path, keep: Path) -> None:
    """Keep the checker's original files together in the run folder (they stay internal)."""
    keep.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, keep / src.name)
