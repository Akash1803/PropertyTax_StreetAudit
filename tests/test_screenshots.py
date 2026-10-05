import os
import time
from datetime import datetime

import numpy as np
from PIL import Image

from streetaudit.screenshots import crop_chrome, load_clicks, match_by_clicks, match_files

UNITS = {"44WN1", "44WN2_p1", "44WN2_p2", "1001"}
PARTS = {"44WN1": ["44WN1"], "44WN2": ["44WN2_p1", "44WN2_p2"], "1001": ["1001"]}


def touch(path, when):
    path.write_bytes(b"x")
    os.utime(path, (when, when))
    return path


def test_files_named_by_building_id(tmp_path):
    for n in ("44WN1.png", "44wn1_2.PNG", "44WN2.jpg", "44WN2_p2.png", "1001.webp", "random.png", "44WN9.png"):
        (tmp_path / n).write_bytes(b"x")
    found, unknown = match_files(tmp_path, UNITS, PARTS)
    assert [p.name for p in found["44WN1"]] == ["44WN1.png", "44wn1_2.PNG"]
    assert [p.name for p in found["44WN2_p1"]] == ["44WN2.jpg"]           # bare id of a multi-part building = part 1
    assert [p.name for p in found["44WN2_p2"]] == ["44WN2_p2.png"]
    assert "1001" in found
    assert sorted(p.name for p in unknown) == ["44WN9.png", "random.png"]


def test_time_named_files_follow_the_click_log(tmp_path):
    t0 = time.time() - 1000
    log = tmp_path / "clicks.log"
    log.write_text("\n".join(f"{u},{datetime.fromtimestamp(t0 + s).isoformat(timespec='seconds')}"
                             for u, s in (("44WN1", 0), ("44WN2", 60), ("44WN2_p2", 120))) + "\n")
    clicks = load_clicks(log)
    assert [c[1] for c in clicks] == ["44WN1", "44WN2", "44WN2_p2"]
    files = [touch(tmp_path / "shot_a.png", t0 + 15), touch(tmp_path / "shot_b.png", t0 + 30),
             touch(tmp_path / "shot_c.png", t0 + 61), touch(tmp_path / "shot_d.png", t0 + 150),
             touch(tmp_path / "shot_e.png", t0 + 120 + 300), touch(tmp_path / "shot_f.png", t0 - 5)]
    found, rejected = match_by_clicks(files, clicks, UNITS, PARTS)
    assert [p.name for p in found["44WN1"]] == ["shot_a.png", "shot_b.png"]   # two pictures after one click
    assert [p.name for p in found["44WN2_p2"]] == ["shot_d.png"]
    reasons = {p.name: why for p, why in rejected}
    assert "too soon" in reasons["shot_c.png"]                                  # 1 s after the click
    assert "too long" in reasons["shot_e.png"]                                  # 5 min after the last click
    assert "before the first click" in reasons["shot_f.png"]
    assert "44WN2_p1" not in found                                              # nothing usable followed that click


def test_crop_chrome_cuts_uniform_bars_only():
    rng = np.random.default_rng(0)
    photo = rng.integers(0, 255, (600, 800, 3), dtype=np.uint8)
    full = np.full((800, 800, 3), 240, dtype=np.uint8)        # 120 px browser chrome above, 80 px taskbar below
    full[120:720] = photo
    out = crop_chrome(Image.fromarray(full))
    assert out.size == (800, 600)
    plain = Image.fromarray(np.full((400, 400, 3), 200, dtype=np.uint8))
    assert crop_chrome(plain).size == (400, 400)              # nothing photographic: left alone
