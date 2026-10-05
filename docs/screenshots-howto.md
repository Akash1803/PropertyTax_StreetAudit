# Taking Street View screenshots for the building check

No Google key is used. You look at each building in the free Street View website, take one screenshot, and save it under the building's id. The script then reads the picture and fills the building's type, floors and shop details; you verify on the map afterwards as before.

## Set-up (once)
1. Open the ward project in QGIS and add the layer `<run>_AI_check_all.geojson` (it loads with its style).
2. Make a folder for the pictures, e.g. `D:\Projects\CCMC\StreetView_POC\screenshots\ward44\`.
3. In Windows Settings > System > Clipboard, nothing special is needed; `Win+Shift+S` (Snipping Tool) must work.

## Faster: AutoScreenshot hotkey (recommended)
Install [AutoScreenshot](https://github.com/artem78/AutoScreenshot) (free, GPL). Settings: **capture on hotkey** (not the timer), output folder = the screenshots folder above, format PNG, any file name (date/time is fine: the file's time is what matters). Then per building:

1. In QGIS, right-click the building > **Street View + copy screenshot name**. The browser opens on the building; QGIS also writes the building id and the time into a log.
2. When the building is framed (small drag or zoom is fine, do not walk down the street), press the AutoScreenshot hotkey. Done. Second picture: press it again.
3. Next building.

The import matches each picture to the building clicked last before it (2 s to 4 min after the click, no other click in between) and cuts the browser bars off automatically. Keep the browser window on the same screen; Google's side panel may stay in the picture.

## Per building, by hand (about 20 seconds)
1. Filter the layer to `check = 'Pending'` (buildings not read yet). Pick one.
2. Right-click it (or use the Actions tool) and choose **Street View + copy screenshot name**. Two things happen: the browser opens Google Street View looking at that building from the planned spot, and the file name (the building id, e.g. `44WN1079`) is copied to your clipboard. QGIS shows the name in a yellow bar at the top.
3. In the browser, wait until the picture is sharp. If the building is not fully in view, drag a little to the left or right or zoom, but **do not walk down the street** - the spot was chosen so that the building is the one in front of you. If you must move, say so in a note later.
4. Press `Win+Shift+S`, drag a box around the Street View picture only (leave out Google's panels). The Snipping Tool opens the snip.
5. Press `Ctrl+S`, paste the name (`Ctrl+V`) in the file-name box, save it in the screenshots folder as PNG.
6. If one picture is not enough (long building, shop on the side), take a second one and save it as `44WN1079_2`.

Multi-part footprints are named with their part: `44WN1073_p1`, `44WN1073_p2` (the id shown in the yellow bar is already right).

## Notes for the reader
- Frame the **whole building**: ground floor to roof, both ends if possible. Floors are counted from the picture.
- Shops: make sure name boards and shutters are in the picture.
- If the building is hidden by trees or a vehicle, try the other arrow buttons in Street View once; if still hidden, save the best you can and name it normally - the reader marks it "hidden".
- Pictures stay on the company's drives only; they are never uploaded anywhere.

## Importing (the script side)
```
python -m streetaudit -c configs\ward44.toml import-screenshots D:\Projects\CCMC\StreetView_POC\screenshots\ward44 --by "Name"
```
It reports how many buildings got pictures, which time-named files were matched through the click log, which files were skipped and why, and writes `work\screenshots_missing.txt` with the buildings still without a picture. Reading sheets go to `evidence\screenshot_sheets\`. After the readings are stored, run `results`, `export --name all`, and the layer updates.
