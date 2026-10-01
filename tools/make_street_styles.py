"""Build the QGIS styles of the street layers (streetaudit/street_style.qml, street_views_style.qml).

Run inside QGIS (Python console), on exported street layers:

    exec(open(r"D:\\code\\PropertyTax_StreetAudit\\tools\\make_street_styles.py", encoding="utf-8").read())
    make_styles(r"<run>\\<run>_streets_<name>.geojson", r"<run>\\<run>_street_views_<name>.geojson")

The picture widget is set up as in the building style (ai_check_style.qml), so all layers show pictures alike.
"""
import os

from qgis.core import (QgsAction, QgsAttributeEditorContainer, QgsAttributeEditorField, QgsEditFormConfig,
                       QgsEditorWidgetSetup, QgsLineSymbol, QgsMarkerSymbol, QgsPalLayerSettings, QgsProperty,
                       QgsRuleBasedRenderer, QgsSingleSymbolRenderer, QgsSymbolLayer, QgsTextBufferSettings,
                       QgsTextFormat, QgsVectorLayer, QgsVectorLayerSimpleLabeling)
from qgis.PyQt.QtGui import QColor, QFont

STYLE_DIR = r"D:\code\PropertyTax_StreetAudit\streetaudit"
PICTURE_TIP = ('<img src="file:///[% replace(file_path(layer_property(@layer, \'path\')), \'\\\\\', \'/\') %]'
               '/[% "image" %]" width="380">')
SHARE = [("no checked building", '"read" IS NULL OR "read" = 0', "160,160,160"),
         ("under 15 % differ from the survey", '"read" > 0 AND "differ_pct" < 15', "30,160,70"),
         ("15-35 %", '"differ_pct" >= 15 AND "differ_pct" < 35', "200,190,0"),
         ("35-60 %", '"differ_pct" >= 35 AND "differ_pct" < 60', "245,140,0"),
         ("60 % and more", '"differ_pct" >= 60', "215,30,30")]


def _picture_config():
    """The picture widget of the building style: the stored path is relative to the layer's own folder."""
    root = {"active": True, "expression": "file_path(layer_property(@layer, 'path'))", "type": 3}
    return {"DefaultRoot": "", "DocumentViewer": 1, "DocumentViewerHeight": 360, "DocumentViewerWidth": 480,
            "FileWidget": True, "FileWidgetButton": False, "FullUrl": False, "RelativeStorage": 2,
            "StorageMode": 0, "UseLink": False,
            "PropertyCollection": {"name": "", "properties": {"propertyRootPath": root}, "type": "collection"}}


def _picture(layer, field, cfg, w=480, h=360):
    cfg = dict(cfg, DocumentViewerWidth=w, DocumentViewerHeight=h)
    layer.setEditorWidgetSetup(layer.fields().indexOf(field), QgsEditorWidgetSetup("ExternalResource", cfg))


def _value_map(layer, field, pairs):
    layer.setEditorWidgetSetup(layer.fields().indexOf(field),
                               QgsEditorWidgetSetup("ValueMap", {"map": [{k: v} for k, v in pairs]}))


def _form(layer, groups):
    cfg = layer.editFormConfig()
    cfg.setLayout(QgsEditFormConfig.TabLayout)
    cfg.clearTabs()
    root = cfg.invisibleRootContainer()
    for title, fields in groups:
        box = QgsAttributeEditorContainer(title, root)
        box.setIsGroupBox(True)
        for f in fields:
            i = layer.fields().indexOf(f)
            if i >= 0:
                box.addChildElement(QgsAttributeEditorField(f, i, box))
        cfg.addTab(box)
    layer.setEditFormConfig(cfg)


def _street_view_action(layer):
    actions = layer.actions()
    for a in actions.actions():
        actions.removeAction(a.id())
    actions.addAction(QgsAction(QgsAction.OpenUrl, "Open in Google Street View", '[% "streetview" %]', "", False,
                                "Street View", {"Feature", "Canvas"}))


def _labels(layer, expression, placement, size=9):
    pal = QgsPalLayerSettings()
    pal.fieldName, pal.isExpression, pal.placement = expression, True, placement
    fmt = QgsTextFormat()
    fmt.setFont(QFont("Arial", size))
    fmt.setSize(size)
    buf = QgsTextBufferSettings()
    buf.setEnabled(True)
    buf.setSize(1.0)
    buf.setColor(QColor("white"))
    fmt.setBuffer(buf)
    pal.setFormat(fmt)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(pal))
    layer.setLabelsEnabled(True)


