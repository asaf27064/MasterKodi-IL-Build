# Skin config variants (STAGING — not wired into the build yet)

Purpose: Zephyr's home crashes on Kodi 21.3 (upstream CPythonInvoker race) come
from spawning one Python interpreter per addon widget on every Home load. Moving
the home widgets to tmdbhelper (which serves widget content from a persistent
service, not per-widget interpreters) removed the crash in live testing. These
variants capture, per skin, the widget SOURCE + content addon combinations so we
can ship/toggle without losing any option.

## Naming
`<skin>-<content-addon>[-tmdb]`:
- `<content-addon>` = **gears** or **pov** (who resolves/plays + serves list content)
- `-tmdb` suffix = home WIDGETS are tmdbhelper service widgets (crash-free);
  without it, widgets are the content addon's own (per-widget interpreter =
  crash-prone on Kodi 21.3 widget-heavy homes)

## Crash verdicts (tested live, Kodi 21.3, 2026-07-21)
- **Zephyr** — CRASHES (widget storm) → needs a `-tmdb` variant
- **Estuary / Nimbus / AF3** — STABLE on plain addon widgets → no tmdb needed
- Signature (all crashes): EXCEPTION_ACCESS_VIOLATION python3.8.dll+0x1c6744

## Variants
| variant | skin | content addon | widgets | notes |
|---|---|---|---|---|
| zephyr-gears | Zephyr | Gears | Gears | ORIGINAL shipped state; safe fallback (crashes) |
| zephyr-gears-tmdb | Zephyr | Gears | tmdbhelper | crash-free; players/gears.json bridge |
| zephyr-pov | Zephyr | POV | POV | POV alternative (still crashes — widget home) |
| zephyr-pov-tmdb | Zephyr | POV | tmdbhelper | crash-free; players/pov.json; THE tested Zephyr pick |
| estuary-pov | Estuary | POV | favourites/skin XML | stable; ratings port (gears.* props via OMDb) |
| nimbus-pov | Nimbus | POV | cpath compiled | stable; +TV genres widget; self-contained ratings |
| af3-pov | AF3 | POV | skinvariables nodes | stable; nodes+includes translated |
| af3-pov-tmdb | AF3 | POV | tmdbhelper | on-request; NODES ONLY, needs generator rebuild |

## File groups per variant
| group | files | apply dest |
|---|---|---|
| menu/widgets | skinshortcuts/ or nodes/ or favourites.xml or skin-overrides/ | per skin (skinshortcuts / skinvariables nodes / userdata / skin xml) |
| skin settings | skin.zephyr/settings.xml | the skin's addon_data settings.xml |
| widget engine (tmdb only) | themoviedb/ (settings + nodes/ + players/) | userdata/addon_data/plugin.video.themoviedb.helper/ |
| POV seeds (pov only) | pov/ (shortcut_folders.json, views.json, settings.xml) | POV navigator.db / views.db / settings |

Icon sets (genre_icons, network_icons) apply to **portable_data/media/**
(= special://home in portable Kodi — NOT the install root).

## Ship note
Favourites + skin config are RE-APPLIED by the wizard on skin-switch / update
(revert to the Gears baseline). A variant only STICKS as the shipped baseline —
so wiring these into the wizard's config-apply is the mechanism. For manual
testing, switch skins via Kodi settings, not the wizard.

## Kodi 22 / Piers
The crash is 21.3-specific. Piers likely needs NO widget-architecture change —
test whether it crashes with Gears widgets before porting any tmdb/pov variant
there. Addon-side fixes (GearsAI, wizard) already ship to both fleets.

STATUS: staging only. Nothing here changes device behavior until the wizard
variant-selector is built + shipped.
