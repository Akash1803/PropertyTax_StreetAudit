# Street-level layer

Design, 1 October 2026. Approved by Akash in chat ("Yes, Build it") after the seven parts below were presented.

## Purpose

The building layer answers "what is this building". The street layer answers two questions per street:

1. **Where is the tax register most out of date?** A summary of the building check along each stretch of road.
2. **Is the street in the right tax zone?** The survey's `Zone` (A/B/C) per assessment, checked for consistency along the street and against what the street looks like.

No paid API: the pictures are read in the chat, batch by batch, and the rules are tuned from Akash's corrections.

## 1. Batches are streets, done end to end

A batch is a few stretches of road, about 50 buildings. For a batch:

- the buildings assigned to those stretches go through the building check as before;
- the stretches get their along-road pictures, which are read;
- the street layer is built from both.

Stretches are chosen by name (`--streets`), by stretch id (`--stretch-ids`), or as the stretches that given buildings belong to (`--ids`, `--ids-file`).

## 2. Stretches from Akash's road layer

- The road layer (`roads` in the run's settings) is Akash's hand-digitised line layer. It is only read.
- Every line is split at every junction: where two lines cross, and where a line ends on another line (within 1 m). Pieces shorter than 10 m are dropped.
- A stretch keeps its id between runs: the id is made from its end points and its midpoint, rounded to a metre. When a newly drawn road splits a stretch, that stretch gets new ids; notes typed on the old one are listed in `work/streets/orphan_notes.json` instead of being dropped silently.
- The name comes from `Road_Name`. Where that is empty, `name_suggested` gives the most common `RoadName` among the stretch's buildings, cleaned of spelling variants.

## 3. Which street a building belongs to

- A building seen from the street belongs to the stretch nearest to the camera of its closest planned view, if that stretch is within 15 m of the camera. A corner house is counted once, on the road it is photographed from.
- A building no street view can check belongs to the nearest stretch within 30 m of its footprint and is counted as "cannot check from street".
- A building whose road is not drawn yet belongs to no stretch.
- Assessments (zone, survey usage) follow their building.

## 4. Along-road pictures

- One picture every 50 m along each stretch (at least one per stretch), looking along the road, field of view 90 degrees.
- The panorama must stand on that road: within 10 m of the picture point and nearer to this stretch than to any other. The newest such panorama is used.
- An ortho chip of the stretch shows the stretch, its picture points and its buildings.
- What is read per stretch: road type (main road / street / lane / dead end), surface, width (1 lane / 2 lanes / wide), footpath, drain, street lights, share of the frontage that is shops, a note and a confidence.
- Readings are tied to the pictures they were made from, like the building readings: planning the pictures again invalidates them.

## 5. Worked out by code

- **Building summary:** buildings, seen from the street, checked, Verified, Unsure, cannot check; types seen; shops and possible shops; how many differ from the survey in type and in floors, with their GIS_IDs.
- **Zone:** the main zone of the stretch's assessments, the main zone of the whole named street, and the assessments whose zone differs from the street's, with their GIS_IDs.
- **Zone fit (provisional, tuned with Akash):**
  - the street looks like a main shopping road (main road, or shops on about half the frontage or more, or half the checked buildings trade) but its zone is B or C: "zone may be low";
  - the street looks like a quiet lane (lane or dead end, one lane wide, few or no shops, under a fifth of the checked buildings trade) but its zone is A: "zone may be high";
  - otherwise "fits"; without a street reading "not read".
- **Gap between building fronts:** every 10 m along the stretch, the distance between the nearest footprints on the two sides, up to 25 m each side. Median and minimum. It is not the road width: set-backs and open plots widen it.

## 6. Output

- `<run>_streets[_<name>].geojson`: one line per stretch, coloured green to red by the share of checked buildings that differ from the survey; a zone flag draws the line dashed. Label: name and zone. The form shows a picture, the street reading, the summary, the zone fields and `verified` / `verify_note`, which are kept when the layer is exported again. Action: open in Google Street View.
- `<run>_street_views[_<name>].geojson`: one point per along-road picture at the camera, with its picture.
- The building layer gains `stretch_id` and `street`.
- Pictures are small copies (about 50 KB); `prune` deletes the full-size ones.

## 7. Learning loop

Akash marks stretches right or wrong. Mistakes go into the reading checklist and the rules before the next batch. Tests cover splitting, ids, assignment, picture points, panorama choice, the summary, the zone rules and the reading validation, with no network.

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `roads` | none | the road line layer |
| `road_name_field` | `Road_Name` | its name field |
| `zone_field` | `Zone` | the zone field of the assessments |
| `junction_tol_m` | 1.0 | a line end this close to another line is a junction |
| `min_stretch_m` | 10 | shorter pieces are dropped |
| `street_cam_m` | 15 | camera to stretch, for assigning a building |
| `backlot_m` | 30 | footprint to stretch, for buildings no street view can check |
| `street_step_m` | 50 | spacing of the along-road pictures |
| `street_pano_m` | 10 | picture point to panorama |
| `street_fov` | 90 | field of view of the along-road pictures |
| `gap_step_m`, `gap_reach_m` | 10, 25 | sampling of the gap between building fronts |

## Stages

```
python -m streetaudit -c configs\ward44.toml streets                       # stretches, assignment, picture points (whole ward, free)
"C:\Program Files\QGIS 3.40.10\bin\python-qgis-ltr.bat" tools\export_ortho_chips.py <ortho> <run_dir> stretches.txt --todo street_chips
python -m streetaudit -c configs\ward44.toml street-images --stretch-ids ...   # fetch pictures, mark the ortho, write reading sheets
python tools\write_street_readings.py -c configs\ward44.toml --by "name" notes.jsonl
python -m streetaudit -c configs\ward44.toml street-export --stretch-ids ... --name s1
```

## Refinements made while building the first batch (1 October 2026)

- **Corner houses.** A corner house photographed from two roads goes to the road of its address (the survey's `RoadName` matched to the stretch names, spelling slips allowed). Without this, NSR Road's corner buildings were counted on the side streets and their zone A assessments showed up as odd zones there.
- **Street numbers in names.** "Chinthamani St-3" and "Chinthamanistreet 3" are one street; "St-3" and "St-4" are not.
- **Upper floors not seen.** Shops below with upper floors not seen does not contradict the survey's Mixed; it is reported as "type unclear (upper floors not seen)" and not counted in the survey gap.
- **Earlier checks follow the building.** The building layer of a street batch keeps `verified` / `verify_note` typed in any other batch layer.
