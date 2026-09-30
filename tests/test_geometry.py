import math

import pytest

from streetaudit.geometry import aim, aim_zoom, bearing, core_span, mid_bearing, project, rel


def test_bearing_compass_directions():
    assert bearing((0, 0), (0, 10)) == pytest.approx(0)       # north
    assert bearing((0, 0), (10, 0)) == pytest.approx(90)      # east
    assert bearing((0, 0), (0, -10)) == pytest.approx(180)
    assert bearing((0, 0), (-10, 0)) == pytest.approx(270)


def test_rel_wraps_around_north():
    assert rel(10, 350) == pytest.approx(20)
    assert rel(350, 10) == pytest.approx(-20)


def test_mid_bearing_across_north():
    assert mid_bearing(350, 30) == pytest.approx(10)
    assert mid_bearing(100, 140) == pytest.approx(120)


def test_project_centre_and_edges():
    size, fov = 640, 90
    # straight ahead at camera height lands in the middle
    assert project(0, 10, 0, fov, 0, size) == pytest.approx((320, 320))
    # a point at half the field of view lands on the right edge
    x, y = project(45, 10, 0, fov, 0, size)
    assert x == pytest.approx(640) and y == pytest.approx(320)
    # higher points are higher in the image (smaller y)
    assert project(0, 10, 3, fov, 0, size)[1] < 320
    # behind the camera: no pixel
    assert project(180, 10, 0, fov, 0, size) is None


def test_project_with_pitch_centres_the_tilt_direction():
    # camera tilted up 20 degrees: a point 20 degrees above the horizon is in the middle
    height = 10 * math.tan(math.radians(20))
    assert project(0, 10, height, 60, 20, 640) == pytest.approx((320, 320))


def _view(dist=10.0, half_width=4.0):
    # camera at the origin looking north at a wall from (-half_width, dist) to (half_width, dist)
    left, right = (-half_width, dist), (half_width, dist)
    span = 2 * math.degrees(math.atan(half_width / dist))
    return {"px": 0.0, "py": 0.0, "left": list(left), "right": list(right),
            "bearing_left": bearing((0, 0), left), "bearing_right": bearing((0, 0), right),
            "span_deg": span, "dist_near": dist, "dist_left": math.hypot(*left), "dist_right": math.hypot(*right)}


def test_aim_points_at_the_middle_and_fits_the_span():
    a = aim(_view(), storeys_hint=2, camera_height=2.5)
    assert a["heading"] == pytest.approx(0, abs=0.01)
    assert a["fov"] >= _view()["span_deg"] + 16 - 0.1
    assert 40 <= a["fov"] <= 120
    assert a["pitch"] > 0                       # tilted up to take in the upper floors


def test_aim_zoom_is_narrow_and_level():
    z = aim_zoom(_view())
    assert 25 <= z["fov"] <= 45 and z["pitch"] == 0


def test_core_span_shrinks_with_position_error():
    view = _view(dist=10, half_width=4)
    left, right = core_span(view, 2.0)
    assert rel(left, view["bearing_left"]) > 0          # moved in from the left end
    assert rel(right, view["bearing_right"]) < 0        # and from the right end
    assert rel(right, left) > 0


def test_core_span_empty_for_a_narrow_frontage():
    # a 3 m frontage cannot survive a 2 m position error either way
    assert core_span(_view(dist=10, half_width=1.5), 2.0) is None
