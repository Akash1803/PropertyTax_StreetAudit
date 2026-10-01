import geopandas as gpd
import numpy as np
import shapely
from shapely.geometry import LineString, Point, box

from streetaudit.streets import (assign_units, building_summary, choose_panorama, front_gaps, main_zone,
                                 make_stretches, name_key, norm_zone, picture_points, similar_keys, split_line,
                                 stretch_id, suggest_name, zone_fit)


def roads(*lines, names=None):
    names = names or [None] * len(lines)
    return gpd.GeoDataFrame({"road_fid": range(len(lines)), "road_name": names},
                            geometry=[LineString(l) for l in lines], crs=32643)


def test_a_crossing_splits_both_lines_and_a_t_junction_splits_the_main_road():
    r = roads([(0, 0), (100, 0)], [(50, -40), (50, 40)], [(80, 0.6), (80, 30)])   # the last one stops 0.6 m short
    st = make_stretches(r, tol=1.0, min_len=10)
    assert sorted(st["length_m"]) == [20.0, 29.4, 30.0, 40.0, 40.0, 50.0]
    main = st[st["road_fid"] == 0]
    assert sorted(main["length_m"]) == [20.0, 30.0, 50.0]
    assert len(st[st["road_fid"] == 1]) == 2


def test_short_pieces_and_lines_drawn_twice_are_dropped():
    r = roads([(0, 0), (100, 0)], [(0, 0), (100, 0)], [(0, 0), (0.8, 0.5)], [(95, 0), (95, 5)])
    st = make_stretches(r, tol=1.0, min_len=10)
    # the duplicate is dropped, the stray 1 m line is dropped, the 5 m stub is dropped but still cuts the road
    assert sorted(st["length_m"]) == [95.0]


def test_stretch_id_ignores_direction_and_survives_small_float_noise():
    a = LineString([(0, 0), (40, 0), (80, 10)])
    b = LineString([(80.2, 10.1), (40, 0), (0.1, -0.2)])
    assert stretch_id(a) == stretch_id(b)
    assert stretch_id(a) != stretch_id(LineString([(0, 0), (80, 10)]))


def test_split_line_ignores_cuts_at_the_ends():
    line = LineString([(0, 0), (100, 0)])
    assert [round(p.length) for p in split_line(line, [0.0, 0.5, 50, 50.4, 99.8])] == [50, 50]


def test_buildings_go_to_the_road_they_are_photographed_from():
    st = make_stretches(roads([(0, 0), (100, 0)], [(100, 0), (100, 100)]), 1.0, 10)
    corner = box(90, 5, 98, 15)                     # a corner house, photographed from the north-south road
    back = box(40, 25, 50, 35)                      # no street view; 25 m from the east-west road
    far = box(40, 80, 50, 90)                       # no street view and no road within 30 m
    units = gpd.GeoDataFrame({"unit_id": ["corner", "back", "far"]}, geometry=[corner, back, far], crs=32643)
    plan = {"corner": {"access": "street-facing", "views": [{"px": 101, "py": 10, "dist_near": 3},
                                                             {"px": 94, "py": 1, "dist_near": 4}]},
            "back": {"access": "not visible", "views": []}, "far": {"access": "not visible", "views": []}}
    got = assign_units(units, plan, st, cam_m=15, backlot_m=30)
    ns = st.set_index("stretch_id")
    assert ns.loc[got["corner"]["stretch_id"], "road_fid"] == 1 and got["corner"]["role"] == "seen"
    assert ns.loc[got["back"]["stretch_id"], "road_fid"] == 0 and got["back"]["role"] == "cannot see"
    assert "far" not in got


def test_a_corner_house_photographed_from_two_roads_goes_to_the_road_of_its_address():
    st = make_stretches(roads([(0, 0), (100, 0)], [(100, 0), (100, 100)]), 1.0, 10)
    corner = box(90, 5, 98, 15)
    units = gpd.GeoDataFrame({"unit_id": ["c"], "road": ["N.S.R. ROAD"]}, geometry=[corner], crs=32643)
    plan = {"c": {"access": "street-facing", "views": [{"px": 101, "py": 10, "dist_near": 3},   # side road, closest
                                                        {"px": 94, "py": 1, "dist_near": 4}]}}  # main road
    ids = dict(zip(st["road_fid"], st["stretch_id"]))
    assert assign_units(units, plan, st, 15, 30)["c"]["stretch_id"] == ids[1]
    keys = {ids[0]: {name_key("NSR Road")}, ids[1]: {name_key("Chinnammal Street")}}
    assert assign_units(units, plan, st, 15, 30, keys)["c"]["stretch_id"] == ids[0]


def test_street_names_from_survey_spellings():
    assert name_key("Chinnamal street") == name_key("CHINNAMMAL STREET") == name_key("Chinnammal St")
    assert name_key("Chindhamani Nagar") == name_key("chinthamani nagar")
    assert similar_keys(name_key("Alaganna street"), name_key("Alagannan Street"))
    assert not similar_keys(name_key("NSR Road"), name_key("Main_road"))
    assert name_key("Chinthamanistreet 3") == name_key("CHINTHAMANI ST-3")
    assert not similar_keys(name_key("Chinthamani St-3"), name_key("Chinthamani St-4"))
    assert suggest_name(["CHINNAMMAL STREET", "Chinnamal street", "Chinnammal Street", "Chinnammal Street", "NSR ROAD"]) \
        == "Chinnammal Street"
    assert suggest_name([None, ""]) is None


