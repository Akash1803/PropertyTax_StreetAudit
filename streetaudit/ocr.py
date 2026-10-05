"""Text read off the pictures: shop names, door numbers, pin codes and road names.

Runs offline (EasyOCR) on pictures already in the run folder, so it costs nothing and calls no API.
The text rules are separate from the OCR engine so they can be tested without the models, and so
the engine can be swapped. Adapted from Akash's road_name_from_streetview_ocr.py, with the fixes
agreed on 2026-10-05: Coimbatore pin codes, Tamil road words, numbered streets never merge, no
piece-of-a-name matching between different roads, Google's own panel text kept apart from signs.

Languages: English works. EasyOCR 1.7.2's Tamil model does not load (character-set mismatch in the
published model), so Tamil is read only when `langs` includes "ta" and that loads; the Tamil text
rules are in place for when it does.
"""
from __future__ import annotations

import difflib
import json
import re
from collections import Counter
from pathlib import Path

ROAD_WORDS_EN = r"(?:Main\s+Road|Cross\s+Street|Cross\s+Road|Cross|High\s+Road|Trunk\s+Road|Salai|Road|Rd|Street|St|Avenue|Ave|Lane|Ln|Layout|Nagar|Colony|Extension|Extn|Veethi)"
ROAD_RE = re.compile(r"((?:\b[A-Z0-9][A-Za-z0-9\.\(\)'&-]*\s+){0,6}?\b" + ROAD_WORDS_EN + r")\b\.?(?:\s*[-,]?\s*(\d{1,2})\b)?", re.IGNORECASE)
TAMIL_ROAD_WORDS = ("சாலை", "தெரு", "வீதி", "நகர்", "குறுக்கு", "லேஅவுட்", "காலனி")
TAMIL_ROAD_RE = re.compile(r"((?:[\u0B80-\u0BFF\.\d]+\s+){0,5}?(?:" + "|".join(TAMIL_ROAD_WORDS) + r"))")
PIN_RE = re.compile(r"\b6\s?4\s?1\s?\d\s?\d\s?\d\b")                       # Coimbatore district: 641xxx
DOOR_RE = re.compile(r"\b(?:Old\s+|New\s+)?No\.?\s*[:\-]?\s*(\d+[A-Za-z]?(?:\s*/\s*\d+[A-Za-z]?)*)|\b(\d{1,4}\s*/\s*\d{1,4}[A-Za-z]?)\b", re.IGNORECASE)
BOARD_HINTS = ("corporation", "municipal", "ccmc", "மாநகராட்சி", "கோயம்புத்தூர்", "coimbatore city")
NOISE_RE = re.compile(r"\b(?:coimbatore\s+city\s+municipal\s+corporation|municipal\s+corporation|corporation|ward|zone|division|pin\s*code|coimbatore|tamil\s*nadu)\b[\s:\-]*", re.IGNORECASE)
TAMIL_NOISE_RE = re.compile(r"(?:கோயம்புத்தூர்\s*மாநகராட்சி|கோயம்புத்தூர்|மாநகராட்சி|வார்டு\s*\d*|மண்டலம்)\s*")
STOP_PREFIX = re.compile(r"^(?:(?:old|new)?\s*no\.?\s*[\d/]+[A-Za-z]?\s*,?\s*)+", re.I)
TRAIL_PIN = re.compile(r"[\s,\-]*6\s?4\s?1\s?\d\s?\d\s?\d\s*$")
ABBREV = [(r"\bRd\b\.?", "Road"), (r"\bSt\b\.?", "Street"), (r"\bAve\b\.?", "Avenue"), (r"\bLn\b\.?", "Lane"),
          (r"\bExtn\b\.?", "Extension"), (r"\bFirst\b", "1st"), (r"\bSecond\b", "2nd"), (r"\bThird\b", "3rd"),
          (r"\bFourth\b", "4th"), (r"\b(\d+)\s*(st|nd|rd|th)\b", r"\1\2")]
# Google's viewer draws its own text over the picture: not a sign on a building
UI_RE = re.compile(r"search google maps|google street view|google maps|image capture|^©|^\(c\)|^goo[gl~]*e?\s*$"
                   r"|^\s*(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+20\d\d\s*$"
                   r"|^(?:view|zoom|ortho|screenshot|p)\s*\d*[\s/]*(?:of view)?\s*\d*\s+20\d\d|^view \d|^zoom of|^ortho", re.I)
ROAD_PRIORITY = ("road", "street", "salai", "cross", "lane", "avenue", "veethi", "layout", "nagar", "colony", "extension")
SIMILARITY = 0.85
MIN_CONF = 0.30
LANGS = ["en"]


