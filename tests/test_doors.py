import pandas as pd

from streetaudit.doors import door_sets, match_doors, norm_door


def test_norm_door_plain_values():
    assert norm_door(" 62/1 ") == "62/1"
    assert norm_door("317-2") == "317/2"
    assert norm_door("16 a") == "16A"
    assert norm_door("D.No: 45") == "45"
    assert norm_door("No.12B") == "12B"


def test_norm_door_undoes_excel_dates():
    assert norm_door("16-Jul") == "16/7"
    assert norm_door("09-Jan") == "9/1"
    assert norm_door("Jan-13") == "1/13"


def test_door_sets_splits_lists_and_merges_fields():
    g = pd.DataFrame({"GIS_ID": ["A", "A", "B"], "NewDoorNo": ["62", "62/1", "4,4-1"], "OldDoorNo": ["2", None, None]})
    sets = door_sets(g, "GIS_ID", ("NewDoorNo", "OldDoorNo"))
    assert sets == {"A": {"62", "62/1", "2"}, "B": {"4", "4/1"}}


def test_match_doors_own_and_unique():
    own_hits, neighbour_hits = match_doors(["62", "No. 62/1"], {"62", "62/1"}, {"N1": {"63"}})
    assert own_hits == ["62", "62/1"] and neighbour_hits == []


def test_match_doors_shared_number_is_not_proof():
    # the same number is also on record for a neighbour: it proves nothing either way
    own_hits, neighbour_hits = match_doors(["5"], {"5"}, {"N1": {"5"}})
    assert own_hits == [] and neighbour_hits == []


def test_match_doors_neighbours_number():
    own_hits, neighbour_hits = match_doors(["63"], {"62"}, {"N1": {"63"}})
    assert own_hits == [] and neighbour_hits == ["63 is N1"]
