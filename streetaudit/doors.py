"""Door numbers: one spelling for comparison, and the door numbers on record per building."""
from __future__ import annotations

import re

import pandas as pd

MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}
_PREFIX = re.compile(r"^(D\.?\s*NO\.?|DOOR\s*NO\.?|NO\.?|#)\s*[:.\-]?\s*")


def norm_door(text) -> str:
    """Upper case, no spaces, '/' as separator, and Excel's date damage undone (16-Jul -> 16/7, Jan-13 -> 1/13)."""
    s = str(text).strip().upper()
    m = re.fullmatch(r"(\d{1,2})-([A-Z]{3})", s)
    if m and m.group(2) in MONTHS:
        return f"{int(m.group(1))}/{MONTHS[m.group(2)]}"
    m = re.fullmatch(r"([A-Z]{3})-(\d{1,2})", s)
    if m and m.group(1) in MONTHS:
        return f"{MONTHS[m.group(1)]}/{int(m.group(2))}"
    s = _PREFIX.sub("", s)
    return re.sub(r"\s+", "", s).replace("-", "/")


def door_sets(geocodes: pd.DataFrame, id_field: str, door_fields: tuple[str, ...]) -> dict[str, set[str]]:
    """Normalised door numbers on record for each building id."""
    out: dict[str, set[str]] = {}
    for gid, grp in geocodes.groupby(id_field):
        values: set[str] = set()
        for col in door_fields:
            for cell in grp[col].dropna():
                for piece in re.split(r"[,&]", str(cell)):
                    if piece.strip():
                        values.add(norm_door(piece))
        out[str(gid)] = values
    return out


def match_doors(read: list[str], own: set[str], neighbours: dict[str, set[str]]) -> tuple[list[str], list[str]]:
    """Split the numbers read on a building into (own and unique nearby, belonging to a neighbour only).

    `neighbours` maps each nearby building id to its door numbers on record.
    """
    own_hits, neighbour_hits = [], []
    for number in sorted({norm_door(n) for n in read if str(n).strip()}):
        holders = [gid for gid, doors in neighbours.items() if number in doors]
        if number in own and not holders:
            own_hits.append(number)
        elif number not in own and holders:
            neighbour_hits.append(f"{number} is {', '.join(sorted(holders))}")
    return own_hits, neighbour_hits
