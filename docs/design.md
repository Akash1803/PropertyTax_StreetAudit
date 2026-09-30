# Street View property-tax validation: proof of concept on CCMC Ward 44

Design document, 30 September 2026. Status: waiting for Akash's review.

A 10-building pilot was run on 30 September with throwaway scripts, at Akash's request, to learn before building the real tool. What it taught is in section 11 and is already folded into sections 4 and 7. The real tool is not written until this document is approved.

## 1. Purpose

Show that street-level imagery plus an LLM can check property-tax records building by building, so the method can be offered for property-tax work in other districts.

The proof has to answer two questions with evidence:

1. **Identity.** When the tool shows a street view for a building in `buildings.shp`, is it really that building?
2. **Classification.** Once the building is confirmed, does the LLM read its usage, floor count and other tax-relevant features correctly?

This is an internal trial on Ward 44. It is not a CCMC deliverable.

## 2. What counts as proof

The work goes in three stages (Akash, 30 Sep 2026):

1. **Pilot, up to 10 buildings.** Done; see section 11.
2. **Whole ward.** All 2,493 buildings of Ward 44, once the pilot lessons are built in.
3. **GitHub.** The code is pushed to Akash's GitHub only after he approves it; see section 12.

The measures below are reported for the whole ward. Akash's hand review covers a fixed set of 199 buildings, plus any other building he picks on the map.

| Measure | How it is measured |
|---|---|
| Coverage | Share of the sample that ends as Verified, Unsure and Not visible, with the reason for each |
| Identity | Akash opens every Verified building in the review set and marks it Right building or Wrong building. A Verified building that turns out wrong is a failure of the method and is investigated |
| Floors | For Verified buildings, share where the LLM floor count equals the field survey |
| Usage | For Verified buildings, share where the LLM usage equals the field survey |
| Disagreements | Akash judges every disagreement: LLM right, survey right, or cannot tell. Final accuracy counts the cases where the LLM was right |
| Possible shops | Number of "possible shop" flags, and how many Akash confirms |

No pass mark is fixed in advance for floors and usage; the proof reports the numbers. For identity the aim is zero wrong buildings among Verified. Anything the tool cannot prove goes to Unsure, not Verified.

**Review set.** The 199 buildings whose `RoadName` is exactly Ganapathy Layout (92), Chinnammal Street (72) or Sivakumar Street (35). They are already flagged in `coverage\ward44_streetview_coverage.gpkg`, field `sample_street`. Ganapathy Layout is a neighbourhood of several lanes, not one street.

## 3. Inputs

| Input | Used for |
|---|---|
| `Ward_44\buildings.shp` | Footprints; field-survey usage (`Building_U`) and storeys (`Total_Floo`) |
| `Ward_44\geocodes.shp` | Door numbers (`NewDoorNo`, `OldDoorNo`); floor and usage of each tax assessment |
| `Ward_44\Ward44_ecw.ecw` | Ortho image, for the roof and neighbour cross-check |
| Google Street View | Panorama positions and dates (metadata request, free); images (Static API, 70,000 free per month on the India price list) |
| Gemini | Reading the images. The model name is a setting |

Owner names and mobile numbers in `geocodes.shp` are never sent to Google or to the LLM. Door numbers are sent to the LLM, because they are the strongest identity check.

## 4. How it works

### 4.1 Choosing what to run

Akash selects buildings in QGIS and runs the tool on the selection. Three ways to select:

- **One building or a few:** the normal QGIS selection.
- **A street:** draw a line along the street; the tool takes the buildings within a set distance of the line (default 20 m).
- **A stretch of a street:** the same line, limited to the first X metres from its start.

`RoadName` is not used for selection, because the same street is spelled up to five ways.

### 4.2 Finding the panoramas

For the selected buildings the tool lists every Google panorama within 40 m. It asks Google for the nearest panorama at points 5 m apart on the open ground around the buildings and removes duplicates.

It keeps only Google's own outdoor panoramas. User-contributed photos are dropped because their positions are unreliable. It records the panorama ID, position and capture month.

### 4.3 Choosing the views

For each building and each nearby panorama, the tool draws sight lines from the panorama to points along the footprint outline. A point is visible when its sight line does not cross any footprint.

