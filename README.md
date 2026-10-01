# PropertyTax_StreetAudit

Checks property-tax building records against street-level imagery.

For each building footprint the tool finds the street panoramas that can really see it, aims the camera at it, marks it on the image, and asks a vision LLM two things: is this the right building, and what is it (floors, usage, shop on the ground floor). Fixed rules in code then decide whether the building is **Verified**, and the result is compared with the field survey and the tax record.

Built as a proof of concept on Ward 44 of the CCMC building survey. The design and the lessons from the 10-building pilot are in [docs/design.md](docs/design.md).

## Why not just send a street image to an LLM

That is what the first trial did, and it showed wrong buildings. Three things cause it:

- An image asked for by coordinates comes from the nearest panorama, which is somewhere else, so the heading is wrong.
- The nearest road is often the lane behind the building.
- "Classify this building" does not say which building in the frame.

This tool asks for images by panorama ID from the panorama's known position, tests the line of sight against the footprints, draws the target on the image, and lets the LLM only observe. The verdict comes from rules.

## How it works

1. **Panoramas.** Every panorama within 40 m of the selected buildings is listed (free metadata requests). User-contributed photos and panoramas that plot inside a footprint are not used.
2. **Views.** For each building, sight lines to its outline are tested against all footprints, including neighbouring wards'. Up to four views are chosen so that every street-facing side is seen. A building seen only from beyond 15 m is *Not street-facing*; one with no line of sight is *Not visible*. Neither costs an image or an LLM call.
3. **Images.** Each view is fetched and marked: two yellow lines at the ends of the visible part of the target, and a green bar over the part that stays the target if the camera position is 2 m off. One zoomed view of the ground floor and an ortho chip with the target outlined are added.
4. **LLM.** The LLM sees the marked views, the zoom and the ortho. It is not told the door numbers, floors or usage on record. Before a building is Verified on the strength of its views alone, the LLM is asked a second time.
5. **Rules.** A building is **Verified** when a door number read on it is on record for it and for no other building within 50 m, or when at least two views agree, match the ortho, and both LLM runs say so. Everything else with a visible building is **Unsure**, with the reason.
6. **Comparison.** Floors (ground = 0, so G+1 = 1) and usage are compared with the record. A rolling shutter with no sign of trade does not make a building Mixed; it raises the flag *Possible shop - check*.

## Setup

```
pip install -r requirements.txt
copy .env.example .env      # then put the two keys in .env
```

- `GOOGLE_MAPS_API_KEY`: a key with the Street View Static API enabled.
- `GEMINI_API_KEY`: a Gemini API key.

A key in `.env` takes precedence over a variable of the same name already set on the machine.

Ortho chips are cut with QGIS's Python, because only its GDAL reads ECW.

## Running

Settings for a run are in a TOML file; see [configs/ward44.toml](configs/ward44.toml).

```
python -m streetaudit -c configs\ward44.toml panoramas
python -m streetaudit -c configs\ward44.toml views
"C:\Program Files\QGIS 3.40.10\bin\python-qgis-ltr.bat" tools\export_ortho_chips.py <ortho.ecw> <run_dir>
python -m streetaudit -c configs\ward44.toml images
python -m streetaudit -c configs\ward44.toml llm
python -m streetaudit -c configs\ward44.toml results
python -m streetaudit -c configs\ward44.toml export
python -m streetaudit -c configs\ward44.toml prune
```

`export` writes the deliverable: one GeoJSON layer with the identified building type, floors and other details in its attributes, one small picture per building (view 1 with the target marked, about 50 KB) and a QGIS style of the same name. `prune` then deletes the full-size pictures and ortho chips the earlier stages needed; they can be fetched again.

Every stage keeps what is already done, so an interrupted run can be continued.

To work on part of the ward, add one of these to any stage:

| Option | Selects |
|---|---|
| `--ids 44WN1079,44WN770` | the listed buildings |
| `--ids-file ids.txt` | the buildings listed one per line |
| `--line "LINESTRING(76.941 11.030, 76.943 11.030)" --buffer 20` | buildings within 20 m of a street line (EPSG:4326) |
| `--line ... --max-length 150` | the same, for the first 150 m of the line |

## What comes out

