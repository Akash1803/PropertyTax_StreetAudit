from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from PIL import Image
from shapely.geometry import box

from streetaudit.export import (_shop, _vs_survey, export_geojson, picture_view, trade_only_in_other_views,
                                upper_floors_unseen)


def test_picture_is_the_first_clear_view_of_the_building():
    views = [{"image": "a.jpg"}, {"image": "b.jpg"}, {"image": "c.jpg"}]
    ans = {"views": [{"target_visible": "hidden", "same_building_as_reference": "cannot tell"},
                     {"target_visible": "clear", "same_building_as_reference": "no"},
                     {"target_visible": "clear", "same_building_as_reference": "this is the reference view"}]}
    assert picture_view(views, ans) == 2                     # not the tree (0), not the neighbour (1)
    ans["views"][2]["target_visible"] = "partly hidden"
    assert picture_view(views, ans) == 2                     # falls back to the reference view
    assert picture_view(views, {}) == 0


def test_trade_seen_only_in_a_farther_view_is_flagged():
    def ans(ref_boards, other_boards, usage="Mixed"):
        return {"classification": {"building_usage": usage},
                "views": [{"same_building_as_reference": "this is the reference view", "boards_read": ref_boards},
                          {"same_building_as_reference": "yes", "boards_read": other_boards}]}
    assert trade_only_in_other_views(ans([], ["AEROBICS"])) is True          # the 44WN1679 case
    assert trade_only_in_other_views(ans(["U99"], ["U99"])) is False
    assert trade_only_in_other_views(ans([], [])) is False
    assert trade_only_in_other_views(ans([], ["AEROBICS"], usage="Residential")) is False


def result():
    rows = [{"unit_id": "A", "building_id": "A", "part": 1, "road": "X ST", "verdict": "Verified", "verdict_why": "2 views agree",
             "rec_usage": "Residential", "rec_floors": 1, "model": "m", "geometry": box(0, 0, 10, 10)},
            {"unit_id": "B", "building_id": "B", "part": 1, "road": "X ST", "verdict": "Not visible", "verdict_why": "no line of sight",
             "rec_usage": "Commercial", "rec_floors": 2, "model": None, "geometry": box(20, 0, 30, 10)}]
    return gpd.GeoDataFrame(rows, crs=4326)


AIMED = {"A": {"views": [{"image": "A_v1.jpg", "pano_id": "p1", "pano_date": "2026-02", "dist_near": 8.0,
                          "aim": {"heading": 90.0, "fov": 60.0, "pitch": 10.0}}]}, "B": {"views": []}}
ANSWERS = {"A": [{"classification": {"building_usage": "Mixed", "floors_above_ground": 2, "possible_shop": False,
                                     "trade_evidence": ["ABC Stores"], "floor_usage": [{"floor": 0, "usage": "Commercial"},
                                                                                      {"floor": 1, "usage": "Residential"}],
                                     "units_seen": 2, "confidence": {"floors": 0.8, "usage": 0.9}, "notes": "shop below"}}]}


def test_shop_and_survey_comparison():
    assert _shop("Mixed", "", False) == "yes"
    assert _shop("Residential", "", "yes") == "possible"
    assert _shop("Residential", "", False) == "no"
    assert _shop("Educational Institutions", "Montessori play school", False) == "no"
    assert _vs_survey("Mixed", 2, "Residential", 1) == "type differs and floors differ"
    assert _vs_survey("Residential", 1, "Residential", 1) == "same"
    assert _vs_survey("cannot tell", 1, "Residential", 1) == "-"
    # shops below, upper floor not seen: the survey's Mixed is not contradicted
    assert _vs_survey("Commercial", 1, "Mixed", 1, upper_unseen=True) == "type unclear (upper floors not seen)"
    assert _vs_survey("Commercial", 1, "Mixed", 1) == "type differs"
    assert _vs_survey("Commercial", 1, "Residential", 1, upper_unseen=True) == "type differs"


def test_upper_floors_unseen():
    cls = {"floors_above_ground": 2, "floor_usage": [{"floor": 0, "usage": "Commercial"},
                                                     {"floor": 1, "usage": "Commercial"}]}
    assert upper_floors_unseen(cls) is True                       # floor 2 not described
    cls["floor_usage"].append({"floor": 2, "usage": "Residential"})
    assert upper_floors_unseen(cls) is False
    assert upper_floors_unseen({"floors_above_ground": 0, "floor_usage": []}) is False


def test_export_writes_the_identified_values_and_keeps_verification(tmp_path):
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    Image.new("RGB", (640, 640), "grey").save(evidence / "A_v1.jpg")
    out = tmp_path / "run" / "layer.geojson"
    assert export_geojson(result(), AIMED, ANSWERS, out, evidence) == 2
    g = gpd.read_file(out).set_index("gis_id")
    a = g.loc["A"]
    assert (a["bldg_type"], a["floors"], a["shop_gf"], a["vs_survey"]) == ("Mixed", "G+2", "yes", "type differs and floors differ")
    assert a["floor_use"] == "G: Commercial; G+1: Residential"
    assert a["survey_type"] == "Residential"            # the survey is shown beside, not used as the answer
    assert a["image"] == "images/A.jpg" and (out.parent / "images" / "A.jpg").exists()
    assert max(Image.open(out.parent / "images" / "A.jpg").size) <= 512
    assert "pano=p1" in a["streetview"]
    b = g.loc["B"]
    assert b["check"] == "Not visible" and pd.isna(b["bldg_type"]) and pd.isna(b["image"])

    # a correction from an older street view replaces the reading
    export_geojson(result(), AIMED, ANSWERS, out, evidence,
                   corrections={"A": {"bldg_type": "Residential", "floors": "G+1", "floor_count": 1, "_why": "old view"}})
    fixed = gpd.read_file(out).set_index("gis_id").loc["A"]
    assert fixed["bldg_type"] == "Residential" and fixed["vs_survey"] == "same"   # comparison follows the fix
    with pytest.raises(ValueError):
        export_geojson(result(), AIMED, ANSWERS, out, evidence, corrections={"A": {"no_such_field": 1}})
    export_geojson(result(), AIMED, ANSWERS, out, evidence)

    # the checker marks A, then the layer is exported again
    g = gpd.read_file(out)
    g.loc[g["gis_id"] == "A", ["verified", "verify_note"]] = ["no", "it is G+1"]
    g.to_file(out, driver="GeoJSON")
    export_geojson(result(), AIMED, ANSWERS, out, evidence)
    again = gpd.read_file(out).set_index("gis_id")
    assert again.loc["A", "verified"] == "no" and again.loc["A", "verify_note"] == "it is G+1"