From this it knows, for each panorama:

- how many metres of the building's outline can be seen;
- the compass span the visible part covers;
- the distance and the viewing angle.

It keeps up to four views per building, from different panoramas. The first is the view with the widest visible frontage. Each further view is the one that shows the most wall not yet seen square-on, so every street-facing side gets looked at. A shop is often on the short side of a building: on 44WN1079 the shutter is on the 7 m north face, not the 25 m west face.

Views are not used when the wall is seen nearly end-on (span under 12°), from closer than 3 m, or from a panorama that plots inside a footprint.

A building is **Not visible**, with no image or LLM cost, when no panorama sees at least 3 m of its outline.

A building that no panorama sees from within 15 m is classed **Not street-facing** and is not sent to the LLM. Views from farther away cross open ground or pass through gaps, and in the pilot they showed other buildings.

**View 1 is the closest direct view.** It is always taken from within 15 m, and it is the reference the other views are compared with (4.5).

A footprint made of separate parts is handled one part at a time. Ward 44 has 106 such footprints; in the pilot the two parts of 44WN1073 stand about 25 m apart and are different buildings.

### 4.4 Aiming and marking

Each image is requested by panorama ID, never by coordinates. This removes the fault in the old script, where Google served a panorama somewhere other than the point the heading was computed from.

- **Heading:** the middle of the visible span, computed from the panorama's true position.
- **Field of view:** the span plus a margin, between 40° and 120°.
- **Pitch:** tilted up so upper floors are in frame. The LLM reports when the top is cut off. Fetching again with a higher pitch is not built yet.

The tool then draws the building's span on the image as two vertical marks. It also draws a narrower **core span**: the part that still belongs to this building if the panorama position is wrong by 2 m in either direction along the street. The coverage check found panorama positions off by about 1 to 2 m against the footprints.

For each building it also makes an ortho chip: the ortho around the building, with the footprint outlined in yellow, the other footprints in thin white, and each camera position with its view number and view direction.

### 4.5 Proving identity

The LLM receives the marked street images and the ortho chip. It is not told the door numbers, floors or usage on record: it reads blind, and the comparison is done afterwards in code. This stops it from "reading" a number it was told to expect.

It is told that a shed, lean-to or shop front attached to the front of the marked building belongs to the target even when it sticks out past a mark, because footprints are drawn on the main roof.

It answers in a fixed form:

- whether one building fills the marked span;
- any door number or name board it can read inside the span;
- whether the neighbours on each side match the ortho (attached or gap, relative width, corner plot, vacant plot);
- roof cues visible from the street that also show in the ortho (tiled sunshade, staircase room, water tank, sheet roof);
- what blocks the view (tree, vehicle, compound wall);
- for every view after the first, whether it shows the same building as the closest view.

**The closest view is the reference (Akash, 30 Sep 2026).** On 44WN775 the pilot called a finished house "Under Construction", because a far view across a vacant plot landed on a building site next door and the LLM described that one. The LLM is now told to describe the building of the closest view, to use another view only when it shows the same building, and to name the views that show something else. Such a view is left out; it no longer spoils the result.

The verdict is then decided by fixed rules in code, not by the LLM:

| Verdict | Rule |
|---|---|
| **Verified** | A door number read on the building equals a door number on record for it and for no other building within 50 m. Or: the core span is not empty, the closest view and at least one other view show the same building, and the neighbours or the roof cues match the ortho. In both cases nothing contradicts |
| **Unsure** | A building is visible but the rule above is not met, or the evidence conflicts. The reason is recorded |
| **Not visible** | No line of sight, no panorama, or the view is blocked |
| **Not street-facing** | No panorama sees the building from within 15 m |

If a door number read on the building belongs to a neighbouring building, the building is Unsure with that reason. Re-aiming towards the correct side is not built yet.

**The LLM is asked twice.** In the pilot its opinion on "same building in all views" changed between two runs on the same images for two of ten buildings, and one of those changes was wrong. The two-view rule therefore counts only when both runs agree.

