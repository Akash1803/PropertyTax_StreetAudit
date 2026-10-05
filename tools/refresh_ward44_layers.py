"""Re-export every Ward 44 layer with its own selection and reload it in the open QGIS project.

QGIS locks an open GeoJSON, so each layer is dropped (remembering its visibility and group), exported,
and added back. Run from the repo folder. Exits non-zero when any export fails.
"""
import json
import socket
import struct
import subprocess
import sys
from pathlib import Path

RUN = "D:/Projects/CCMC/StreetView_POC/runs/ward44_2026-09-30/ward44_2026-09-30"
POC = Path(r"D:\Projects\CCMC\StreetView_POC")
STRETCHES = ",".join((POC / "street_batch1_stretches.txt").read_text().split())
FIRST10 = "44WN1079,44WN779,44WN770,44WN1072,44WN771,44WN780,44WN775,44WN765,44WN1073,44RN540"
GROUP = "Street audit - streets batch 1"

# name in QGIS, file, group, stage, arguments
LAYERS = [
    ("Ward44 AI check - ALL (1,652 pending: take screenshots)", RUN + "_AI_check_all.geojson", None, "export", ["--name", "all"]),
    ("Ward44 AI check (batch 2, 50)", RUN + "_AI_check_batch2.geojson", None, "export",
     ["--ids-file", str(POC / "batch2_ids.txt"), "--name", "batch2"]),
    ("Ward44 AI check (first 10)", RUN + "_AI_check.geojson", None, "export", ["--ids", FIRST10]),
    ("Buildings - streets batch 1 (112)", RUN + "_AI_check_streets1.geojson", GROUP, "export",
     ["--stretch-ids", STRETCHES, "--name", "streets1"]),
    ("Streets - batch 1 (22 stretches)", RUN + "_streets_streets1.geojson", GROUP, "street-export",
     ["--stretch-ids", STRETCHES, "--name", "streets1"]),
    ("Street views - along-road pictures (batch 1)", RUN + "_street_views_streets1.geojson", GROUP, None, None),
    ("Streets - whole ward (173 stretches, zone check)", RUN + "_streets_all.geojson", None, "street-export", ["--name", "all"]),
]


def call(cmd, params, timeout=120):
    s = socket.socket()
    s.settimeout(timeout)
    s.connect(("127.0.0.1", 9876))
    body = json.dumps({"type": cmd, "params": params}).encode()
    s.sendall(struct.pack(">I", len(body)) + body)
    h = b""
    while len(h) < 4:
        h += s.recv(4 - len(h))
    n = struct.unpack(">I", h)[0]
    d = b""
    while len(d) < n:
        d += s.recv(n - len(d))
    s.close()
    r = json.loads(d)
    if r.get("status") != "success":
        raise SystemExit(f"QGIS: {r}")
    return r["result"].get("stdout", "").strip()


def main():
    names = [l[0] for l in LAYERS]
    state = call("execute_code", {"code": f'''
import json
from qgis.core import QgsProject
proj = QgsProject.instance(); root = proj.layerTreeRoot(); out = {{}}
for name in {names!r}:
    for l in proj.mapLayersByName(name):
        node = root.findLayer(l.id()); out[name] = node.isVisible() if node else False
        proj.removeMapLayer(l.id())
import gc; gc.collect(); print(json.dumps(out))'''})
    vis = json.loads(state or "{}")
    done = set()
    for name, path, group, stage, extra in LAYERS:
        if stage is None or (stage, tuple(extra)) in done:
            continue
        r = subprocess.run([sys.executable, "-m", "streetaudit", "-c", "configs/ward44.toml", stage, *extra],
                           capture_output=True, text=True)
        line = (r.stdout.strip().splitlines() or [r.stderr.strip()[-300:]])[-1]
        print(f"{stage} {extra[-1] if extra else ''}: {line[-90:]}")
        if "written" not in line and "layer:" not in line:
            sys.exit(1)
        done.add((stage, tuple(extra)))
    print(call("execute_code", {"code": f'''
from qgis.core import QgsProject, QgsVectorLayer
proj = QgsProject.instance(); root = proj.layerTreeRoot()
vis = {vis!r}
for name, path, group in {[(l[0], l[1], l[2]) for l in LAYERS]!r}:
    lyr = QgsVectorLayer(path, name, "ogr")
    if not lyr.isValid(): print("INVALID", name); continue
    proj.addMapLayer(lyr, False)
    parent = root.findGroup(group) if group else None
    node = parent.insertLayer(len(parent.children()), lyr) if parent else root.insertLayer(0 if name.startswith("Ward44 AI check - ALL") else 1, lyr)
    node.setItemVisibilityChecked(vis.get(name, name.startswith("Ward44 AI check - ALL")))
    print(name, lyr.featureCount())'''}))


if __name__ == "__main__":
    main()
