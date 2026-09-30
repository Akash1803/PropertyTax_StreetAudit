"""The vision LLM: the question it is asked and the client that asks it.

The LLM is not told the door numbers, floors or usage on record. It reads blind and the comparison is
done afterwards in code, so it cannot "read" a value it was told to expect. Owner names and phone
numbers are never sent.
"""
from __future__ import annotations

import base64
import hashlib
import json
import time
from pathlib import Path

import requests

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

PROMPT = """You are checking buildings for a municipal property-tax survey in an Indian city.

You get {n} street-level photos of ONE target building taken from different camera positions{zoom_text}, then one
aerial photo (ortho, north up).

How the photos are marked:
- In each street photo the target building lies BETWEEN the two yellow lines. The green bar at the top
  shows the part that is the target even if the camera position is a little wrong. Buildings outside the
  yellow lines are neighbours. The marks come from a map, so they can be off by a metre or two.
- The building footprint on the map is the main roof. A shed, lean-to, shop front or porch attached to the
  front of the marked building, inside its compound, may stick out past a yellow line. Report such a
  structure as part of the target.
- In the aerial photo the target is outlined in yellow, other mapped footprints in thin white, and each
  camera is a cyan dot with its view number and two cyan lines to the ends of what it sees.
- View 1 is the closest and most direct view. The other views are often farther away, across open ground
  or through gaps, and can land on a DIFFERENT building by mistake, for example a new building that is not
  on the map yet. The reference view is the first view in which the target is visible (normally view 1).
  For every other view say whether it shows the same building as the reference view.

Do two jobs and answer ONLY with JSON in the form given below.

JOB 1 - is it the right building? Judge from what you can see, never assume.
JOB 2 - describe the target building for tax purposes. Describe the building of the reference view. Use
  another view only if it shows the same building as the reference view; ignore views that show something
  else, even if that other building is more striking (under construction, a shop, taller).

Rules for JOB 2:
- Floors: count floors above the ground floor. Ground floor only = 0, G+1 = 1, G+2 = 2. Count a level as a
  floor only when it covers most of the building. Use the aerial photo to judge this: a staircase room,
  water-tank room, single terrace room, tower or tall decorative parapet that covers a small part of the
  roof is NOT a floor. Describe it in terrace_structures instead. If the top of the building is cut off
  or hidden, say so and give null.
- Usage, floor by floor, then one overall usage from this list: Residential, Commercial, Mixed, Industrial,
  Educational Institutions, Government Building, Temple, Church, Office / Lodge / Theater / Restaurants,
  Under Construction, Vacant Land, Others.
- Call a floor Commercial only when you see a sign of trade: a name board, goods on display, an open shop,
  an office board. A board counts only when it is fixed on the target building or its gate and names a
  business run there. Roadside banners, posters, advertisement hoardings, "to let" and "for sale" boards do
  NOT count: list them under other_boards. A rolling shutter with no sign of trade is NOT enough: keep the
  usage you would give without it and set possible_shop to true.
- Mixed = at least one floor residential and at least one floor with a sign of trade.
- If you cannot tell something, say "cannot tell". Do not guess.

JSON form:
{{
 "views": [
  {{"view": 1,
    "target_visible": "clear | partly hidden | hidden",
    "hidden_by": "nothing | tree | vehicle | compound wall | other building | other",
    "one_building_between_marks": "yes | no, more than one | no, only part of a larger building | cannot tell",
    "what_is_between_marks": "short description: colour, number of floors, striking features",
    "same_building_as_reference": "yes | no | cannot tell | this is the reference view",
    "numbers_read": ["every door number or plot number you can actually read on the target, exactly as written"],
    "boards_read": ["text of every name board or sign on the target"],
    "top_cut_off": true,
    "left_side": "attached building | gap | road or lane | vacant plot | trees | cannot tell",
    "right_side": "attached building | gap | road or lane | vacant plot | trees | cannot tell"
  }}
 ],
 "zoom_numbers_read": ["door or plot numbers readable in the zoomed photo, exactly as written"],
 "zoom_boards_read": ["board texts readable in the zoomed photo"],
 "reference_view": 1,
 "why_same_or_not": "short reason for the same_building_as_reference answers",
 "classification_based_on_views": [1],
 "aerial_check": {{
    "features_seen_in_both": ["things visible in a street photo AND in the aerial photo of the target, e.g. red tiled sunshade on the west edge, water tanks, staircase room, sheet roof"],
    "neighbours_match_aerial": "yes | no | cannot tell",
    "note": "short"
 }},
 "identity_confidence": 0.0,
 "classification": {{
    "floors_above_ground": 0,
    "floors_note": "short, e.g. G+1 with a staircase room on the terrace",
    "terrace_structures": "none, or what stands on the roof and roughly what share of the roof it covers",
    "floor_usage": [{{"floor": 0, "usage": "Residential | Commercial | Parking | cannot tell", "evidence": "short"}}],
    "building_usage": "one value from the list",
    "trade_evidence": ["name boards, goods, open shop - empty list when none"],
    "other_boards": ["boards that are not proof of trade in this building"],
    "shutters_on_ground_floor": 0,
    "possible_shop": false,
    "attached_front_structure": "none, or a short description",
    "construction_status": "complete | under construction | vacant land",
    "roof_type": "RCC flat | tiled | sheet | mixed | cannot tell",
    "stilt_parking": false,
    "extras": ["hoarding, cell tower, solar panels ... empty list when none"],
    "confidence": {{"floors": 0.0, "usage": 0.0}},
    "notes": "anything a tax inspector should know, short"
 }}
}}"""


