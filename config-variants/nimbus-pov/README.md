# nimbus-pov (2026-07-21)

Nimbus on POV. Nimbus does NOT crash (widget home tested stable, unlike
Zephyr) -> pure Gears->POV shortcut translation, NO tmdb architecture.

## Contents
- skin-overrides/  -- 9 translated skin XMLs (main_menu + widget templates,
  search dialog, Variables_Search, power menu). Search mode renames:
  history.search->search_history, person_direct.search->person_search,
  tmdb_movies_search_sets->tmdb_movies_search_collections; imdb-keyword
  search -> POV title search (POV has no imdb-keyword flow); networks/torbox
  folders -> POV shortcut-folder browse; power cache label -> POV.
- nimbus/cpath_seed.json  -- the COMPILED menu config (script.nimbus.helper
  cpath_cache.db custom_paths): the actual home render source, 14 entries,
  12 gears->pov. Re-apply: UPDATE custom_paths per row.

## Depends on POV navigator.db folders (shared with other pov variants):
SELECTED NETWORKS (11) + Connect Services -- seeded from zephyr-pov.

## Notes
- Nimbus ratings are self-contained (nimbus.*Rating via script.nimbus.helper)
  -- no gears/pov ratings port needed (unlike Estuary).
- cpath_cache is the live render source; editing it directly avoids the
  template-buildtemplate rebuild dance. If widgets show empty, force a
  Nimbus menu rebuild.
- Config re-applies on skin-switch (wizard baseline) -> sticks only as
  shipped baseline; for manual test switch via Kodi settings.
