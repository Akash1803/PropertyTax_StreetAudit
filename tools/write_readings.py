"""Store readings made by eye (a person, or an assistant in a chat) as the tool's LLM answers.

The readings are written in a short JSON-lines form and expanded to the full answer form, so the same
fixed rules decide the check and the same export makes the layer. Each answer is keyed to the images it
was made from: when the views are planned or fetched again, the old readings no longer count.

usage: python tools/write_readings.py -c configs/ward44.toml --by "name" notes1.jsonl [notes2.jsonl ...]

One line per building:
  {"u": unit id, "v": [[visible, same, between, what, hidden_by?, boards?], ...],   one entry per view, in order
   "why": str, "air": [[features seen in street view and ortho], "yes|no|cannot tell"],
   "t": building usage, "f": floors above ground (G = 0) or null, "fn": floors note,
   "fu": [[floor, usage, evidence], ...], "trade": [...], "ob": [...], "shop": bool, "sh": shutters,
   "front": str, "st": status, "roof": str, "ter": terrace structures, "stilt": bool, "ex": [...],
   "units": int or null, "zn": [door numbers read in the zoom], "cf": float, "cu": float, "note": str,
   "moved": str (screenshots only: the checker did not stand at the planned spot)}
Codes: visible c (clear) / p (partly hidden) / h (hidden);
       same r (reference: the view that surely shows the building) / y / n (another building) / ? (cannot tell);
       between y / n2 (more than one building) / np (part of a larger building) / ? (cannot tell).
Later lines for the same building replace earlier ones.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from streetaudit import llm  # noqa: E402
from streetaudit.config import Settings  # noqa: E402

VIS = {"c": "clear", "p": "partly hidden", "h": "hidden"}
SAME = {"r": "this is the reference view", "y": "yes", "n": "no", "?": "cannot tell"}
BETWEEN = {"y": "yes", "n2": "no, more than one", "np": "no, only part of a larger building", "?": "cannot tell"}


def expand(n: dict, n_views: int, viewer_placed: bool = False) -> dict:
    views = []
    for k, v in enumerate(n["v"], 1):
        vis, same, between, what = v[0], v[1], v[2], v[3]
        hidden_by = v[4] if len(v) > 4 and v[4] else ("nothing" if vis != "h" else "other")
        views.append({"view": k, "target_visible": VIS[vis], "hidden_by": hidden_by,
                      "one_building_between_marks": BETWEEN[between], "what_is_between_marks": what,
                      "same_building_as_reference": SAME[same], "numbers_read": [],
                      "boards_read": v[5] if len(v) > 5 else [], "top_cut_off": False,
                      "left_side": "cannot tell", "right_side": "cannot tell"})
    if len(views) != n_views:
        raise ValueError(f"{n['u']}: {len(views)} views in the notes, {n_views} in the run")
    refs = [v["view"] for v in views if v["same_building_as_reference"] == SAME["r"]]
    if len(refs) != 1:
        raise ValueError(f"{n['u']}: exactly one view must be the reference, found {refs}")
    if views[refs[0] - 1]["target_visible"] == "hidden":
        raise ValueError(f"{n['u']}: the reference view {refs[0]} must show the building, but it is hidden")
    cls = {"floors_above_ground": n["f"], "floors_note": n.get("fn", ""), "terrace_structures": n.get("ter", "none"),
           "floor_usage": [{"floor": a, "usage": b, "evidence": c} for a, b, c in n.get("fu", [])],
           "building_usage": n["t"], "trade_evidence": n.get("trade", []), "other_boards": n.get("ob", []),
           "shutters_on_ground_floor": n.get("sh", 0), "possible_shop": n.get("shop", False),
           "attached_front_structure": n.get("front", "none"), "construction_status": n.get("st", "complete"),
           "roof_type": n.get("roof", "RCC flat"), "stilt_parking": n.get("stilt", False), "extras": n.get("ex", []),
           "confidence": {"floors": n.get("cf", 0.7), "usage": n.get("cu", 0.8)}, "notes": n.get("note", ""),
           "units_seen": n.get("units")}
    out = {"views": views, "zoom_numbers_read": n.get("zn", []), "zoom_boards_read": [], "reference_view": refs[0],
            "why_same_or_not": n.get("why", ""),
            "classification_based_on_views": [v["view"] for v in views
                                              if v["same_building_as_reference"] in (SAME["r"], "yes")],
            "aerial_check": {"features_seen_in_both": n["air"][0], "neighbours_match_aerial": n["air"][1], "note": ""},
            "identity_confidence": None, "classification": cls}
    if viewer_placed:                       # screenshots taken by the checker at the planned camera
        out["viewer_placed"] = True
        if n.get("moved"):
            out["moved_away"] = n["moved"]
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-c", "--config", required=True)
    ap.add_argument("--by", required=True, help="who read the pictures, written into the layer's checked_by")
    ap.add_argument("notes", nargs="+")
    args = ap.parse_args()
    s = Settings.load(args.config)
    aimed = json.loads((s.work_dir / "aimed.json").read_text(encoding="utf-8"))
    notes = {}
    for path in args.notes:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.strip():
                n = json.loads(line)
                notes[n["u"]] = n
    out = s.work_dir / "llm"
    out.mkdir(parents=True, exist_ok=True)
    for unit_id, n in notes.items():
        info = aimed[unit_id]
        shown = [v for v in info["views"] if v.get("image")]
        record = {"key": llm.request_key(info), "prompt": "read by eye",
                  "runs": [{"answer": expand(n, len(shown), bool(info.get("screenshots"))), "model": args.by, "usage": {}, "error": "",
                            "seconds": 0, "single_reading": True}]}
        (out / f"{unit_id}.json").write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    print("readings written:", len(notes))


if __name__ == "__main__":
    main()