def _image_part(path: Path) -> dict:
    return {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(path.read_bytes()).decode()}}


def request_key(info: dict) -> str:
    """Identifies the images a building's answers were given for. Answers with another key are out of date."""
    shown = [[v["pano_id"], v["aim"]] for v in info["views"] if v.get("image")]
    zoom = info.get("zoom") or {}
    text = json.dumps([shown, zoom.get("pano_id"), zoom.get("aim"), PROMPT], sort_keys=True)
    return hashlib.sha1(text.encode()).hexdigest()[:16]


def build_request(info: dict, evidence_dir: Path) -> list[dict] | None:
    """The message for one building: prompt, marked street views, zoom, ortho. None when there is no image."""
    views = [(n, v) for n, v in enumerate(info["views"], 1) if v.get("image")]
    if not views:
        return None
    zoom = info.get("zoom")
    parts = [{"text": PROMPT.format(
        n=len(views), zoom_text=", then one zoomed photo of the ground floor" if zoom else "")}]
    for n, v in views:
        parts.append({"text": f"Street photo, view {n} (captured {v['pano_date']}, "
                              f"camera {v['dist_near']:.0f} m from the building):"})
        parts.append(_image_part(evidence_dir / v["image"]))
    if zoom:
        parts.append({"text": f"Zoomed photo of the ground floor, from the camera of view {zoom['of_view']} "
                              "(no marks; use it to read door numbers and boards):"})
        parts.append(_image_part(evidence_dir / zoom["image"]))
    if info.get("ortho_image"):
        parts.append({"text": "Aerial photo (ortho), north up:"})
        parts.append(_image_part(evidence_dir / info["ortho_image"]))
    return parts


class GeminiClient:
    def __init__(self, key: str, models: tuple[str, ...], session: requests.Session | None = None):
        self._key, self._models = key, models
        self._session = session or requests.Session()

    def ask(self, parts: list[dict]) -> dict:
        """Returns {"answer": dict | None, "model", "usage", "error", "seconds"}."""
        body = {"contents": [{"role": "user", "parts": parts}],
                "generationConfig": {"temperature": 0, "responseMimeType": "application/json"}}
        t0, error = time.time(), ""
        for model in self._models:
            for attempt in range(4):
                try:
                    r = self._session.post(API.format(model=model), params={"key": self._key}, json=body, timeout=300)
                except requests.RequestException as exc:
                    error = f"{model}: {type(exc).__name__}"
                    time.sleep(5 * (attempt + 1))
                    continue
                if r.status_code == 200:
                    data = r.json()
                    answer = parse_answer(data)
                    if answer is not None:
                        return {"answer": answer, "model": model, "usage": data.get("usageMetadata", {}),
                                "error": "", "seconds": round(time.time() - t0, 1)}
                    error = f"{model}: answer not in the expected form"
                    continue
                try:
                    err = r.json().get("error", {})
                    status = f"{err.get('status', '')} {str(err.get('message', ''))[:120]}"
                except ValueError:
                    status = ""
                error = f"{model}: HTTP {r.status_code} {status}".strip()
                if r.status_code in (429, 500, 503):
                    time.sleep(15 * (attempt + 1))
                    continue
                break                     # 400 / 403 / 404: this model will not work, try the next one
        return {"answer": None, "model": "", "usage": {}, "error": error, "seconds": round(time.time() - t0, 1)}


def parse_answer(response: dict) -> dict | None:
    """The JSON object in a generateContent response, or None. Some models wrap the object in a list."""
    try:
        text = "".join(p.get("text", "") for p in response["candidates"][0]["content"]["parts"])
        data = json.loads(text)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        return None
    if isinstance(data, list) and len(data) == 1:
        data = data[0]
    return data if isinstance(data, dict) and "classification" in data else None
