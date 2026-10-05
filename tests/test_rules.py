from streetaudit.rules import (agreeing_views, compare, needs_second_opinion, record_floors, two_view_support,
                               verdict)


def answer(views=2, same="yes", aerial="yes", numbers=(), between="yes", floors=1, usage="Residential", shop=False):
    """An LLM answer with `views` clear views; `same` is what it says about every view after the first."""
    return {
        "views": [{"view": i + 1, "target_visible": "clear", "hidden_by": "nothing",
                   "one_building_between_marks": between, "numbers_read": list(numbers) if i == 0 else [],
                   "same_building_as_reference": "this is the reference view" if i == 0 else same}
                  for i in range(views)],
        "reference_view": 1,
        "aerial_check": {"features_seen_in_both": [], "neighbours_match_aerial": aerial},
        "classification": {"floors_above_ground": floors, "building_usage": usage, "possible_shop": shop},
    }


OWN, NEIGHBOURS = {"62"}, {"N1": {"63"}}


def v(access, answers, core_ok=True, second_required=True, error=""):
    return verdict(access, answers, error, OWN, NEIGHBOURS, core_ok, second_required)


def test_no_line_of_sight():
    assert v("not visible", [])[0] == "Not visible"
    assert v("not street-facing", [])[0] == "Not street-facing"


def test_llm_failure_is_unsure():
    assert v("street-facing", [], error="HTTP 500") == ("Unsure", "LLM error: HTTP 500")


def test_not_asked_yet_is_pending_not_unsure():
    assert v("street-facing", [])[0] == "Pending"


def test_hidden_in_every_view():
    a = answer()
    for view in a["views"]:
        view["target_visible"], view["hidden_by"] = "hidden", "tree"
    assert v("street-facing", [a]) == ("Not visible", "hidden in every view (tree)")


def test_own_door_number_verifies_even_with_one_view():
    assert v("street-facing", [answer(views=1, numbers=["62"])])[0] == "Verified"


def test_a_far_view_of_another_building_is_left_out_not_fatal():
    # three clear views; the third lands on a building under construction next door
    a = answer(views=3)
    a["views"][2]["same_building_as_reference"] = "no"
    assert [x["view"] for x in agreeing_views(a)] == [1, 2]
    assert two_view_support(a, True) == (True, "2 views agree and match the ortho")


def test_reference_is_the_named_visible_view():
    a = answer(views=3)
    a["views"][0]["target_visible"] = "hidden"
    a["reference_view"] = 2
    a["views"][1]["same_building_as_reference"] = "this is the reference view"
    assert [x["view"] for x in agreeing_views(a)] == [2, 3]
    # an extra view fetched later can be the reference; view 2 then counts only if it says "yes"
    a["reference_view"] = 3
    a["views"][1]["same_building_as_reference"] = "cannot tell"
    a["views"][2]["same_building_as_reference"] = "this is the reference view"
    assert [x["view"] for x in agreeing_views(a)] == [3]
    # a hidden reference proves nothing
    a["reference_view"] = 1
    assert agreeing_views(a) == []
    # no name: the first visible view
    del a["reference_view"]
    assert agreeing_views(a)[0]["view"] == 2


def test_screenshots_placed_by_the_checker_verify_on_one_reading():
    a = answer(views=1)
    a["viewer_placed"] = True
    assert v("street-facing", [a]) == ("Verified", "camera placed by the checker at the planned spot; reading confirms one building")
    a["views"][0]["one_building_between_marks"] = "no, more than one"
    assert v("street-facing", [a])[0] == "Unsure"
    b = answer(views=1)
    b["viewer_placed"], b["moved_away"] = True, "picture shows the far side of the junction"
    assert v("street-facing", [b])[0] == "Unsure"
    c = answer(views=1, numbers=["63"])
    c["viewer_placed"] = True
    assert v("street-facing", [c])[0] == "Unsure"                  # a neighbour's door number still blocks


def test_neighbours_door_number_blocks_verification():
    got = v("street-facing", [answer(numbers=["63"]), answer()])
    assert got[0] == "Unsure" and "N1" in got[1]


def test_two_views_need_both_runs_to_agree():
    assert v("street-facing", [answer(), answer()])[0] == "Verified"
    assert v("street-facing", [answer(), answer(same="no")])[0] == "Unsure"
    assert v("street-facing", [answer()]) == ("Unsure", "second LLM run missing")
    assert v("street-facing", [answer()], second_required=False)[0] == "Verified"


def test_two_view_rule_conditions():
    assert two_view_support(answer(views=1), True)[0] is False
    assert two_view_support(answer(same="cannot tell"), True)[0] is False
    assert two_view_support(answer(same="no"), True)[0] is False
    assert two_view_support(answer(between="no, more than one"), True)[0] is False
    a = answer(views=3)
    a["views"][1]["one_building_between_marks"] = "no, more than one"   # a confirming view also shows a neighbour
    assert two_view_support(a, True)[0] is True
    assert two_view_support(answer(aerial="cannot tell"), True)[0] is False
    assert two_view_support(answer(), False)[0] is False          # frontage too narrow
    assert two_view_support(answer(), True)[0] is True


def test_second_opinion_only_when_it_decides_the_verdict():
    assert needs_second_opinion(answer(), OWN, NEIGHBOURS, True) is True
    assert needs_second_opinion(answer(numbers=["62"]), OWN, NEIGHBOURS, True) is False   # door number decides
    assert needs_second_opinion(answer(views=1), OWN, NEIGHBOURS, True) is False          # cannot verify anyway


def test_record_floors_counts_from_ground_zero():
    assert record_floors(0) is None       # vacant land
    assert record_floors(1) == 0          # ground floor only
    assert record_floors(2) == 1          # G+1
    assert record_floors(None) is None


def cmp(answers, storeys=2, usage="Residential", newest="2026-02"):
    return compare(answers, {"storeys": storeys, "usage": usage}, True, newest, "2025-01")


def test_compare_match_and_mismatch():
    assert cmp([answer(floors=1)])["flag"] == "Match"
    assert cmp([answer(floors=2)])["floor_flag"] == "LLM higher"
    assert cmp([answer(floors=2)])["flag"] == "Mismatch"
    assert cmp([answer(usage="Mixed")])["usage_flag"] == "Different"


def test_compare_possible_shop_only_when_everything_else_matches():
    assert cmp([answer(shop=True)])["flag"] == "Possible shop - check"
    assert cmp([answer(shop=True, floors=3)])["flag"] == "Mismatch"


def test_compare_old_imagery_and_disagreeing_runs():
    assert cmp([answer(floors=2)], newest="2022-12")["flag"] == "Mismatch (old imagery)"
    assert cmp([answer(floors=1), answer(floors=2)])["flag"] == "LLM runs disagree"
    assert cmp([answer(floors=1), answer(floors=1)])["runs_agree"] is True


def test_compare_usage_spelling_and_unknown_floors():
    rec = "Office / Lodge/ Theater/ Restaurants"
    assert cmp([answer(usage="Office / Lodge / Theater / Restaurants")], usage=rec)["usage_flag"] == "Same"
    assert cmp([answer(floors=None)])["floor_flag"] == "Cannot tell"


def test_compare_not_checked():
    out = compare([], {"storeys": 2, "usage": "Residential"}, False, None, "2025-01")
    assert out["flag"] == "Not checked" and out["rec_floors"] == 1
