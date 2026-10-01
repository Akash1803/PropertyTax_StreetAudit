import json
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import Point, box

from streetaudit.config import Settings
from streetaudit.results import REVIEW_FIELDS, load_result, review_scores, save_result
from streetaudit.review import _clean, import_answers, review_data


def result_layer():
    rows = []
    for i, (uid, verdict, llm_floors, llm_usage, rec_floors, rec_usage, shop) in enumerate([
        ("A", "Verified", 1, "Residential", 1, "Residential", "no"),
        ("B", "Verified", 2, "Mixed", 1, "Residential", "yes"),
        ("C", "Unsure", 1, "Residential", 2, "Commercial", "no"),
        ("D", "Pending", None, None, 1, "Residential", None),
    ]):
        rows.append({"unit_id": uid, "building_id": uid, "road": "X STREET", "verdict": verdict, "verdict_why": "",
                     "flag": "", "llm_floors": llm_floors, "llm_usage": llm_usage, "rec_floors": rec_floors,
                     "rec_usage": rec_usage, "poss_shop": shop, "assess_usage": rec_usage, "door_record": "",
                     **{f: None for f in REVIEW_FIELDS}, "geometry": box(i * 20, 0, i * 20 + 10, 10)})
    return gpd.GeoDataFrame(rows, crs=4326)


def settings(tmp_path):
    return Settings(buildings=Path("b"), geocodes=Path("g"), run_dir=tmp_path / "ward_run")


def answers_file(tmp_path, name, by, answers, run="ward_run"):
    p = tmp_path / name
    p.write_text(json.dumps({"run": run, "reviewer": by, "answers": answers}), encoding="utf-8")
    return p


def test_clean_answer_fields():
    got = _clean({"ident": "yes", "floors": "1", "usage": "Residential", "shop": True, "by": "Akash", "note": " ok "})
    assert got["rev_floors"] == 1 and got["rev_shop"] == "yes" and got["review_note"] == "ok"
    assert got["review"] == "right building: yes; floors: G+1; usage: Residential; shop on ground floor"
    wrong = _clean({"ident": "no", "floors": "3", "usage": "Commercial", "by": "Akash"})
    assert wrong["rev_floors"] is None and wrong["rev_usage"] is None    # details only count for the right building
    assert _clean({"ident": "yes", "floors": "cannot tell", "usage": "cannot tell", "by": "A"})["rev_floors"] is None
    with pytest.raises(ValueError):
        _clean({"ident": "maybe"})
    with pytest.raises(ValueError):
        _clean({"ident": "yes", "floors": "1", "usage": "Shop"})


def test_import_merges_two_reviewers_and_scores(tmp_path):
    s = settings(tmp_path)
    s.run_dir.mkdir()
    save_result(result_layer(), None, None, s)
    f1 = answers_file(tmp_path, "a.json", "Akash", {
        "A": {"ident": "yes", "floors": "1", "usage": "Residential", "shop": False, "by": "Akash", "at": "2026-10-01T10:00"},
        "B": {"ident": "yes", "floors": "1", "usage": "Residential", "shop": True, "by": "Akash", "at": "2026-10-01T10:01"},
    })
    f2 = answers_file(tmp_path, "b.json", "Priya", {
        "C": {"ident": "no", "by": "Priya", "at": "2026-10-01T11:00"},
        "B": {"ident": "yes", "floors": "2", "usage": "Residential", "shop": True, "by": "Priya", "at": "2026-10-01T11:05"},
        "ZZ": {"ident": "yes", "floors": "0", "usage": "Residential", "by": "Priya", "at": "2026-10-01T11:06"},
    })
    out, counts = import_answers(s, [f1, f2])
    assert counts == {"files": 2, "answers": 4, "unknown_buildings": 1, "disagreements": 1, "unique_buildings": 3}
    _, res, _, _ = load_result(s)
    res = res.set_index("unit_id")
    assert res.loc["B", "reviewer"] == "Priya" and res.loc["B", "rev_floors"] == 2      # later answer wins
    assert "earlier answer by Akash" in res.loc["B", "review_note"]
    assert res.loc["C", "rev_ident"] == "no"

    scores = review_scores(res.reset_index())
    assert scores["reviewed"] == 3
    assert scores["llm_verified_reviewed"] == 2 and scores["llm_verified_but_wrong_building"] == 0
    assert scores["floors_llm_vs_reviewer"] == {"n": 2, "pct": 100.0}      # A: 1 = 1, B: 2 = 2
    assert scores["floors_record_vs_reviewer"] == {"n": 2, "pct": 50.0}    # B's record says G+1
    assert scores["usage_llm_vs_reviewer"] == {"n": 2, "pct": 50.0}        # B: LLM said Mixed
    assert scores["possible_shop_confirmed"] == {"flagged": 1, "reviewer_saw_shop": 1}


def test_import_refuses_answers_of_another_run(tmp_path):
    s = settings(tmp_path)
    s.run_dir.mkdir()
    save_result(result_layer(), None, None, s)
    other = answers_file(tmp_path, "x.json", "A", {}, run="ward_12")
    with pytest.raises(ValueError):
        import_answers(s, [other])


def test_review_data_lists_only_buildings_with_pictures_north_first():
    res = result_layer()
    aimed = {"A": {"views": [{"image": "A_v1.jpg", "pano_date": "2026-02", "dist_near": 8.2}], "ortho_image": "A_ortho.jpg"},
             "C": {"views": [{"image": "C_v1.jpg", "pano_date": "2022-12", "dist_near": 5.0}], "zoom": {"image": "C_zoom.jpg", "of_view": 1}},
             "D": {"views": []}}
    data = review_data(res, aimed, {"C"}, "ward_run")
    ids = [u["id"] for u in data["units"]]
    assert sorted(ids) == ["A", "C"]
    c = next(u for u in data["units"] if u["id"] == "C")
    assert c["tag"] is True and c["zoom"] == "C_zoom.jpg" and c["llm"]["floors"] == "G+1"
    assert c["rec"]["floors"] == "G+2"                                     # record storeys shown the reviewers' way
