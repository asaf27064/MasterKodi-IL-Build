# af3-pov (2026-07-21)

Arctic Fuse 3 on POV. AF3 does NOT crash (15-widget home tested stable) ->
plain Gears->POV shortcut translation, NO tmdb architecture.

## Contents
- nodes/  -- 5 translated skinvariables node JSONs (home/1101/1102 widgets,
  search widgets, power menu) = the SOURCE menu config
- skin-overrides/  -- the COMPILED output includes
  (script-skinvariables-generator-includes-.xml: 71 widget paths;
  script-skinviewtypes-includes.xml). BOTH nodes AND compiled includes are
  translated so the home renders POV regardless of whether the skinvariables
  generator re-runs (avoids the buildtemplate empty-category trap).

Action renames: in_theaters->premieres, oscar_winners->trakt_movies_most_watched
(label 'הנצפים ביותר'); networks/torbox folders -> POV shortcut-folder browse;
power cache label -> 'ניקוי קאש POV' (GearsAI kept).

## Depends on POV navigator.db folders: SELECTED NETWORKS (11) + Connect Services.

## Notes
- AF3 ratings use native ListItem.Rating + TMDbHelper.ListItem.* service props
  (addon-agnostic) -- NO gears rating-props port needed (unlike Estuary).
- If the home shows EMPTY widgets, AF3's skinvariables generator needs a forced
  rebuild (buildtemplate) -- but both nodes+includes are pre-translated so this
  should not happen.
- Config re-applies on skin-switch (wizard baseline) -> sticks as shipped
  baseline; manual test: switch via Kodi settings.
