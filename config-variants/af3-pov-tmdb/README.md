# af3-pov-tmdb (2026-07-21)

Arctic Fuse 3 with TMDb-service widgets + POV content addon. AF3 does NOT
crash on Kodi 21, so af3-pov (plain POV widgets) is the tested default; THIS
variant is the crash-free-architecture option (built on request, parity with
zephyr-pov-tmdb).

## Contents
- nodes/  -- AF3 skinvariables nodes, content widgets -> TMDbHelper:
  trending->trakt_trending, in_theaters/premieres->now_playing,
  blockbusters->revenue_movies, most_watched->trakt_mostviewers,
  genres->info=genres, tv premieres->airing_today, upcoming->upcoming,
  SELECTED NETWORKS->info=dir_custom_node. KEPT on POV: Continue Watching
  (no TMDbHelper local resume), search, Connect Services, power cache.
- themoviedb/  -- settings.xml, players/pov.json (POV player),
  nodes/SELECTED NETWORKS.json (custom node)

## Apply (IMPORTANT -- differs from af3-pov)
Includes are NOT pre-translated here. Seed the nodes, then FORCE the AF3
skinvariables generator to rebuild the compiled includes from them
(buildtemplate) -- else the home renders the old includes / empty categories.
Also install themoviedb/ (settings + player + custom node) and ensure genre +
network icons are in portable_data/media/ (see zephyr-pov-tmdb).
NOT LIVE-TESTED on the box (box runs af3-pov); node mapping mirrors the
tested zephyr-pov-tmdb vocabulary.
