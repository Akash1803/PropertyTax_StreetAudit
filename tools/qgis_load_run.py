"""Load a run's result into the open QGIS project as a styled group.

In the QGIS Python console:

    exec(open(r"D:\\code\\PropertyTax_StreetAudit\\tools\\qgis_load_run.py", encoding="utf-8").read())
    load_run(r"D:\\Projects\\CCMC\\StreetView_POC\\runs\\ward44_2026-09-30")

The group holds the result by verdict, the same result by flag, the views and the panoramas used.
Identify a building and run the action "Show street view evidence" to open its evidence page.
"""
import os

from qgis.core import (Qgis, QgsAction, QgsCategorizedSymbolRenderer, QgsFillSymbol, QgsLineSymbol, QgsMarkerSymbol,
                       QgsProject, QgsRendererCategory, QgsSingleSymbolRenderer, QgsVectorLayer)

VERDICTS = [("Verified", "40,170,80"), ("Unsure", "255,170,0"), ("Pending", "0,190,230"),
            ("Not street-facing", "120,120,200"), ("Not visible", "150,150,150")]
FLAGS = [("Match", "40,170,80"), ("Possible shop - check", "0,160,220"), ("Mismatch", "220,40,40"),
         ("Mismatch (old imagery)", "240,140,140"), ("LLM runs disagree", "170,60,200"), ("Not checked", "170,170,170")]
# the evidence field holds a path relative to the run folder, so the link works wherever the folder is copied
EVIDENCE_URL = "[% 'file:///' || replace(file_path(layer_property(@layer, 'path')), '\\\\', '/') || '/' || \"evidence\" %]"


def _categorised(layer, field, spec, opacity):
    counts = {}
    for f in layer.getFeatures():
        counts[f[field]] = counts.get(f[field], 0) + 1
    cats = []
    for value, rgb in spec:
        if value in counts:
            sym = QgsFillSymbol.createSimple({"color": f"{rgb},{opacity}", "outline_color": f"{rgb},255", "outline_width": "0.5"})
            cats.append(QgsRendererCategory(value, sym, f"{value} ({counts[value]})"))
    layer.setRenderer(QgsCategorizedSymbolRenderer(field, cats))


def latest_result(run_dir):
    """result.gpkg, or the highest result_<n>.gpkg written while an older result was open in QGIS."""
    best, best_n = None, 0
    for name in os.listdir(run_dir):
        stem, ext = os.path.splitext(name)
        if ext != ".gpkg" or not (stem == "result" or (stem.startswith("result_") and stem[7:].isdigit())):
            continue
        n = 1 if stem == "result" else int(stem[7:])
        if n > best_n:
            best, best_n = os.path.join(run_dir, name), n
    return best


USAGES = ["Residential", "Commercial", "Mixed", "Industrial", "Educational Institutions", "Government Building",
          "Temple", "Church", "Office / Lodge / Theater / Restaurants", "Under Construction", "Vacant Land", "Others",
          "cannot tell"]


def _review_widgets(layer):
    """Drop-down lists for the review fields, so a reviewer can also answer in the QGIS attribute form."""
    from qgis.core import QgsEditorWidgetSetup

    def value_map(field, values):
        idx = layer.fields().indexOf(field)
        if idx >= 0:
            layer.setEditorWidgetSetup(idx, QgsEditorWidgetSetup("ValueMap", {"map": [{v: v} for v in values]}))

    value_map("rev_ident", ["yes", "no", "cannot tell"])
    value_map("rev_usage", USAGES)
    value_map("rev_shop", ["yes", "no"])
    idx = layer.fields().indexOf("rev_floors")
    if idx >= 0:
        layer.setEditorWidgetSetup(idx, QgsEditorWidgetSetup("Range", {"Min": 0, "Max": 15, "Step": 1, "AllowNull": True}))


def load_run(run_dir, group_name=None):
    gpkg = latest_result(run_dir)
    if gpkg is None:
        raise FileNotFoundError(os.path.join(run_dir, "result.gpkg"))
    project = QgsProject.instance()
    group_name = group_name or "Street audit - " + os.path.basename(os.path.normpath(run_dir))
    if project.layerTreeRoot().findGroup(group_name):
        raise RuntimeError(f"group '{group_name}' is already in the project; remove it first")

    def layer(name, title):
        lyr = QgsVectorLayer(f"{gpkg}|layername={name}", title, "ogr")
        if not lyr.isValid():
            raise RuntimeError(f"cannot open layer {name} of {gpkg}")
        return lyr

    verdict = layer("buildings_result", "Result - verdict")
    flag = layer("buildings_result", "Result - flag against the record")
    views = layer("views", "Views (camera to building)")
    panos = layer("panoramas", "Panoramas used")
    panos.setSubsetString('"used" = 1')
    _categorised(verdict, "verdict", VERDICTS, 120)
    _categorised(flag, "flag", FLAGS, 140)
    views.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol.createSimple({"color": "0,255,255,255", "width": "0.2"})))
    panos.setRenderer(QgsSingleSymbolRenderer(QgsMarkerSymbol.createSimple(
        {"name": "circle", "color": "0,255,255,255", "outline_color": "0,0,0,255", "size": "1.4"})))
    for lyr in (verdict, flag):
        lyr.actions().addAction(QgsAction(Qgis.AttributeActionType.OpenUrl, "Show street view evidence", EVIDENCE_URL,
                                          "", False, "Evidence", {"Feature", "Canvas"}))
        _review_widgets(lyr)

    group = project.layerTreeRoot().insertGroup(0, group_name)
    for lyr, visible in ((panos, False), (views, False), (flag, False), (verdict, True)):
        project.addMapLayer(lyr, False)
        group.addLayer(lyr).setItemVisibilityChecked(visible)
    return group