The deliverable is `<run>_AI_check.geojson` in the run folder, with `images\` and `<run>_AI_check.qml` beside it. Add the GeoJSON to QGIS and the style loads with it:

- buildings coloured by the type the script identified, labelled with floors and type;
- the attribute form shows the marked picture at the top, then the identified details, the survey values (for comparison only), and two fields for the checker: `verified` and `verify_note`;
- the map tip shows the picture; the action "Open in Google Street View" opens the same panorama live.

| Attribute | Meaning |
|---|---|
| `check` | Verified / Unsure / Not visible / Not street-facing: is the picture proven to show this building |
| `bldg_type`, `floors`, `floor_count` | identified from the pictures; floors counted from ground = 0 (G+1 = 1) |
| `floor_use`, `shop_gf`, `units_seen` | use floor by floor, shop on the ground floor (yes / possible / no), dwellings or shops seen |
| `construct`, `roof`, `terrace`, `front`, `boards`, `other_boards`, `extras` | other details for the tax check |
| `survey_type`, `survey_floors`, `vs_survey` | the survey values and where they differ; never used as the answer |
| `verified`, `verify_note` | filled in by the checker; kept when the layer is exported again |

Working files in the run folder:

| File | Content |
|---|---|
| `result.gpkg`, layer `buildings_result` | one polygon per building part: verdict and reason, LLM floors and usage, record floors and usage, flags, door numbers read, and the fields `review`, `reviewer`, `review_note` for the reviewers |
| `result.gpkg`, layers `views`, `panoramas` | the views used and the panoramas found |
| `evidence\<building>.html` | the marked views, zoom, ortho chip and the LLM's answer beside the record |
| `evidence\index.html` | all buildings, filterable by verdict and flag |
| `summary.json` | the counts |

The evidence links are relative, so the run folder can be copied or zipped for a second reviewer.

In QGIS, [tools/qgis_load_run.py](tools/qgis_load_run.py) loads a run as a styled group. Identify a building and run the action **Show street view evidence**.

Rerunning the `results` stage keeps what reviewers have typed into `review`, `reviewer` and `review_note`. If `result.gpkg` is open in QGIS at that moment it cannot be replaced, so the new result is written beside it as `result_2.gpkg`; the loader always takes the newest.

Buildings the LLM has not been asked about yet have the verdict **Pending**, so a ward can be asked in batches.

## Reading the pictures by eye

Without an LLM, a person (or an assistant in a chat) can read the marked pictures and store the readings with [tools/write_readings.py](tools/write_readings.py). The readings use the same answer form, so the same rules decide the check and the same export makes the layer. The script refuses readings whose number of views does not match the run, or whose reference view is hidden.

Checklist that came out of Akash's checks of the first 60 buildings:

- A view counts as "same building" only on matching features (colour, windows, balconies, gate, roof), never because it is "also a grey house".
- A camera a few metres from the footprint must show the building close up. If it shows a building far behind a wall, that view shows another building (44WN1679).
- Count every level of shops, including a street-level floor below raised shops (44WN1048).
- A stilt parking level is the ground floor; count the floors above it (44WN1242).
- When the current views are hidden, look at older panoramas and other angles before answering "cannot tell" (44WN1073 part 2, 44WN1142).
- Check which footprint a shop board belongs to with the neighbouring footprints' distances, not by eye alone (44WN1749, 44WN1141).

Answers are tied to the images they were read from (`request_key`). Rewording the LLM question does not invalidate them; planning or fetching the views again does. [tools/rekey_answers.py](tools/rekey_answers.py) migrated answers written under the older key.

## Tests

```
python -m pytest
```

The tests cover the camera geometry, the line-of-sight test, view selection, door-number matching and the verdict and comparison rules. They need no API key and no network.

## Limits

- **Blockers are footprints only.** Trees, compound walls and unmapped buildings are not known to the line-of-sight test. The LLM reports when a view is hidden.
- **Door numbers** are rarely readable at 640 px. Most Verified buildings rest on the two-view rule.
- **The LLM is not stable.** On the same images its identity opinion can change between runs; this is why it is asked twice.
- **Back-lot buildings** cannot be audited from the street.
- **Re-aiming** when a neighbour's door number is read is not implemented; such buildings are Unsure.

## Imagery terms

Google Maps Platform terms restrict creating content from Street View imagery and storing the images. Run folders hold stored images and must stay internal. They are excluded from this repository by `.gitignore`. Before this method is offered as a service, the image source has to be settled: `streetaudit/sources.py` is the only place that knows where images come from, so own 360° imagery or an open source can replace it.

Owner names and phone numbers in the survey data are never sent to the image source or the LLM, and no survey data is kept in this repository.
