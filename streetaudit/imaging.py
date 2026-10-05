"""Fetching the street views and drawing the target on them and on the ortho chip."""
from __future__ import annotations

import io
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import geopandas as gpd
import shapely
from PIL import Image, ImageDraw, ImageFont

from .config import Settings
from .geometry import aim, aim_zoom, core_span, project, rel

YELLOW, GREEN, CYAN, MAGENTA_RGB = (255, 235, 0), (0, 230, 90), (0, 255, 255), (255, 60, 255)


def _font(size: int):
    for name in ("arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _line(draw, pts, colour, width):
    draw.line(pts, fill=(0, 0, 0), width=width + 3)
    draw.line(pts, fill=colour, width=width)


def _caption(draw, text, width):
    draw.rectangle([0, 0, width, 26], fill=(0, 0, 0))
    draw.text((6, 3), text, fill=YELLOW, font=_font(18))


def mark_street(img: Image.Image, view: dict, a: dict, caption: str, s: Settings) -> Image.Image:
    """Two yellow lines at the ends of the visible part of the target; a green bar over its core span."""
    draw = ImageDraw.Draw(img)
    top = a["height"] - s.camera_height_m
    for side in ("left", "right"):
        delta = rel(view[f"bearing_{side}"], a["heading"])
        pts = [project(delta, view[f"dist_{side}"], z, a["fov"], a["pitch"], s.image_px)
               for z in (-s.camera_height_m, 0.0, top / 2, top)]
        pts = [p for p in pts if p is not None]
        if len(pts) >= 2:
            _line(draw, pts, YELLOW, 4)
    core = view.get("core_span")
    if core:
        mid_dist = (view["dist_left"] + view["dist_right"]) / 2
        ends = [project(rel(b, a["heading"]), mid_dist, top, a["fov"], a["pitch"], s.image_px) for b in core]
        if all(ends):
            y = max(34, min(p[1] for p in ends) - 10)
            _line(draw, [(ends[0][0], y), (ends[1][0], y)], GREEN, 7)
    _caption(draw, caption, 300)
    return img


def mark_ortho(chip: dict, unit_id: str, views: list[dict], units_near: gpd.GeoDataFrame) -> Image.Image:
    """Ortho chip with the target in yellow, other footprints in thin white and the cameras in cyan."""
    img = Image.open(chip["file"]).convert("RGB")
    draw = ImageDraw.Draw(img)
    scale = chip["px"] / (chip["xmax"] - chip["xmin"])

    def px(x, y):
        return (x - chip["xmin"]) * scale, (chip["ymax"] - y) * scale

    for row in units_near.itertuples():
        pts = [px(x, y) for x, y in row.geometry.exterior.coords]
        if row.unit_id == unit_id:
            _line(draw, pts, YELLOW, 4)
        else:
            draw.line(pts, fill=(255, 255, 255), width=1)
    font = _font(15)
    for n, v in enumerate(views, 1):
        c = px(v["px"], v["py"])
        for side in ("left", "right"):
            draw.line([c, px(*v[side])], fill=CYAN, width=2)
        draw.ellipse([c[0] - 9, c[1] - 9, c[0] + 9, c[1] + 9], fill=CYAN, outline=(0, 0, 0), width=2)
        draw.text((c[0] - 4, c[1] - 9), str(n), fill=(0, 0, 0), font=font)
    _caption(draw, "ORTHO  north up  yellow = target", 360)
    return img


def zoom_view(views: list[dict], s: Settings) -> int | None:
    """Index of the view to zoom from: the closest one that is not too close."""
    ok = [i for i, v in enumerate(views) if v["dist_near"] >= 4]
    return min(ok or range(len(views)), key=lambda i: views[i]["dist_near"], default=None)


def build_evidence(plan: dict[str, dict], units: gpd.GeoDataFrame, chips: dict[str, dict], source, s: Settings,
                   previous: dict[str, dict] | None = None, workers: int = 6, progress=None) -> dict[str, dict]:
    """Fetch and mark every planned view, one zoomed view and the ortho chip. Returns the plan with file names.

    `previous` is what an earlier call returned. An image on disk is reused only when it was fetched for
    the same panorama with the same aim; after the views are planned again, changed views are fetched again.
    """
    s.evidence_dir.mkdir(parents=True, exist_ok=True)
    previous = previous or {}
    tree = shapely.STRtree(units.geometry.values)

    def one(unit_id: str) -> tuple[str, dict]:
        info = json.loads(json.dumps(plan[unit_id]))
        before = previous.get(unit_id) or {}
        old_views = before.get("views") or []
        storeys = info["record"]["storeys"] or 0
        for n, v in enumerate(info["views"], 1):
            v["aim"] = aim(v, storeys, s.camera_height_m)
            v["core_span"] = core_span(v, s.position_error_m)
            old = old_views[n - 1] if n - 1 < len(old_views) else {}
            reuse = bool(old.get("image")) and old.get("pano_id") == v["pano_id"] and old.get("aim") == v["aim"]
            v["image"] = _fetch(source, v, v["aim"], s.evidence_dir / f"{unit_id}_v{n}.jpg", s, reuse,
                                lambda img, v=v, n=n: mark_street(img, v, v["aim"], f"VIEW {n}   {v['pano_date']}", s))
        z = zoom_view(info["views"], s)
        info["zoom"] = None
        if z is not None:
            v = info["views"][z]
            za = aim_zoom(v)
            old = before.get("zoom") or {}
            reuse = bool(old.get("image")) and old.get("pano_id") == v["pano_id"] and old.get("aim") == za

            def label(img, z=z):
                _caption(ImageDraw.Draw(img), f"ZOOM of view {z + 1}, ground floor", 330)
                return img
            name = _fetch(source, v, za, s.evidence_dir / f"{unit_id}_zoom.jpg", s, reuse, label)
            if name:
                info["zoom"] = {"of_view": z + 1, "pano_id": v["pano_id"], "aim": za, "image": name}
        info["ortho_image"] = None
        chip = chips.get(unit_id)
        if chip and info["views"] and Path(chip["file"]).exists():      # prune deletes chip files
            out = s.evidence_dir / f"{unit_id}_ortho.jpg"
            box = shapely.box(chip["xmin"], chip["ymin"], chip["xmax"], chip["ymax"])
            near = units.iloc[tree.query(box, predicate="intersects")]
            mark_ortho(chip, unit_id, info["views"], near).save(out, quality=88)     # local and cheap: always redrawn
            info["ortho_image"] = out.name
        return unit_id, info

    done = {}
    todo = [u for u, info in plan.items() if info["views"]]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for n, (unit_id, info) in enumerate(pool.map(one, todo), 1):
            done[unit_id] = info
            if progress and n % 100 == 0:
                progress(f"images ready for {n}/{len(todo)} buildings")
    for unit_id, info in plan.items():
        done.setdefault(unit_id, {**info, "zoom": None, "ortho_image": None})
    return done


def _fetch(source, view: dict, a: dict, out: Path, s: Settings, reuse: bool, decorate) -> str | None:
    """Fetch and decorate one image, or keep the file on disk when `reuse` says it is the same view.

    Returns the file name, or None when the fetch failed.
    """
    if reuse and out.exists():
        return out.name
    data = source.image(view["pano_id"], a["heading"], a["fov"], a["pitch"], s.image_px)
    if data is None:
        return None
    decorate(Image.open(io.BytesIO(data)).convert("RGB")).save(out, quality=90)
    return out.name