def style_streets(layer, picture_cfg):
    root = QgsRuleBasedRenderer.Rule(None)
    for label, expr, rgb in SHARE:
        sym = QgsLineSymbol.createSimple({"line_color": rgb, "line_width": "1.6", "capstyle": "round"})
        sym.symbolLayer(0).setDataDefinedProperty(
            QgsSymbolLayer.PropertyStrokeStyle,
            QgsProperty.fromExpression("CASE WHEN \"zone_flag\" = 'yes' THEN 'dash' ELSE 'solid' END"))
        root.appendChild(QgsRuleBasedRenderer.Rule(sym, 0, 0, expr, label))
    layer.setRenderer(QgsRuleBasedRenderer(root))
    _labels(layer, "coalesce(\"street\", '(no name)') || coalesce('  Zone ' || \"zone_street\", '') || "
                   "CASE WHEN \"read\" > 0 THEN '  ' || \"differ\" || '/' || \"read\" || ' differ' ELSE '' END",
            QgsPalLayerSettings.Line)
    _picture(layer, "image", picture_cfg)
    _value_map(layer, "verified", [("yes - all correct", "yes"), ("no - corrected in note", "no"),
                                   ("wrong pictures", "wrong pictures")])
    _form(layer, [
        ("Picture (looking along the road)", ["image"]),
        ("Street", ["stretch_id", "street", "name_drawn", "name_suggested", "length_m", "check", "photo_date",
                    "pictures", "pictures_ok"]),
        ("What the street looks like", ["road_type", "surface", "width_seen", "footpath", "drain", "lights",
                                        "shops_seen", "street_note", "street_conf", "gap_med_m", "gap_min_m"]),
        ("Buildings on this stretch", ["bldgs", "seen", "cant_see", "read", "bldg_verified", "bldg_unsure",
                                       "pending", "residential", "commercial", "mixed", "other_type", "trade_pct",
                                       "shops", "poss_shops"]),
        ("Survey gap", ["differ", "differ_type", "differ_floors", "differ_pct", "differ_ids", "type_unclear"]),
        ("Tax zone", ["assess", "zone_here", "zone_street", "zone_share", "odd_zone", "odd_zone_ids", "zone_fit",
                      "zone_flag"]),
        ("Check", ["checked_by", "checked_on", "verified", "verify_note"]),
    ])
    _street_view_action(layer)
    layer.setMapTipTemplate('<b>[% "street" %]</b> ([% "stretch_id" %]) - Zone [% "zone_street" %], [% "zone_fit" %]<br>'
                            '[% "road_type" %], [% "width_seen" %], shops: [% "shops_seen" %]<br>'
                            'buildings checked [% "read" %], differ from the survey [% "differ" %]<br>' + PICTURE_TIP)


def style_views(layer, picture_cfg):
    sym = QgsMarkerSymbol.createSimple({"name": "arrow", "color": "255,60,255", "outline_color": "0,0,0",
                                        "size": "4.5", "outline_width": "0.3"})
    sym.symbolLayer(0).setDataDefinedProperty(QgsSymbolLayer.PropertyAngle, QgsProperty.fromField("heading"))
    layer.setRenderer(QgsSingleSymbolRenderer(sym))
    _labels(layer, "'P' || \"pic\"", QgsPalLayerSettings.AroundPoint, 8)
    _picture(layer, "image", picture_cfg)
    _form(layer, [("Picture", ["image"]),
                  ("Along-road picture", ["stretch_id", "street", "pic", "of_pics", "along_m", "photo_date", "heading",
                                          "looking", "usable", "pic_note"])])
    _street_view_action(layer)
    layer.setMapTipTemplate('<b>[% "street" %]</b> picture [% "pic" %]/[% "of_pics" %], [% "photo_date" %], '
                            'looking [% "looking" %]<br>' + PICTURE_TIP)


def make_styles(streets_path, views_path):
    cfg = _picture_config()
    for path, style, name in ((streets_path, style_streets, "street_style.qml"),
                              (views_path, style_views, "street_views_style.qml")):
        layer = QgsVectorLayer(path, "x", "ogr")
        if not layer.isValid():
            raise RuntimeError(f"cannot open {path}")
        style(layer, cfg)
        for out in (os.path.join(STYLE_DIR, name), os.path.splitext(path)[0] + ".qml"):
            msg, ok = layer.saveNamedStyle(out)
            print(out, "saved" if ok else msg)
        del layer
    import gc
    gc.collect()          # an unreleased layer keeps the GeoJSON locked, and the next export cannot replace it
