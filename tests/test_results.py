from pathlib import Path

import geopandas as gpd
from shapely.geometry import Point, box

from streetaudit.config import Settings
from streetaudit.results import latest_result, summarise, write_layers


def row(unit_id, verdict="Verified", flag="Match", **extra):
    base = {"unit_id": unit_id, "verdict": verdict, "verdict_why": "", "flag": flag, "llm_floors": 1, "rec_floors": 1,
            "floor_flag": "Same", "llm_usage": "Residential", "usage_flag": "Same", "poss_shop": False,
            "runs_agree": None, "shutters": 0, "door_match": "", "review": None, "reviewer": None, "review_note": None,
            "geometry": box(0, 0, 10, 10)}
    return base | extra


def panos():
    return gpd.GeoDataFrame({"pano_id": ["p1"], "date": ["2026-02"]}, geometry=[Point(0, -5)], crs=32643)


def test_latest_result_prefers_the_highest_number(tmp_path):
    assert latest_result(tmp_path) is None
    (tmp_path / "result.gpkg").touch()
    assert latest_result(tmp_path).name == "result.gpkg"
    (tmp_path / "result_2.gpkg").touch()
    (tmp_path / "result_10.gpkg").touch()
    (tmp_path / "result_backup.gpkg").touch()          # not one of ours
    assert latest_result(tmp_path).name == "result_10.gpkg"


def test_rewriting_the_result_keeps_the_reviewers_entries(tmp_path):
    s = Settings(buildings=Path("b"), geocodes=Path("g"), run_dir=tmp_path)
    out = write_layers([row("A"), row("B")], [], panos(), s)
    assert out.name == "result.gpkg"
    layer = gpd.read_file(out, layer="buildings_result")
    layer.loc[layer["unit_id"] == "A", ["review", "reviewer", "review_note"]] = ["Right building", "Akash", "ok"]
    layer.to_file(out, layer="buildings_result", driver="GPKG")

    out2 = write_layers([row("A", verdict="Unsure"), row("B")], [], panos(), s)
    assert out2.name == "result.gpkg"                   # not open anywhere, so it is replaced in place
    again = gpd.read_file(out2, layer="buildings_result", ignore_geometry=True).set_index("unit_id")
    assert again.loc["A", "verdict"] == "Unsure"
    assert again.loc["A", "review"] == "Right building" and again.loc["A", "reviewer"] == "Akash"
    assert again.loc["B", "review"] is None or again.loc["B", "review"] != again.loc["B", "review"]
    assert not (tmp_path / "result_2.gpkg").exists()


def test_summarise_counts():
    rows = [row("A"), row("B", verdict="Unsure", flag="Mismatch", floor_flag="LLM higher", verdict_why="only one clear view"),
            row("C", verdict="Not visible", flag="Not checked", llm_usage=None, floor_flag="Not checked", usage_flag="Not checked"),
            row("D", flag="Possible shop - check", poss_shop=True)]
    s = summarise(rows)
    assert s["buildings"] == 4
    assert s["verdict"] == {"Verified": 2, "Unsure": 1, "Not visible": 1}
    assert s["possible_shops"] == 1
    assert s["verified_floors_same_pct"] == 100.0
    assert s["checked_floors_same_pct"] == round(100 * 2 / 3, 1)
