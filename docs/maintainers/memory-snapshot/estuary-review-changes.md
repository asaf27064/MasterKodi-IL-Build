---
name: estuary-review-changes
description: Estuary review customizations to REPLICATE across all skins (Nimbus/AF3/Zephyr)
metadata: 
  node_type: memory
  type: project
  originSessionId: 3dfd12c0-e979-4f10-a359-3f2151554677
---

Skin-settings review (2026-07-14), starting with Estuary. Asaf: these Estuary
changes will PROBABLY be applied to the other skins too (Nimbus, AF3, Zephyr) —
replicate them per skin as the review proceeds. See [[skin-settings-review-workflow]].

**SHIPPED 2026-07-14 (commit 33eb611, wizard 2.4.48, config 20):** Estuary batch +
AF3 batch (חיבור שירותים rename, תחזוקה category via wizard maintenance_folder,
autoscroll+hubpreloading, 11 official TMDb network icons in gears overlay 2.2.10)
+ clean repo-based Arctic_Fuse_Skin.zip published to v1.0 (end-to-end install-tested
first; old zip carried 95 cache files + full-system Addons33.db). EXE/APK rebuilt.
**REVIEW COMPLETE (2026-07-15): all four skins shipped.** Nimbus batch (config
21-23, wizard 2.4.51, clean nimbus.zip published): service-connect + maintenance
categories via wizard maintenance_folder, fixed fontset names, star-glyph tofu +
Hebrew-genre-chip fixes, 12 curated gears views, he_il view names. Zephyr batch
(config 26, zephyr 1.0.51.23): 9 settings defaults force_id'd (Icons=colorful,
flipside, flixhidemenu, homeicons, osd.autohideonpause, playerscrollseekbar,
alphavalue_highlight=100, background_fade=20, OSD_Timeout), gears viewtypes
(movies/tvshows=53, seasons=52, episodes=529, menus=50) via new policy entries
(+AF3 viewtypes parity), home-menu icons in the overlay's mainmenu.DATA.
NOT done for Zephyr (skipped due to the python crash — see [[kodi-python-crash]]):
maintenance home category + font-picker idloc check.

Estuary changes made (live on the install, pending ship):
1. **תחזוקה (Maintenance) home category** — a new home menu item `mk_services`
   (label תחזוקה) + submenu panel `9151` in `addons/skin.estuary/xml/Home.xml`, with
   4 actions: Gears cache clear `RunPlugin(plugin://plugin.video.gears/?mode=clear_all_cache)`,
   GearsAI cache clear `RunScript(special://home/addons/service.subtitles.gearsai/
   resources/modules/clean_cache_functions.py,clean_all_cache)`, send logs
   `?mode=send_logs`, quick update `?mode=check_updates` (all wizard modes exist in
   default.py). Other skins already have these in their power menus — but Asaf wants
   them as a home CATEGORY.
2. **Debrid/TorBox category renamed** `דבריד` -> **`חיבור שירותים`** (Connect Services,
   general — not TorBox-specific). The submenu keeps the TorBox connect/info/revoke items.
3. **Subtitle font default -> Rubik** (guisettings `subtitles.fontname`). NOT shipping
   device-name (per-device) or pictures.displayresolution (incidental).

Open Estuary item: the SKIN UI font. Estuary's Hebrew fontset is "מבוסס אריאל"
(Arial-based) — Asaf flagged it (Settings > Interface > Skin > Fonts). Zephyr already
got a nicer Hebrew font (Rubik, task #6). Consider a nicer Hebrew UI font for Estuary
too. Local-first: test on the install, ship only after Asaf OKs ([[local-first-workflow]]).
