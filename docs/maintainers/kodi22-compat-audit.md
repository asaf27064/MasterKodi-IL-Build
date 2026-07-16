# Kodi 22 (Piers) compatibility audit — everything we run on Omega, checked

*Audited 2026-07-16 against Kodi 22.0 Beta 1, using static py3.14 scans of every
addon in the build plus a live sandbox (`C:\KodiPiersTest` on Asaf's PC: Kodi 22
portable + a full clone of the real install, debug-logged sessions with Gears
browsing, wizard service, subtitles services and skinshortcuts all running).*

## Kodi 22 runtime facts

| Fact | Omega (21.3) | Piers (22.0 b1) | Impact |
|---|---|---|---|
| Python | 3.8 | **3.14** | stdlib removals (see findings) |
| GUI API (`xbmc.gui`) | 5.17 | **5.18** | skins must declare 5.18 — Omega skins DON'T LOAD |
| Python API | 3.0.x | 3.0.x line | backwards-compatible — python addons load fine |
| `Addons33.db` | 33 | **33 (same)** | wizard's direct DB writes still valid ✓ |
| `ViewModes6.db` | 6 | **6 (same)** | view merges still valid ✓ |
| Textures / MyVideos / MyMusic / TV / Epg | 13/131/83/46/16 | 14/146/84/48/21 | migrated automatically; wizard uses `Addons*.db`/`Textures` prefix globs — no pins break ✓ |
| Windows installer | kodi-21.3-Omega-x64.exe | kodi-22.0-Piers_beta1-x64.exe (official) | EXE pipeline portable |
| Android arm64 | releases/android/arm64-v8a | ✓ beta1 exists | APK pipeline portable |
| Android 32-bit (Xiaomi!) | releases/android/armeabi-v7a | ✓ beta1 exists — **dir renamed to `releases/android/arm/`** | Xiaomi CAN go to 22 |
| Userdata migration | — | Omega userdata migrated cleanly in sandbox (settings, DBs, gears data) | in-place upgrade viable |

## Python addons — the Gears stack

Scanned with the exact interpreter Kodi 22 uses (python 3.14): AST-parse of every
`.py` + detection of stdlib modules removed since 3.8. Sandbox sessions produced
**zero tracebacks/deprecations** from the whole stack.

| Addon | Verdict | Detail |
|---|---|---|
| plugin.video.gears (incl. full Hebrew overlay: kodirdil, tmdb_api, indexers, service) | **CLEAN** (105 files) | also ran live in sandbox: navigator, widgets, metadata all functional |
| script.module.gearsscrapers | **CLEAN** (64 files) | |
| plugin.program.masterkodi.il.wizard | **CLEAN** (12 files) | service ran live in sandbox (incl. pending_view_rebuild flow) |
| service.kodi.il.firstrun / skipintro / script.skinhelper / localsubtitle | **CLEAN** | |
| service.subtitles.gearsai | **CLEAN (fix applied)** | vendored `httpx` `import cgi` replaced inline in `_models.py`. Deep scan note: vendored `sockshandler.py` calls `ssl.match_hostname` (removed 3.12) but it's a dead path — only reachable via yandexfreetranslate SOCKS proxy, which gearsai never configures (engine.py does yandex with its own HTTP calls; `set_proxy` never called). Identical dead path on Omega. |
| service.subtitles.all_subs_plus | **CLEAN (fix applied, +2 tolerated files)** | `aa_subs.py` `cgi.parse_header` replaced. `zfile.py`/`zfile_18.py` have py2 syntax; `zfile_18` IS imported (service.py:676) but inside `try/except` with stdlib `zipfile` fallback — equally broken-and-harmless on Omega py3.8, not a Piers regression. |
| script.module.simplejson | OK | `import imp` is inside a py2-only guard; and only requests' optional try/except touches simplejson |
| All other script.module.* (requests, urllib3, bs4, six, qrcode, pysubs2, …) | **CLEAN** | full scan, no removed modules, no syntax errors |
| resource.language.he_il 11.0.79 | **REPLACE in manifest-v22** | it's the Kodi-21 translation set; installs on 22 (requires only kodi.resource 1.0.0) but new K22 core strings show English. Decision (Asaf): ship the Piers he_il 12.x pack in manifest-v22. |

