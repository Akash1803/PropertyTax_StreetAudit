"""The fixed rules: is the building proven to be the right one, and does the LLM agree with the record.

The LLM supplies observations. Verdicts and flags are decided here, in code.
"""
from __future__ import annotations

import re

from .doors import match_doors

CLEAR = ("clear", "partly hidden")


def usable_views(answer: dict) -> list[dict]:
    return [v for v in answer.get("views", []) if v.get("target_visible") in CLEAR]


def numbers_read(answers: list[dict]) -> list[str]:
    out = []
    for a in answers:
        for v in a.get("views", []):
            out += [str(n) for n in v.get("numbers_read") or []]
        out += [str(n) for n in a.get("zoom_numbers_read") or []]
    return out


def agreeing_views(answer: dict) -> list[dict]:
    """The reference view and the clear views the LLM says show the same building.

    The reference is the first view in which the target is visible: the views are ordered so that this
    is the closest, most direct one. A far view that lands on another building is simply left out.
    """
    usable = usable_views(answer)
    if not usable:
        return []
    reference = usable[0]
    if answer.get("reference_view") not in (None, reference.get("view")):
        return [reference]            # the LLM compared against another view: its answers cannot be used
    return [reference] + [v for v in usable[1:] if v.get("same_building_as_reference") == "yes"]


def two_view_support(answer: dict, core_ok: bool) -> tuple[bool, str]:
    """Does one LLM run support 'Verified' without a door number? Returns (yes/no, reason)."""
    usable = usable_views(answer)
    agree = agreeing_views(answer)
    if len(usable) < 2:
        return False, "only one clear view and no door number read"
    if len(agree) < 2:
        return False, "no second view clearly shows the same building as the closest view"
    split = [v for v in agree if str(v.get("one_building_between_marks", "")).startswith("no")]
    if split:
        return False, "marks do not hold exactly one building: " + str(split[0]["one_building_between_marks"])
    aerial = answer.get("aerial_check") or {}
    if aerial.get("neighbours_match_aerial") != "yes" and not aerial.get("features_seen_in_both"):
        return False, "views agree but nothing matches the ortho"
    if not core_ok:
        return False, "frontage too narrow for the camera position error; needs a door number"
    return True, f"{len(agree)} views agree and match the ortho"


def door_evidence(answers: list[dict], own: set[str], neighbours: dict[str, set[str]]) -> tuple[list[str], list[str]]:
    return match_doors(numbers_read(answers), own, neighbours)


def needs_second_opinion(answer: dict, own: set[str], neighbours: dict[str, set[str]], core_ok: bool) -> bool:
    """A second LLM run is needed when the first would verify the building by the two-view rule alone."""
    own_hits, neighbour_hits = door_evidence([answer], own, neighbours)
    if own_hits or neighbour_hits or not usable_views(answer):
        return False
    return two_view_support(answer, core_ok)[0]


def verdict(access: str, answers: list[dict], error: str, own: set[str], neighbours: dict[str, set[str]],
            core_ok: bool, second_required: bool) -> tuple[str, str]:
    """(verdict, reason). Verdicts: Verified, Unsure, Not visible, Not street-facing, Pending."""
    if access == "not visible":
        return "Not visible", "no panorama has a line of sight"
    if access == "not street-facing":
        return "Not street-facing", "seen only from a distance, through gaps between other buildings"
    if not answers:
        if error:
            return "Unsure", "LLM error: " + error
        return "Pending", "images are ready, the LLM has not been asked yet"
    first = answers[0]
    if not usable_views(first):
        hidden = sorted({str(v.get("hidden_by", "?")) for v in first.get("views", [])})
        return "Not visible", "hidden in every view (" + ", ".join(hidden) + ")"
    own_hits, neighbour_hits = door_evidence(answers, own, neighbours)
    if neighbour_hits:
        return "Unsure", "door number read belongs to a neighbour: " + "; ".join(neighbour_hits)
    if own_hits:
        return "Verified", "door number read: " + ", ".join(own_hits)
    ok, reason = two_view_support(first, core_ok)
    if not ok:
        return "Unsure", reason
    if len(answers) < 2:
        if second_required:
            return "Unsure", "second LLM run missing"
        return "Verified", reason + " (LLM asked once)"
    ok2, reason2 = two_view_support(answers[1], core_ok)
    if not ok2:
        return "Unsure", "the two LLM runs disagree on identity: " + reason2
    return "Verified", reason + ", in both LLM runs"


def _norm_usage(text) -> str:
    return re.sub(r"[^a-z]", "", str(text).lower())


def record_floors(storeys) -> int | None:
    """Storeys on record (0 = vacant land, 1 = ground only) to floors above ground (ground = 0)."""
    return None if storeys is None or storeys <= 0 else storeys - 1


def compare(answers: list[dict], record: dict, checked: bool, imagery_newest: str | None,
            old_imagery_before: str) -> dict:
    """Floors and usage from the LLM against the record, and one overall flag."""
    out = {"llm_floors": None, "rec_floors": record_floors(record.get("storeys")), "floor_flag": "Not checked",
           "llm_usage": None, "usage_flag": "Not checked", "runs_agree": None, "flag": "Not checked"}
    if not checked or not answers:
        return out
    cls = answers[0].get("classification") or {}
    floors = cls.get("floors_above_ground")
    floors = floors if isinstance(floors, int) and not isinstance(floors, bool) else None
    usage = str(cls.get("building_usage") or "").strip()
    out["llm_floors"], out["llm_usage"] = floors, usage or None
    if floors is None or out["rec_floors"] is None:
        out["floor_flag"] = "Cannot tell"
    else:
        out["floor_flag"] = "Same" if floors == out["rec_floors"] else "LLM higher" if floors > out["rec_floors"] else "LLM lower"
    out["usage_flag"] = "Same" if _norm_usage(usage) == _norm_usage(record.get("usage")) else "Different"
    if len(answers) > 1:
        second = answers[1].get("classification") or {}
        out["runs_agree"] = (second.get("floors_above_ground") == cls.get("floors_above_ground")
                             and _norm_usage(second.get("building_usage")) == _norm_usage(usage))
    mismatch = out["floor_flag"] in ("LLM higher", "LLM lower") or out["usage_flag"] == "Different"
    if out["runs_agree"] is False:
        out["flag"] = "LLM runs disagree"
    elif mismatch:
        old = bool(imagery_newest) and imagery_newest < old_imagery_before
        out["flag"] = "Mismatch (old imagery)" if old else "Mismatch"
    elif cls.get("possible_shop"):
        out["flag"] = "Possible shop - check"
    else:
        out["flag"] = "Match"
    return out