def test_front_gap_between_buildings_on_both_sides():
    line = LineString([(0, 0), (100, 0)])
    fps = np.array([box(0, 4, 100, 14), box(0, -20, 50, -5)], dtype=object)    # south side built only on the west half
    g = front_gaps(line, fps, shapely.STRtree(fps), step=10, reach=25)
    assert g["gap_samples"] == 10 and g["gap_built_both"] == 5
    assert g["gap_med_m"] == 9.0 and g["gap_min_m"] == 9.0


def test_picture_points_are_spread_evenly_with_the_road_direction():
    pts = picture_points(LineString([(0, 0), (0, 230)]), 50)
    assert [p["along_m"] for p in pts] == [23.0, 69.0, 115.0, 161.0, 207.0]
    assert all(p["bearing"] == 0.0 for p in pts)                                 # northwards
    assert len(picture_points(LineString([(0, 0), (12, 0)]), 50)) == 1


def test_panorama_must_stand_on_this_road_and_the_newest_wins():
    st = make_stretches(roads([(0, 0), (100, 0)], [(50, 0), (50, 100)]), 1.0, 10)
    panos = gpd.GeoDataFrame({"pano_id": ["old", "new", "cross"], "date": ["2022-12", "2026-02", "2026-02"]},
                             geometry=[Point(20, 1), Point(26, -1), Point(51, 8)], crs=32643)
    tree, ptree = shapely.STRtree(st.geometry.values), shapely.STRtree(panos.geometry.values)
    i = int(np.flatnonzero((st["road_fid"] == 0) & (st.geometry.centroid.x < 50))[0])
    got = choose_panorama({"x": 22, "y": 0, "bearing": 90.0}, i, tree, panos, ptree, 10, set())
    assert got["pano_id"] == "new" and got["heading"] == 90.0
    assert choose_panorama({"x": 22, "y": 0, "bearing": 90.0}, i, tree, panos, ptree, 10, {"new"})["pano_id"] == "old"
    # near the junction, the camera on the crossing road is not used for the east-west road
    j = int(np.flatnonzero((st["road_fid"] == 0) & (st.geometry.centroid.x > 50))[0])
    assert choose_panorama({"x": 52, "y": 0, "bearing": 90.0}, j, tree, panos, ptree, 10, set()) is None


def test_zones():
    assert [norm_zone(z) for z in ("ZONE-B", "zone a", "C", "#N/A", "0", None)] == ["B", "A", "C", None, None, None]
    assert main_zone(["B", "B", "A", None]) == ("B", 0.67)
    assert main_zone([None]) == (None, None)


def row(check="Verified", t="Residential", vs="same", shop="no", role="seen", gid="X"):
    return {"check": check, "bldg_type": t, "vs_survey": vs, "shop_gf": shop, "role": role, "gis_id": gid}


def test_building_summary_counts_only_checked_buildings_for_the_survey_gap():
    rows = [row(), row(t="Commercial", vs="type differs", shop="yes", gid="A"),
            row(check="Unsure", t="Mixed", vs="type differs and floors differ", shop="yes", gid="B"),
            row(check="Pending", t=None, vs="-"), row(check="Not visible", t=None, vs="-", role="cannot see"),
            row(check="Unsure", t="cannot tell", vs="-")]
    s = building_summary(rows)
    assert (s["bldgs"], s["seen"], s["cant_see"], s["read"], s["pending"]) == (6, 5, 1, 3, 1)
    assert (s["differ"], s["differ_type"], s["differ_floors"], s["differ_pct"]) == (2, 2, 1, 67)
    assert s["differ_ids"] == "A, B" and s["trade_pct"] == 67 and s["shops"] == 2
    # shops below and upper floors not seen: not counted as a difference with the survey's Mixed
    s = building_summary([row(t="Commercial", vs="type unclear (upper floors not seen)", gid="C"),
                          row(t="Commercial", vs="type unclear (upper floors not seen) and floors differ", gid="D")])
    assert (s["differ"], s["differ_type"], s["differ_floors"], s["type_unclear"]) == (1, 0, 1, 2)


def test_street_readings_are_checked_against_the_pictures():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "wsr", Path(__file__).resolve().parent.parent / "tools" / "write_street_readings.py")
    wsr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wsr)
    pics = [{"n": 1, "image": "a.jpg"}, {"n": 2, "image": None}, {"n": 3, "image": "c.jpg"}]
    note = {"s": "S1", "p": [["c", "shops"], ["h", "bus"]], "type": "street", "w": "2 lanes", "shops": "few"}
    r = wsr.expand(note, pics)
    assert [p["n"] for p in r["pictures"]] == [1, 3] and r["pictures"][1]["usable"] == "hidden"
    assert r["road_type"] == "street" and r["surface"] == "cannot tell"
    import pytest
    with pytest.raises(ValueError):
        wsr.expand({**note, "p": [["c", ""]]}, pics)                 # one picture short
    with pytest.raises(ValueError):
        wsr.expand({**note, "w": "4 lanes"}, pics)                   # not an allowed answer


def test_zone_fit_rules():
    main = {"road_type": "main road", "width": "wide", "shops": "mostly"}
    lane = {"road_type": "lane", "width": "1 lane", "shops": "none"}
    assert zone_fit("B", main, 80, 10) == "zone may be low"
    assert zone_fit("A", main, 80, 10) == "fits"
    assert zone_fit("A", lane, 0, 6) == "zone may be high"
    assert zone_fit("C", lane, 0, 6) == "fits"
    assert zone_fit("B", {"road_type": "street", "width": "2 lanes", "shops": "few"}, 60, 8) == "zone may be low"
    assert zone_fit("B", {"road_type": "street", "width": "2 lanes", "shops": "few"}, 60, 3) == "fits"
    assert zone_fit("A", None, 0, 0) == "not read"
    assert zone_fit(None, lane, 0, 0) == "no zone"