*Deep removed-API scan (2026-07-16, all 11 of our own addons, 865 files — removed
NAMES inside living modules, not just removed modules): every hit triaged safe
(version-guarded try/except, py2-only branches, docstrings, or the gearsai
sockshandler dead path above). Full dependency-graph resolution of all 57 manifest
addons against Kodi 22 core+bundled: 100% resolve, zero version violations.*
| script.skinshortcuts 2.0.3 | **PROVEN LIVE** | compiled the full menu on Kodi 22 in 8s, no errors |
| script.skinvariables 2.2.2 | **PROVEN LIVE** | buildviews/buildtemplate ran fine |

## Skins — the hard part

GUI API is NOT backwards-compatible: an Omega skin (xbmc.gui 5.17) will not load
on Piers. Ecosystem state (July 2026):

| Skin | Piers version exists? | Port effort |
|---|---|---|
| **Zephyr (resurrection.mod)** | ✓ DenDyGH ships Piers 1.1.9 in the same releases as Omega 1.0.51 | Our overlay does NOT transplant: Omega-era 1080i layout files break the Piers skin (verified — chimera renders no widgets), and even pure-Piers + minimal overlay (fonts/he_il/Font.xml) left widgets empty — Piers home consumes NEW compiled-include names (`skinshortcuts-template-widgets-submenu`, `skinshortcuts-mainmenu-submenu`) and the widget wiring differs. Needs a real per-file port + re-harvested menu bundle. **Biggest single work item.** |
| **AF3** | ✗ — jurialmunkey has ONLY an `omega` branch; newest 3.2.13 (Jul 2026) declares gui 5.17 | Blocked on upstream. Watch for a Piers branch/release. |
| **Nimbus** | ✗ — master declares gui 5.17 | Blocked on upstream. |
| **Estuary** | Kodi 22 ships its OWN Estuary (new-generation, differs from 21's) | Our Estuary mod (hardcoded menu/widgets in 21's skin.estuary) must be re-applied onto 22's Estuary — a re-mod, not a copy. Per Asaf: do this LAST, after everything else is safely on Piers. |

**Consequence:** an initial Piers build can offer Zephyr (after the overlay port)
and stock-or-lightly-modded Estuary only. AF3/Nimbus join when upstream ships
Piers versions (our upstream-watch already monitors jurialmunkey).

## Delivery/infra checklist for the 22 track

- New workflows alongside existing ones (`build-exe-v22`, `build-apk-v22`),
  publishing to a **separate Asaf-only release** (not linked on the download
  page); `build-inputs-v22` for the 22 base binaries (Windows beta1 exe files,
  arm64+arm APKs).
- Wizard: works as-is (proven), but review `filecache` JSON-RPC setting ids and
  any guisettings ids the config ships against 22's settings schema (sandbox
  migrated cleanly, but config seeds target fresh installs too).
- Menu bundle (`resources/menu_defaults/`) is compiled-for-Omega — must be
  re-harvested from a working Piers Zephyr.
- Keymaps, advancedsettings: carried over fine in the sandbox.
- inputstream.adaptive: Kodi 22 ships its own (sandbox had it) — verify playback.

## Bottom line

The **entire python stack is essentially Piers-ready** (two one-line `cgi` fixes
in the subtitles services; everything else clean and live-proven). The real
migration work is **skins**: port the Zephyr overlay to the Piers skin
(unresolved widget wiring), wait on upstream for AF3/Nimbus, and re-mod Estuary
on 22's new skin at the end.
