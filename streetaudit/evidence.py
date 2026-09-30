"""Evidence pages for reviewers: one page per building and an index. All links are relative, so the
run folder can be copied or zipped and opened on another machine.
"""
from __future__ import annotations

import html
import json
from pathlib import Path

CSS = """body{font-family:Segoe UI,Arial,sans-serif;margin:16px;background:#f4f4f4;color:#111}
figure{display:inline-block;margin:6px;vertical-align:top;width:470px}img{width:470px}
figcaption{font-size:13px}table{border-collapse:collapse}td,th{padding:4px 10px;border-bottom:1px solid #ccc;
font-size:14px;text-align:left}td:first-child{font-weight:600;white-space:nowrap}
.Verified{color:#1a7f37}.Unsure{color:#b26a00}.Pending{color:#0a7ea4}.Notvisible,.Notstreet-facing{color:#666}"""


def _e(value) -> str:
    return html.escape("" if value is None else str(value))


def write_page(evidence_dir: Path, row: dict, info: dict, answers: list[dict], source) -> str:
    """Write the page of one building; returns its path relative to the run folder."""
    first = answers[0] if answers else {}
    cls = first.get("classification") or {}
    llm_views = first.get("views") or []
    figures = []
    for n, v in enumerate(info.get("views", []), 1):
        if not v.get("image"):
            continue
        said = llm_views[n - 1] if n - 1 < len(llm_views) else {}
        link = source.viewer_url(v["pano_id"], v["aim"]["heading"], v["aim"]["fov"], v["aim"]["pitch"])
        figures.append(
            f'<figure><img src="{_e(v["image"])}" loading="lazy"><figcaption>View {n} - {_e(v["pano_date"])} - '
            f'{v["dist_near"]:.0f} m away - <a href="{_e(link)}">open this panorama</a><br>'
            f'LLM: {_e(said.get("target_visible"))}; same building as the closest view: '
            f'<b>{_e(said.get("same_building_as_reference"))}</b>; {_e(said.get("what_is_between_marks"))}'
            '</figcaption></figure>')
    if info.get("zoom"):
        figures.append(f'<figure><img src="{_e(info["zoom"]["image"])}" loading="lazy"><figcaption>Zoom of view '
                       f'{info["zoom"]["of_view"]}, ground floor (no marks)</figcaption></figure>')
    if info.get("ortho_image"):
        figures.append(f'<figure><img src="{_e(info["ortho_image"])}" loading="lazy"><figcaption>Ortho, north up. '
                       'Yellow = target, cyan dots = cameras.</figcaption></figure>')
    aerial = first.get("aerial_check") or {}
    table = [
        ("Verdict", f'<b class="{_e(row["verdict"]).replace(" ", "")}">{_e(row["verdict"])}</b> - {_e(row["verdict_why"])}'),
        ("Flag", _e(row["flag"])),
        ("Floors (ground = 0)", f'LLM {_e(row["llm_floors"])} | record {_e(row["rec_floors"])} | {_e(row["floor_flag"])} | {_e(row["floors_note"])}'),
        ("Terrace", _e(row["terrace"])),
        ("Usage", f'LLM {_e(row["llm_usage"])} | survey {_e(row["rec_usage"])} | assessments {_e(row["assess_usage"])} | {_e(row["usage_flag"])}'),
        ("Floor by floor", _e("; ".join(f'{f.get("floor")}: {f.get("usage")} ({f.get("evidence")})' for f in cls.get("floor_usage") or []))),
        ("Possible shop", f'{_e(row["poss_shop"])} | shutters {_e(row["shutters"])} | trade evidence: {_e(row["trade_evid"]) or "none"}'),
        ("Other boards", _e(row["other_boards"])),
        ("Door numbers", f'read: {_e(row["door_read"]) or "none"} | on record: {_e(row["door_record"]) or "none"}'),
        ("Views showing the same building", f'{_e(row["views_agree"])} of {_e(row["views_clear"])} clear views - {_e(first.get("why_same_or_not"))}'),
        ("Views that show another building", _e(row["views_other"]) or "none"),
        ("Classification based on views", _e(", ".join(map(str, first.get("classification_based_on_views") or [])))),
        ("Ortho check", f'{_e(", ".join(aerial.get("features_seen_in_both") or []))} | neighbours match: {_e(aerial.get("neighbours_match_aerial"))}'),
        ("LLM asked twice", "no" if len(answers) < 2 else f'yes - floors/usage agree: {_e(row["runs_agree"])}'),
        ("LLM note", _e(row["llm_note"])),
    ]
    page = (
        f"<!doctype html><meta charset='utf-8'><title>{_e(row['unit_id'])}</title><style>{CSS}</style>"
        f"<p><a href='index.html'>&larr; all buildings</a></p><h2>{_e(row['unit_id'])} - {_e(row['road'])}</h2>"
        "<p>Yellow lines: the target, from the map. Green bar: the part that stays the target if the camera "
        "position is 2 m off.</p><table>"
        + "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in table) + "</table>" + "".join(figures))
    (evidence_dir / f"{row['unit_id']}.html").write_text(page, encoding="utf-8")
    return f"evidence/{row['unit_id']}.html"


def write_index(evidence_dir: Path, rows: list[dict], title: str) -> None:
    """A filterable list of all buildings, for reviewers who are not in QGIS."""
    cols = ["unit_id", "road", "verdict", "flag", "llm_floors", "rec_floors", "llm_usage", "rec_usage", "poss_shop", "verdict_why"]
    data = [{c: r.get(c) for c in cols} | {"has_page": bool(r.get("evidence"))} for r in rows]
    page = f"""<!doctype html><meta charset='utf-8'><title>{_e(title)}</title><style>{CSS}
tr:hover{{background:#e8eef7}}select,input{{font-size:14px;margin-right:12px}}</style>
<h2>{_e(title)}</h2>
<p><label>Verdict <select id='fv'><option value=''>all</option></select></label>
<label>Flag <select id='ff'><option value=''>all</option></select></label>
<label>Search <input id='fq' placeholder='building id or road'></label><span id='count'></span></p>
<table><thead><tr><th>Building</th><th>Road</th><th>Verdict</th><th>Flag</th><th>Floors LLM / record</th>
<th>Usage LLM / survey</th><th>Possible shop</th><th>Why</th></tr></thead><tbody id='rows'></tbody></table>
<script>
const data = {json.dumps(data, ensure_ascii=False).replace("</", "<\\/")};
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c]));
for (const [id, key] of [['fv', 'verdict'], ['ff', 'flag']]) {{
  const counts = {{}};
  data.forEach(r => counts[r[key]] = (counts[r[key]] || 0) + 1);
  Object.keys(counts).sort().forEach(v => document.getElementById(id).insertAdjacentHTML('beforeend',
    `<option value="${{esc(v)}}">${{esc(v)}} (${{counts[v]}})</option>`));
}}
function draw() {{
  const v = fv.value, f = ff.value, q = fq.value.toLowerCase();
  const shown = data.filter(r => (!v || r.verdict === v) && (!f || r.flag === f) &&
    (!q || (r.unit_id + ' ' + (r.road || '')).toLowerCase().includes(q)));
  count.textContent = shown.length + ' of ' + data.length;
  rows.innerHTML = shown.map(r => `<tr><td>${{r.has_page ? `<a href="${{encodeURIComponent(r.unit_id)}}.html">${{esc(r.unit_id)}}</a>` : esc(r.unit_id)}}</td>
    <td>${{esc(r.road)}}</td><td class="${{esc(r.verdict).replace(/ /g, '')}}">${{esc(r.verdict)}}</td><td>${{esc(r.flag)}}</td>
    <td>${{esc(r.llm_floors)}} / ${{esc(r.rec_floors)}}</td><td>${{esc(r.llm_usage)}} / ${{esc(r.rec_usage)}}</td>
    <td>${{r.poss_shop ? 'yes' : ''}}</td><td>${{esc(r.verdict_why)}}</td></tr>`).join('');
}}
[fv, ff, fq].forEach(el => el.addEventListener('input', draw));
draw();
</script>"""
    (evidence_dir / "index.html").write_text(page, encoding="utf-8")