# ------------------------------------------------------------------------------------------ text rules

def _word(w: str) -> str:
    if re.match(r"^\d", w) or "." in w or (w.isupper() and len(w) <= 3):     # 1st, N.S.R., NSR, C.S.
        return w
    return w[:1].upper() + w[1:].lower()


def normalize(name: str | None) -> str | None:
    if not name:
        return None
    s = re.sub(r"\s+", " ", name).strip(" ,.-")
    s = NOISE_RE.sub(" ", s)
    s = TAMIL_NOISE_RE.sub("", s)
    s = STOP_PREFIX.sub("", s.strip(" ,.-"))
    s = TRAIL_PIN.sub("", s)
    for pat, rep in ABBREV:
        s = re.sub(pat, rep, s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip(" ,.-")
    if not s:
        return None
    if re.search(r"[\u0B80-\u0BFF]", s):
        return s
    return " ".join(_word(w) for w in s.split(" "))


def street_number(name: str) -> str | None:
    m = re.search(r"(?:^|\s)(\d{1,2})(?:st|nd|rd|th)?(?:\s|$)|(?:street|st|cross|road)\s*[-,]?\s*(\d{1,2})\b", name, re.I)
    return (m.group(1) or m.group(2)) if m else None


def same_road(a: str | None, b: str | None) -> bool:
    """Two spellings of one road. Different street numbers are different roads; one name being a
    piece of another ('Main Road' in 'NSR Main Road') is not a match."""
    if not a or not b:
        return False
    a2, b2 = a.lower(), b.lower()
    if a2 == b2:
        return True
    if street_number(a2) != street_number(b2):
        return False
    return difflib.SequenceMatcher(None, re.sub(r"[^a-z0-9\u0B80-\u0BFF]", "", a2),
                                   re.sub(r"[^a-z0-9\u0B80-\u0BFF]", "", b2)).ratio() >= SIMILARITY


def _priority(name: str) -> int:
    low = name.lower()
    for i, w in enumerate(ROAD_PRIORITY):
        if re.search(r"\b" + w, low):
            return i
    return len(ROAD_PRIORITY)


def extract(lines: list[str], panel: list[str] | None = None) -> dict:
    """What one picture's text says: road names (with the kind of sign), door numbers, pin codes, shop names.

    `panel`: lines Google's viewer drew (its address label, dates); they give `google_address` but
    never count as a sign.
    """
    lines = [ln for ln in lines if ln.strip() and not UI_RE.search(ln.strip())]
    full = " ".join(lines)
    low = full.lower()
    is_board = any(h in low for h in BOARD_HINTS)
    pins = sorted({re.sub(r"\s", "", m.group(0)) for m in PIN_RE.finditer(full)})
    doors = []
    for m in DOOR_RE.finditer(full):
        d = (m.group(1) or m.group(2) or "").replace(" ", "")
        if d and d not in doors and not PIN_RE.fullmatch(d):
            doors.append(d)
    raw = []
    for text in lines + [full]:
        for m in ROAD_RE.finditer(text):
            name = m.group(1).strip() + (" " + m.group(2) if m.group(2) else "")
            if len(name.split()) >= 2:
                raw.append(name)
        for m in TAMIL_ROAD_RE.finditer(text):
            if len(m.group(1).split()) >= 2:
                raw.append(m.group(1).strip())
    names = []
    for r in sorted(set(raw), key=len, reverse=True):
        n = normalize(r)
        if n and len(n.split()) >= 2 and not any(same_road(n, x) for x in names):
            names.append(n)
    names.sort(key=_priority)
    kind = "street_name_board" if is_board else ("shop_address" if (doors or pins) else "other")
    shops = [ln.strip() for ln in lines
             if 2 <= len(ln.strip()) <= 40 and not ROAD_RE.search(ln) and not DOOR_RE.search(ln)
             and not PIN_RE.search(ln) and re.search(r"[A-Za-z\u0B80-\u0BFF]{3}", ln)][:6]
    google = None
    for ln in panel or []:
        m = ROAD_RE.search(ln)
        if m and not UI_RE.search(ln):
            google = normalize(m.group(1))
            break
    return {"kind": kind, "road_names": names[:3], "doors": doors[:4], "pins": pins, "shop_lines": shops,
            "google_address": google, "text": full[:400]}


def decide_name(candidate: str | None, evidence: list[dict]) -> dict:
    """Akash's rules a-d over the evidence of one street stretch.

    evidence items: {"name", "kind", "unit_id", "dist_m"}. Returns name, status, rule, fix (road layer wrong).
    """
    cand = normalize(candidate)
    evidence = [{**e, "name": normalize(e["name"])} for e in evidence if normalize(e["name"])]
    boards = [e for e in evidence if e["kind"] == "street_name_board"]
    shops = [e for e in evidence if e["kind"] == "shop_address"]

    def groups(items):
        out: list[dict] = []
        for e in items:
            for g in out:
                if same_road(e["name"], g["name"]):
                    g["items"].append(e)
                    if len(e["name"]) > len(g["name"]):
                        g["name"] = e["name"]
                    break
            else:
                out.append({"name": e["name"], "items": [e]})
        return out

    if boards:
        gs = groups(boards)
        if cand:
            hit = [g for g in gs if same_road(g["name"], cand)]
            if hit:
                return {"name": hit[0]["name"], "status": "Accepted - board confirms", "rule": "a", "fix": False}
        gs.sort(key=lambda g: (-len(g["items"]), min(i.get("dist_m", 0) for i in g["items"])))
        if len(gs) > 1 and len(gs[0]["items"]) == len(gs[1]["items"]):
            return {"name": None, "status": "Conflict - needs review", "rule": "conflict", "fix": False}
        return {"name": gs[0]["name"], "status": "Board overrides road layer" if cand else "Accepted - board (no road layer name)",
                "rule": "b", "fix": bool(cand)}
    if shops:
        uniq = {}
        for e in shops:
            uniq.setdefault(e["unit_id"], e)          # one vote per building
        gs = sorted(groups(list(uniq.values())), key=lambda g: -len(g["items"]))
        if gs and len(gs[0]["items"]) >= 2:
            if len(gs) > 1 and len(gs[1]["items"]) == len(gs[0]["items"]):
                return {"name": None, "status": "Conflict - needs review", "rule": "conflict", "fix": False}
            g = gs[0]
            return {"name": g["name"], "status": "Accepted - shop addresses agree", "rule": "c",
                    "fix": bool(cand) and not same_road(g["name"], cand)}
    if cand:
        return {"name": cand, "status": "Unverified - road layer only", "rule": "d", "fix": False}
    best = Counter(e["name"] for e in evidence).most_common(1)
    return {"name": best[0][0] if best else None, "status": "Unverified - single sign only" if best else "No name found",
            "rule": "none", "fix": False}


# ------------------------------------------------------------------------------------------ the engine

_readers: dict[str, object] = {}


def reader(langs: list[str] | None = None):
    key = ",".join(langs or LANGS)
    if key not in _readers:
        import easyocr
        _readers[key] = easyocr.Reader(langs or LANGS, gpu=False, verbose=False)
    return _readers[key]


def read_lines(path: Path, min_conf: float = MIN_CONF, langs: list[str] | None = None) -> tuple[list[str], list[str]]:
    """OCR one picture into text lines (top to bottom, left to right) and the lines of Google's panel.

    In a Street View screenshot the viewer's card sits in the top-left corner; text whose box lies in
    the top 40 % and left 35 % of the picture is treated as panel text when the picture carries the
    viewer's own labels at all.
    """
    from PIL import Image
    w, h = Image.open(path).size
    results = [r for r in reader(langs).readtext(str(path), detail=1, paragraph=False) if r[2] >= min_conf]
    results.sort(key=lambda r: (round(min(p[1] for p in r[0]) / 15), min(p[0] for p in r[0])))
    has_ui = any(UI_RE.search(r[1]) for r in results)
    lines, panel, cur, cur_y, cur_panel = [], [], [], None, False
    for box, text, _ in results:
        y = round(min(p[1] for p in box) / 15)
        xs, ys = [p[0] for p in box], [p[1] for p in box]
        # the viewer's address card (top left) and its mini-map (bottom left)
        in_panel = has_ui and max(xs) < 0.35 * w and (max(ys) < 0.40 * h or min(ys) > 0.70 * h)
        if cur and (y != cur_y or in_panel != cur_panel):
            (panel if cur_panel else lines).append(" ".join(cur))
            cur = []
        cur.append(text)
        cur_y, cur_panel = y, in_panel
    if cur:
        (panel if cur_panel else lines).append(" ".join(cur))
    return lines, panel


def read_unit(unit_id: str, images: list[Path], out: Path, langs: list[str] | None = None) -> dict:
    """OCR every picture of a building; one record per picture plus the merged extraction. Cached on disk."""
    record = {"unit_id": unit_id, "langs": langs or LANGS, "pictures": []}
    all_lines, all_panel = [], []
    for img in images:
        if not img.exists():
            continue
        lines, panel = read_lines(img, langs=langs)
        record["pictures"].append({"image": img.name, "lines": lines, "panel": panel, **extract(lines, panel)})
        all_lines += lines
        all_panel += panel
    record["merged"] = extract(all_lines, all_panel)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    return record