**Door numbers need a closer look.** In the pilot no door number could be read in any of the 29 images. For each building the tool therefore fetches one extra, zoomed image of the gate and entrance. Door numbers on record are cleaned first: Excel has turned many into dates (16/7 is stored as "16-Jul").

### 4.6 Classifying

Verified and Unsure buildings are classified; the verdict stays attached to the result. The LLM reports:

- **Floors**, numbered Akash's way: ground = 0, so G+1 = 1. A level counts as a floor only when it covers most of the building, judged with the ortho. A staircase room, tank room, tower or tall parapet is reported separately as a terrace structure.
- **Usage floor by floor**, and the building usage from the survey's own list (Residential, Commercial, Mixed, Industrial and the rest).
- **Evidence** for the usage: shutters, name boards with their text, goods on display.
- **Construction:** complete, under construction, or vacant land.
- **Roof type** where visible: RCC flat, tiled, sheet.
- **Ground floor parking** (stilt).
- **Extras:** hoarding, cell tower.
- A confidence for each item. "Cannot tell" is an allowed answer.

**Shop rule (Akash, 30 Sep 2026).** A building is Mixed only when there is a sign of trade: a name board, goods, or an open shop. A rolling shutter with no sign of trade does not change the usage; the building gets the flag "possible shop – check".

A board counts as a sign of trade only when it is fixed on the building or its gate and names a business run in that building. Roadside banners, hoardings and boards for a business on another plot are listed separately and do not change the usage.

### 4.7 Comparing with the record

- **Floors:** `Total_Floo` counts storeys (0 = vacant land, 1 = ground only, 2 = G+1), so the record value is `Total_Floo − 1`. Result: same, LLM higher, or LLM lower.
- **Usage:** compared with `Building_U` and with the usages of the building's assessments in `geocodes.shp`.
- **Old imagery:** when the view is from 2022 and the result differs from the 2026 survey, the difference is marked "old imagery".

Each building gets one overall flag: Match, Mismatch, Possible shop – check, or Not checked.

## 5. Outputs

Everything is a map layer. There are no Excel sheets.

