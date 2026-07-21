# zephyr-pov — FINAL clean snapshot of the working POV configuration (2026-07-21)

The complete, Asaf-approved POV-widgets setup, captured live after all fixes:
curated networks (11, gears logos), services row (My Services + TorBox account),
per-genre icons, POV maintenance/power entries.

## Contents
- skinshortcuts/  — FULL live set: all .DATA.xml + the .properties (rebuild source)
- skin.zephyr/settings.xml — Zephyr skin settings
- pov/shortcut_folders.json — the two curated POV shortcut folders
  (SELECTED NETWORKS + Connect Services); seed into POV navigator.db:
  INSERT OR REPLACE INTO navigator VALUES (name, 'shortcut_folder', repr(items))

## Addon-side pieces (NOT in this dir)
- POV overlay (overlays-staging/plugin.video.pov): genres per-icon patch
  (menus/navigator.py), genre_tv.png, mk_torbox/mk_tb_connect icons in
  skins/Default/media, network_icons/ (76 curated logos)
- wizard default.py: 'ניקוי קאש POV' tile + maint_pov handler (LOCAL edit on
  Asaf's box; backup default.py.backup_pre_pov_20260721; ship as dynamic
  tile if POV goes fleet)
- skin Includes_Items.xml: power-menu 'ניקוי קאש POV'
  (LOCAL edit; backup Includes_Items.xml.backup_pre_pov_20260721)

## Apply
1. Kodi CLOSED. Copy skinshortcuts/* into script.skinshortcuts, DELETE the
   .hash file (forces menu rebuild). Copy skin settings.
2. Seed pov/shortcut_folders.json into POV's navigator.db.
3. Ensure the POV overlay pieces above are installed.

## Crash verdict (why this exists)
POV widgets crash Zephyr 21.3 identically to Gears widgets:
EXCEPTION_ACCESS_VIOLATION python3.8.dll+0x1c6744 on Home load (dumps
2026-07-21 13:29 gears / 13:48 pov). "Many addon widgets" is the proven
trigger, addon-agnostic.
