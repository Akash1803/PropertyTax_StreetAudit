from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
import shapely
from shapely.geometry import LineString, Point, box

from streetaudit.config import Settings
from streetaudit.data import select_units
from streetaudit.visibility import access_class, candidate_views, choose_views, see, usable_panoramas

S = Settings(buildings=Path("b"), geocodes=Path("g"), run_dir=Path("r"))

# a street running east-west along y = 0..-6; three houses in a row on its north side, one behind them
A = box(0, 2, 10, 12)
B = box(12, 2, 22, 12)
C = box(24, 2, 34, 12)
BACK = box(12, 14, 22, 24)
TREE = shapely.STRtree(np.array([A, B, C, BACK], dtype=object))


def test_front_wall_is_visible_from_the_street():
    v = see(B, (17, -3), TREE, S)
    assert v is not None
    assert v["dist_near"] == pytest.approx(5, abs=0.3)
    assert 8 <= v["apparent_m"] <= 11                 # about the 10 m frontage
    # the visible ends are the two front corners
    assert sorted([round(v["left"][0]), round(v["right"][0])]) == [12, 22]
    assert v["bearing_left"] > 270 or v["bearing_left"] < 90


def test_house_behind_the_row_is_not_visible_from_in_front():
    assert see(BACK, (17, -3), TREE, S) is None


def test_far_side_of_a_building_is_hidden_by_the_building_itself():
    v = see(B, (17, -3), TREE, S)
    assert v["visible_m"] < B.exterior.length / 2


def _panos(points):
    return gpd.GeoDataFrame({"pano_id": [f"p{i}" for i in range(len(points))], "date": "2026-02"},
                            geometry=[Point(p) for p in points], crs=32643)


def test_candidate_views_drop_end_on_and_too_close_views():
    cands = candidate_views(B, _panos([(17, -3), (17, 1.5), (60, 4)]), TREE, S)
    assert [c["pano_id"] for c in cands] == ["p0"]      # p1 is 0.5 m away, p2 sees nothing of B


def test_panorama_inside_a_footprint_is_moved_out_or_dropped():
    panos = _panos([(17, -3), (17, 3), (17, 7)])       # on the street, 1 m inside B, 5 m inside B
    panos["own"] = True
    blockers = np.array([A, B, C, BACK], dtype=object)
    usable = usable_panoramas(panos, blockers, S)
    assert list(usable["pano_id"]) == ["p0", "p1"]      # the deep one is dropped
    moved = usable[usable["pano_id"] == "p1"].iloc[0]
    assert moved["snapped"] and not B.contains(moved.geometry)
    assert moved.geometry.y == pytest.approx(1.5)       # just outside B's front wall at y = 2
    assert not usable[usable["pano_id"] == "p0"].iloc[0]["snapped"]


def test_user_photos_are_not_used():
    panos = _panos([(17, -3), (20, -3)])
    panos["own"] = [True, False]
    assert list(usable_panoramas(panos, np.array([B], dtype=object), S)["pano_id"]) == ["p0"]


def test_choose_views_adds_a_view_only_for_new_wall():
    corner = box(0, 2, 10, 12)                          # a corner plot: street to the south and to the west
    tree = shapely.STRtree(np.array([corner], dtype=object))
    cands = candidate_views(corner, _panos([(5, -4), (11, -4), (-6, 7)]), tree, S)
    chosen = choose_views(cands, S)
    ids = [c["pano_id"] for c in chosen]
    assert "p0" in ids and "p2" in ids                  # south face and west face both get a view
    assert all("_good" not in c for c in chosen)


def test_access_class():
    assert access_class([], S) == "not visible"
    assert access_class([{"dist_near": 22.0}], S) == "not street-facing"
    assert access_class([{"dist_near": 22.0}, {"dist_near": 8.0}], S) == "street-facing"


def test_select_units_by_id_and_by_street_line():
    units = gpd.GeoDataFrame({"unit_id": ["A", "B_p1", "B_p2", "C"], "building_id": ["A", "B", "B", "C"]},
                             geometry=[A, B, BACK, C], crs=32643)
    assert list(select_units(units, ids=["B"])["unit_id"]) == ["B_p1", "B_p2"]
    street = LineString([(0, -3), (40, -3)])
    assert list(select_units(units, line=street, buffer_m=6)["unit_id"]) == ["A", "B_p1", "C"]
    # only the first 5 m of the street
    assert list(select_units(units, line=street, buffer_m=6, max_length_m=5)["unit_id"]) == ["A"]