Each run has its own folder under `D:\Projects\CCMC\StreetView_POC\runs\` and writes `result.gpkg` there. A run never touches the source shapefiles.

| Layer | Content |
|---|---|
| `buildings_result` | One polygon per footprint part, with verdict and reason, door numbers read, LLM floors and usage, record floors and usage, the flags, confidence, and three fields the reviewers fill in: `review`, `reviewer` and `review_note` |
| `views` | One line per view used, from panorama to building, with heading and field of view |
| `panoramas` | Panorama points with ID and capture month |

The layers are loaded as a styled group in `Ward_44.qgz`, coloured by verdict and by flag, with counts in the legend. Writing the result again keeps what reviewers have entered.

**Evidence for review with a team-mate (Akash, 30 Sep 2026).** The run folder holds an `evidence` folder with one page per building: the marked street views, the zoomed view, the ortho chip, and the record beside the LLM result, with a link to the same panorama in Google Maps. `evidence\index.html` lists all buildings and filters by verdict and flag. A layer action on `buildings_result` opens a building's page from QGIS. The links are relative, so the run folder can be copied or zipped for a second reviewer.

The images are therefore stored in the run folder. This is against Google's rule on storing images (section 9), so run folders stay inside the team and never go to GitHub.

**Summary.** `summary.json` holds the counts of section 2 for the run.

## 6. Parts of the tool

All logic sits in a plain Python package, `streetaudit`, that runs from the command line without QGIS. QGIS's Python is used only to cut ortho chips from the ECW, and QGIS itself to show results. Starting a run from a QGIS toolbox button is not built yet; the selection is passed as building ids or as a street line.

| Part | Does | Depends on |
|---|---|---|
| Image source | Lists panoramas near an area; fetches an image for a panorama, heading, field of view and pitch | Google Street View. Swappable: Mapillary or own 360° imagery can replace it |
| Visibility | Sight lines, visible span, view ranking, core span | Footprints only |
| Marking | Draws the span and core span on an image; computes pixel columns from compass bearings | Visibility |
| Ortho chips | Renders the ortho chip for a building | QGIS, because only its GDAL reads ECW |
| LLM client | Sends images and a prompt; returns the fixed-form answer | Gemini. Swappable |
| Identity rules | Turns the LLM answer and the geometry into Verified, Unsure or Not visible | Visibility, LLM client |
| Classification | Floors, usage, evidence, extras | LLM client |
| Comparison | Flags against the survey and tax record | `buildings.shp`, `geocodes.shp` |
| Result writer | Writes the run GeoPackage and summary | All of the above |
| QGIS loader script | Loads a run as a styled group and provides the evidence action | The run folder |

Code location: `D:\code\PropertyTax_StreetAudit\`, as its own git repository (section 12). The Google and Gemini keys are read from environment variables, not written in the code.

## 7. Failures and edge cases

- **Google or LLM request fails:** retried with a growing wait; after that the building is Unsure with the reason "request failed".
- **LLM answer not in the fixed form:** asked once more; then Unsure with the reason "LLM error".
- **Run interrupted:** a rerun continues from the buildings already done.
- **Panorama inside a footprint:** not used for that building.
- **Narrow frontage:** when the core span is empty, only a door number can verify the building.
- **Building newer than the imagery, or recorded as under construction:** the result carries the capture month, and the comparison is marked "old imagery" where it applies.
- **Vacant land on record:** the LLM is asked whether a building now stands there.
- **Ward edge:** buildings across the road in the next ward have no footprint in `buildings.shp`, so the line-of-sight test would not see them. The footprints of the neighbouring wards (34, 43, 45 and 69 for Ward 44) are loaded as blockers only. Where no neighbouring survey exists, views at the edge rest on the LLM saying the view is hidden.
- **Speed:** the pilot took about 40 seconds of LLM time per building. The whole ward is run with several buildings in parallel, which brings about 28 hours down to a few hours. Image requests for the ward are about 10,000, inside Google's 70,000 free per month.

## 8. Testing

- **Geometry, without any API:** heading and bearing, pixel column of a bearing, sight-line test on made-up footprints, core span, floor conversion, comparison flags, verdict rules.
- **Google and LLM clients:** tested against saved replies, so tests cost nothing and do not depend on the network.
- **One live check:** building 44WN1079. Expected result: G+1 (floor count 1), Residential with "possible shop – check".
- **Pilot buildings again:** the real tool is run first on the same 10 pilot buildings and compared with the pilot result and Akash's review of it, before the whole ward is run.

## 9. Open risk: Google's terms

Google Maps Platform terms (section 3.2.3) forbid creating content from Google Maps content, and give building a tree index from Street View imagery as an example. They also forbid storing the images; panorama IDs may be stored. An LLM writing floors and usage into a layer falls under that restriction.

For this internal trial the tool stores panorama IDs, the LLM's results and, for team review, the marked images in the run folder. Both the stored images and the derived results fall under the restriction, so run folders stay inside the team. Before the method is sold to a district, FarmwiseAI management needs to decide between Google's permission, open imagery such as Mapillary, or imagery captured with its own 360° camera. The image source is a swappable part for this reason.

## 10. Not in this proof

- Other wards or districts.
- Back-lot buildings that cannot be seen from a street.
- Measuring built-up area or computing tax.
- Writing results back into `buildings.shp` or `geocodes.shp`.
- Fixing the ward-label and duplicate problems found in the Ward 44 data.
- Older Street View dates for the same spot.
- A web application.

## 11. What the 10-building pilot taught (30 September 2026)

Ten buildings in Ganapathy Layout were run with throwaway scripts: the eight around 44WN1079, plus one Mixed (44WN1073) and one Commercial (44RN540) building. 68 panoramas were found around them, 29 marked views were fetched, and Gemini (`gemini-pro-latest`) answered for all ten. The result is in `runs\pilot_2026-09-30\pilot_result.gpkg`.

**Result after the second LLM round**

| Building | Verdict | Floors LLM / survey | Usage LLM / survey | Flag |
|---|---|---|---|---|
| 44WN1079 | Verified | G+1 / G+1 | Residential / Residential | Possible shop – check |
| 44WN1072 | Verified | G+1 / G+1 | Residential / Residential | Match |
| 44WN770 | Verified | G+1 / G+1 | Mixed / Residential | Mismatch |
| 44WN771 | Unsure (one view) | G+1 / G+1 | Residential / Residential | Match |
| 44WN779 | Unsure (one view) | G+1 / G+1 | Residential / Residential | Match |
| 44WN780 | Unsure (one clear view) | G+1 / G+1 | Residential / Residential | Match |
| 44WN765 | Unsure (one view) | G+2 / G+1 | Residential / Residential | Mismatch |
| 44WN775 | Unsure (views differ) | G+1 / G+1 | Under Construction / Residential | Mismatch |
| 44WN1073 | Unsure (views differ) | G+3 / G+2 | Residential / Mixed | Mismatch |
| 44RN540 | Unsure (views differ) | G+1 / G+1 | Mixed / Commercial | Mismatch |

Three of ten were Verified, seven Unsure, none Not visible. Akash has not reviewed these yet, so none of the mismatches is settled.

**What worked**

- Aiming by panorama ID from the panorama's true position put the marks on the right building in every case checked by eye (44WN1079, 44RN540, 44WN770, 44WN779).
- Searching all panoramas found views for all ten buildings, including 44WN780, which the coverage check had classed as blocked.
- 44WN1079 came out as Akash expected: G+1, Residential, "possible shop – check" for the closed shutter on the north face.
- The rules kept doubtful cases out of Verified: the two-part footprint 44WN1073 and the back-lot building 44WN775 both ended Unsure.

**What did not work, and the change made in this design**

| Finding | Change |
|---|---|
| The first view choice looked only at the long side and missed the shutter on the short north face of 44WN1079 | Views are chosen to cover every street-facing side, up to four (4.3) |
| The LLM first counted 44WN1079 as G+2 because of the staircase tower | A level is a floor only when it covers most of the roof, judged with the ortho (4.6). This fixed 44WN1079 |
| The LLM's "same building" opinion changed between two runs for 44RN540 and 44WN780; for 44RN540 the second opinion was wrong | The LLM is asked twice and the two-view rule needs both runs to agree (4.5) |
| No door number was readable in 29 views | One extra zoomed image of the gate per building; record door numbers cleaned of Excel date damage (4.5) |
| 44WN770 was called Mixed because of a "Turfify" board on its gate that the LLM itself linked to the next plot | A board counts only when it names a business in that building (4.6) |
| 44WN1073 is one footprint in two parts 25 m apart | Each part is handled separately (4.3) |
| 44WN775 has no street side; its views showed the buildings in front | New class Not street-facing (4.3) |
| Buildings west of the lane at 44WN1079 are in another ward and have no footprints | Neighbouring ward footprints are needed at the ward edge (7) |
| The shutter of 44WN1079 is in a shed outside the digitised footprint | The LLM is told that attached front structures belong to the target (4.5) |
| About 40 seconds of LLM time per building | Whole-ward runs work in parallel (7) |

The throwaway pilot scripts are not the tool; the tool was written fresh from this design.

**Rerun with the real tool.** The same ten buildings (eleven footprint parts) were run again with the tool: 7 Verified, 4 Unsure. 44WN775 came out Verified, G+1, Residential, with its far view across the vacant plot named as another building. 44WN1079 came out Verified, G+1, Residential, "possible shop – check". 44WN770 came out Residential; the roadside board no longer counts. The two LLM runs disagreed on the floors of 44RN540, which is flagged for review.

## 12. Project name and GitHub

**Name: `PropertyTax_StreetAudit`** (chosen by Akash, 30 Sep 2026). It is descriptive, in the style of his `AutoGeorefernce_FMB`, and does not contain "Street View", which is Google's product name; the image source is meant to be swappable and the repository may be shown to clients.

The code folder is `D:\code\PropertyTax_StreetAudit\` and the GitHub repository is `Akash1803/PropertyTax_StreetAudit`.

**What goes to GitHub:** the tool's code, its tests, this design document and a README.

**What never goes to GitHub:** the Google and Gemini keys, any Street View image, the ortho, `buildings.shp` and `geocodes.shp` (owner names and mobile numbers), and run results.

The repository is created private. Nothing is pushed until Akash has reviewed the code and says so.
