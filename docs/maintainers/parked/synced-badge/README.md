# Parked: synced pill badge in the Gears sources window

Built + tested on-device 2026-07-13; Asaf preferred the original orange inline
text and asked to keep the design parked for a possible polish round later.

## What it was
A colored pill at the top-right of every source row in `sources_results.xml`
(all three view variants: 1180 list / 935 panel / 1700 wide, itemlayout +
focusedlayout = 6 blocks), replacing the orange `כתוביות: [site] NN% התאמה`
text on the SIZE line:

- green `FF1D9E75` — `מסונכרן NN% ✓` (sync >= 90%)
- amber `FFC9821E` — `חלקי NN% ~` (70-89%)
- cyan  `FF2596BE` — `מוטמע ✓` (embedded Hebrew subs)

## Files in this folder (ready to drop back into the overlay)
- `windows_sources.py` → `overlays/plugin.video.gears/files/resources/lib/windows/sources.py`
  (unpacks the 5-tuple, sets `heb_sync`/`heb_sync_color`/`heb_sync_label`
  properties, stops appending the orange text to size_label)
- `hebrew_subtitles_search_utils.py` → `overlays/plugin.video.gears/files/resources/lib/kodirdil/`
  (returns a 5th element: `sync_badge = (type, percent)`)
- `sources_results.xml` → `overlays/plugin.video.gears/files/resources/skins/Default/1080i/`
  (pill = circle.png colordiffused + label, x = row_width-230, y 18, 210x26;
  SIZE-line `width max` capped per variant: 710 / 475 / 1240 so text never
  collides with the pill)

## Re-activation
Copy the three files to the paths above, run `tools/apply_overlay.py overlays
addons --verify`, bump nothing (gears ships by sha). NOTE: these files are a
snapshot of the overlay at 2026-07-13 — if `windows/sources.py` or the utils
have changed since, re-apply the badge hunks manually instead of copying whole
files.

## Improvement ideas noted during testing
- Pill felt visually heavy vs the row design; consider a slimmer outline-style
  chip, or badge only on the focused row.
- Mixed RTL text ordering inside the pill (`✓`/`%`) needs a careful pass.
