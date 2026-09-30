"""Camera geometry: bearings, where a map point lands in a street image, how to aim at a building.

All map coordinates are in a metric projected CRS (x east, y north). Bearings are compass degrees.
"""
from __future__ import annotations

import math


def bearing(p, q) -> float:
    """Compass bearing from p to q."""
    return math.degrees(math.atan2(q[0] - p[0], q[1] - p[1])) % 360


def rel(angle: float, ref: float) -> float:
    """Angle relative to a reference, in -180..180; positive is clockwise (to the right)."""
    return (angle - ref + 180) % 360 - 180


def mid_bearing(left: float, right: float) -> float:
    """Bearing halfway between a left and a right bearing, going clockwise from left to right."""
    return (left + (rel(right, left) % 360) / 2) % 360


def project(delta_deg: float, dist: float, height: float, fov: float, pitch: float, size: int):
    """Pixel (x, y) of a point in a perspective image, or None when it is behind the camera.

    delta_deg: bearing of the point minus the camera heading; dist: horizontal distance in metres;
    height: metres above the camera; fov: horizontal field of view; pitch: camera tilt up, degrees.
    """
    d, p = math.radians(delta_deg), math.radians(pitch)
    x, y = dist * math.sin(d), dist * math.cos(d)
    depth = y * math.cos(p) + height * math.sin(p)
    up = -y * math.sin(p) + height * math.cos(p)
    if depth <= 0.05:
        return None
    f = (size / 2) / math.tan(math.radians(fov) / 2)
    return size / 2 + f * x / depth, size / 2 - f * up / depth


def aim(view: dict, storeys_hint: int, camera_height: float) -> dict:
    """Heading, field of view and pitch that put the visible part of the building in the frame.

    The height is a generous guess (at least 12 m) so the top is not cut off; it is not a measurement.
    """
    height = max(12.0, (storeys_hint + 1) * 3.2)
    d = max(view["dist_near"], 1.0)
    top = math.degrees(math.atan((height - camera_height) / d))
    bottom = math.degrees(math.atan(-camera_height / d))
    fov = min(120.0, max(40.0, view["span_deg"] + 16, (top - bottom) + 6))
    pitch = (top + bottom) / 2
    if (top - bottom) + 6 > 120:           # cannot fit: keep the top, lose some ground
        pitch = top - fov / 2 + 3
    heading = mid_bearing(view["bearing_left"], view["bearing_right"])
    return {"heading": round(heading, 2), "fov": round(fov, 1), "pitch": round(pitch, 1), "height": height}


def aim_zoom(view: dict) -> dict:
    """A closer look at the ground floor (gate, door numbers, name boards) from the same panorama."""
    fov = min(45.0, max(25.0, view["span_deg"] + 6))
    return {"heading": round(mid_bearing(view["bearing_left"], view["bearing_right"]), 2),
            "fov": round(fov, 1), "pitch": 0.0, "height": 0.0}


def core_span(view: dict, shift_m: float):
    """Bearings (left, right) that stay on the building if the camera is off by +-shift_m sideways.

    Returns None when nothing stays: the frontage is too narrow for the position error.
    """
    px, py = view["px"], view["py"]
    mid = math.radians(mid_bearing(view["bearing_left"], view["bearing_right"]))
    tx, ty = math.cos(mid), -math.sin(mid)   # unit vector at right angles to the view direction
    ref = view["bearing_left"]
    lefts, rights = [], []
    for s in (-shift_m, 0.0, shift_m):
        cam = (px + s * tx, py + s * ty)
        lefts.append(rel(bearing(cam, view["left"]), ref))
        rights.append(rel(bearing(cam, view["right"]), ref))
    left, right = max(lefts), min(rights)
    if right <= left:
        return None
    return (ref + left) % 360, (ref + right) % 360
