# estuary-pov (FINAL clean, 2026-07-21)

Complete Estuary-on-POV config, install-fresh. Estuary is favourites+skin-XML
driven (no skinshortcuts, no tmdb layer per Asaf -- direct POV).

## Contents
- favourites.xml  -- 23 shortcuts, all POV (TorBox->myservices, networks
  key_id->network_id, trending/in-progress/next/genres/cache same modes)
- skin-overrides/  -- Home.xml / Includes.xml / Custom_1107_SearchDialog.xml
  / DialogButtonMenu.xml: all gears refs -> POV; ratings-flag images ->
  POV gears_flags path; cache-tile labels -> 'ניקוי מטמון POV'
- pov/shortcut_folders.json  -- SELECTED NETWORKS (11) + Connect Services,
  seed into POV navigator.db
- pov/views.json  -- Poster (51) for movies/tvshows, 55 elsewhere; seed into
  POV views.db (loads at POV startup)
- pov/settings.xml  -- POV addon settings (credential-free)

## POV overlay pieces (in overlays-staging/plugin.video.pov, ship with overlay)
- modules/kodirdil_ratings.py + movies.py/tvshows.py patches: per-item
  gears.*_rating props from cached OMDb (Estuary/AF3 rating flags)
- resources/settings.xml: kodirdil.extra_ratings + omdb_api_key rows
- resources/skins/Default/media/gears_flags/ (rating flag icons)

## IMPORTANT (ship note)
Favourites + skin config are RE-APPLIED by the wizard on skin-switch / update
-- they revert to the Gears BASELINE. This estuary-pov config only STICKS when
it becomes the shipped baseline (wire into wizard config-apply). For manual
testing: switch skins via Kodi settings, not the wizard, or it re-clobbers.

## Crash note
Estuary home is favourites-driven (no addon-widget storm) -- the config that
does NOT trigger task #10's crash.
