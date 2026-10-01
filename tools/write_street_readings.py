"""Store readings of the along-road pictures, one per stretch, made by eye (a person, or an assistant in a chat).

usage: python tools/write_street_readings.py -c configs/ward44.toml --by "name" notes.jsonl [more.jsonl ...]

One line per stretch:
  {"s": stretch id,
   "p": [[usable, note], ...],     one entry per fetched picture, in order; usable c (clear) / p (partly hidden) / h (hidden)
   "type": main road | street | lane | dead end | cannot tell,
   "surf": tar | concrete | paver | mud | mixed | cannot tell,
   "w": 1 lane | 2 lanes | wide | cannot tell,
   "foot": both sides | one side | none | cannot tell,
   "drain": open | covered | none seen | cannot tell,
   "light": yes | none seen | cannot tell,
   "shops": none | few | about half | mostly | cannot tell,      share of the frontage that is shops
   "note": str, "cf": confidence 0-1}
Later lines for the same stretch replace earlier ones. Each reading is tied to the pictures it was made
from: when the pictures are planned or fetched again, it no longer counts.
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from streetaudit import streets as st  # noqa: E402
from streetaudit.config import Settings  # noqa: E402

USABLE = {"c": "clear", "p": "partly hidden", "h": "hidden"}
CHOICES = {"type": ("road_type", st.ROAD_TYPES + ("cannot tell",)), "surf": ("surface", st.SURFACES),
           "w": ("width", st.WIDTHS), "foot": ("footpath", st.FOOTPATHS), "drain": ("drain", st.DRAINS),
           "light": ("lights", st.LIGHTS), "shops": ("shops", st.SHOPS)}


def expand(n: dict, pictures: list[dict]) -> dict:
    shown = [p for p in pictures if p.get("image")]
    if len(n["p"]) != len(shown):
        raise ValueError(f"{n['s']}: {len(n['p'])} pictures in the notes, {len(shown)} fetched")
    out = {"pictures": [{"n": p["n"], "usable": USABLE[q[0]], "note": q[1] if len(q) > 1 else ""}
                        for p, q in zip(shown, n["p"])]}
    for short, (field, allowed) in CHOICES.items():
        value = n.get(short, "cannot tell")
        if value not in allowed:
            raise ValueError(f"{n['s']}: {short}={value!r}, expected one of {allowed}")
        out[field] = value
    out["note"], out["conf"] = n.get("note", ""), n.get("cf", 0.7)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-c", "--config", required=True)
    ap.add_argument("--by", required=True, help="who read the pictures, written into the layer's checked_by")
    ap.add_argument("notes", nargs="+")
    args = ap.parse_args()
    s = Settings.load(args.config)
    pictures = json.loads((s.work_dir / "streets" / "pictures.json").read_text(encoding="utf-8"))
    notes = {}
    for path in args.notes:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.strip():
                n = json.loads(line)
                notes[n["s"]] = n
    out = s.work_dir / "streets" / "readings"
    out.mkdir(parents=True, exist_ok=True)
    for sid, n in notes.items():
        pics = pictures[sid]["pictures"]
        record = {"key": st.picture_key(pics), "by": args.by, "made": time.strftime("%Y-%m-%d"),
                  "reading": expand(n, pics)}
        (out / f"{sid}.json").write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    print("street readings written:", len(notes))


if __name__ == "__main__":
    main()
