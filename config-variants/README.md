# Zephyr widget-source variants (STAGING — not wired into the build yet)

Purpose: Zephyr's home crashes on Kodi 21.3 (upstream CPythonInvoker race) come
from spawning one Python interpreter per Gears widget on every Home load. Moving
the home widgets to tmdbhelper (which serves widget content from a persistent
service, not per-widget interpreters) removed the crash in live testing. These
variants let us ship/toggle the widget SOURCE without losing any option.

Each variant is a self-contained bundle of the three file groups that define the
home experience:

| group | files | config_policy dest |
|---|---|---|
| menu (widgets) | skinshortcuts/*.DATA.xml (+ .properties) | userdata/addon_data/script.skinshortcuts/ |
| skin settings | skin.zephyr/settings.xml | userdata/addon_data/skin.arctic.zephyr.2.resurrection.mod/settings.xml |
| widget-engine settings | themoviedb/settings.xml (+ nodes/, players/) | userdata/addon_data/plugin.video.themoviedb.helper/ |

## Variants
- **zephyr-gears/** — the ORIGINAL shipped state: home widgets are Gears
  (build_movie_list/build_tvshow_list). Preserved as the safe fallback.
- **zephyr-tmdb/** — the experiment: home widgets are tmdbhelper (trending_week,
  now_playing, upcoming, airing_today, SELECTED NETWORKS node) + a players/
  gears.json bridge so playback still routes through Gears' scraper +
  tmdbhelper language he-IL. Snapshot when Asaf declares the config final.
- **zephyr-pov/** — PLACEHOLDER for the future POV-based build. Same structure;
  widgets will point at plugin.video.pov once POV is integrated. Asaf brings the
  clean POV files; we wire its update source + Hebrew mod like Gears.

## How this will wire into the build (design, NOT yet implemented)
config_policy.json / the wizard gets a "widget_variant" selector. The wizard
applies the chosen variant's three file groups on install/update. Default =
whatever we decide per skin/per Kodi version (21 may default tmdb to dodge the
crash; 22 may keep Gears if its invoker fix holds). Applies to BOTH fleets.

STATUS: staging only. zephyr-gears preserved from the shipped config. zephyr-tmdb
to be snapshotted from Asaf's box when he says the tmdb config is final. Nothing
here changes device behavior until the wizard selector is built + shipped.
