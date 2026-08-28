# rounded-pov-tmdb — Arctic Zephyr Rounded, TMDb Helper widgets

Vendored skin (`skin.arctic.zephyr.rounded`, upstream 1.3.00 from
Nanomani/repository.omega.nanomani). The skin itself is UNMODIFIED so it keeps
auto-updating through `tools/adopt_deps.py`; everything we add lives here.

## Contents
- `skinshortcuts/` — the full menu set. Filenames carry the `skin.arctic.zephyr.rounded-`
  prefix because the skin declares `<doNotShareMenu />` in `shortcuts/overrides.xml`,
  so skinshortcuts namespaces every group per skin.
  Categories: srtym (movies), sdrvt (tv), 137 (search), hybvrshyrvtym (services),
  thzvkh (maintenance), hhlptskyn (skin switch), 5 (settings), 13012 (exit),
  plus powermenu and searchmenulist.
  Content rows live in the `-1` submenu files; the mainmenu entries are categories.
- `skin.rounded/settings.xml` — a PARTIAL seed of skin settings (ratings, clearlogo).
  Merged per id by the wizard; the user's other settings are never touched.
- `pov/` — POV seeds (settings, navigator shortcut folders, views). Byte-identical
  to zephyr-pov. POV reads shortcut folders with `json.loads`, so these stay JSON.
- `themoviedb/` — TMDb Helper config incl. the OMDb/MDbList keys. Required even in
  the POV-widget build: every rating except IMDb comes from TMDb Helper's ListItem
  monitor, which resolves POV items too.
- `media/` — genre + network icons.

## Ratings (why they were blank until 2026-08-29)
The data was always being fetched. The skin hides its whole media-flags row on the
home screen unless `hide.furniture.flags.vertical.widgets` is false, and the skin
defaults it to true while we ship `home.vertical`. The seed above sets it false.
Requires `enabled.tmdb.helper.service` (skin) and `use_online_ratings` (TMDb Helper).

## Not shipped
- View types: the skin keeps its own `shortcuts/skinviewtypes.json` (34 views).
  Overriding it would mean patching a skin file, i.e. an overlay. Left alone
  pending Asaf's choice of view.
- Hebrew: upstream ships 52/639. A complete translation is prepared but held in
  `translations/skin.arctic.zephyr.rounded/` and deliberately NOT applied, because
  applying it makes the skin a modified dep and ends auto-update.
