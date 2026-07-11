---
name: masterkodi-il-builder
description: Use this skill whenever Asaf is working on the MasterKodi IL Hebrew Kodi build ecosystem. Triggers include any mention of: plugin.video.gears (The Gears addon), Gears Hebrew mod, kodirdil module, chainsrepo / unhingedthemes, FenLight_Estuary base build, Arctic_Fuse_Skin advanced skin, MasterKodi IL Wizard, plugin.program.masterkodi.il.wizard, service.kodi.il.firstrun, repository.masterkodi.il, building the EXE/APK installers, deploying to GitHub Releases on asaf27064/asaf27064.github.io, pushing the wizard to its repo, the pov-modified-heb repo, Hebrew subtitle integration via Ktuvit/Wizdom/OpenSubtitles, Magneto scraper provider, TorBox/Real-Debrid integration in the build, or maintaining the FenLight→Gears migration. Also trigger when the user mentions diffing Kodi addon ZIPs, checking for upstream Gears updates, updating Kodi DB files (Addons33.db, ViewModes6.db, settings.db, navigator.db) for the Hebrew build, or editing skinvariables shortcut JSONs. This skill supersedes the older fenlight-hebrew-mod-updater skill — FenLight is dead, Gears is the fork now used.
---

# MasterKodi IL Builder

Maintains the **MasterKodi IL** Hebrew Kodi build ecosystem maintained by Asaf (`asaf27064` on GitHub). The ecosystem replaces the abandoned FenLight addon with its fork **The Gears** (`plugin.video.gears`).

## What this skill covers

Four cooperating pieces in `C:\Users\asaf2\Desktop\kodi_project\`:

| Folder | Role |
|---|---|
| `pov-modified-heb/` | The Hebrew-mod source-of-truth git repo. Holds `gears_hebrew_subtitles.zip`, `pov_hebrew_subtitles.zip`, `skin_hebrew_files.zip` plus their `*_version.json` companions and the GitHub Action that watches upstream. |
| `FenLight_Estuary/` | Working tree of the **base build** the wizard installs — Estuary skin + plugin.video.gears + Hebrew overlay + supporting modules. Contains `addons/`, `userdata/`, `media/`. |
| `Arctic_Fuse_Skin/` | Working tree of the **advanced skin** the wizard installs as an optional layer atop the base build. Contains its own `addons/skin.arctic.fuse.3/` + skinvariables shortcuts. |
| `MasterKodi_Build/` | The build pipeline: `build_masterkodi_all.py`, the wizard/firstrun/repo addon zips, the Inno Setup `.iss` template (Windows), and the apktool-based APK builder (Android). |

Plus two reference zips at the repo root:
- `plugin.video.gears-2.0.7.zip` — clean upstream Gears (input)
- `repository.chainsrepo-0.0.15.zip` — clean chainsrepo (input)

## Mental model

The Hebrew mod is an **overlay** of a small number of files on top of clean Gears. When upstream changes, only re-patch the files that actually contain Hebrew modifications — not the whole addon. Out of the FenLight files that the legacy Hebrew mod used to ship, **only three Python files plus `kodirdil/` plus `settings_manager.xml`** actually carry Hebrew-specific code. The rest were stale upstream copies that would silently downgrade Gears if shipped.

```
Hebrew overlay (the only files that get shipped in gears_hebrew_subtitles.zip):
  resources/lib/kodirdil/                           (entire self-contained Hebrew subs module)
  resources/lib/modules/sources.py                  (kodirdil import + scrape-thread injection)
  resources/lib/windows/sources.py                  (Hebrew filters, panel text, per-source matching, SDR-only filter, tried-source tracking)
  resources/lib/caches/settings_cache.py            (10 hebrew_subtitles.* settings appended)
  resources/lib/apis/tmdb_api.py                    (substitutes gears.meta_language into 4 TMDb URLs — gives Hebrew synopses for movies/shows/seasons/collections)
  resources/lib/apis/torbox_api.py                  (DROPPED as of Gears 2.2.2 — upstream now has native TorBox QR/device flow; we use upstream's as-is, do NOT overlay)
  resources/lib/indexers/movies.py                  (pre-fetches OMDB + sets gears.<rating>_rating listitem props)
  resources/lib/indexers/tvshows.py                 (same pattern as movies.py)
  resources/lib/indexers/navigator.py               (per-genre icons via i.get('icon', 'genres'))
  resources/lib/modules/meta_lists.py               ('icon': 'genre_X' field on every genre dict)
  resources/lib/service.py                          (TorBoxSubscriptionCheck class — Hebrew banner with days-remaining on startup)
  resources/skins/Default/1080i/settings_manager.xml (Hebrew Subtitles submenu id=90)
  resources/media/icons/*.png                       (~102 in-addon icons sourced from Tikipeter fenlight mirror — overrides Gears defaults)
  resources/media/network_icons/*.png               (76 network logos: Netflix, Disney+, HBO, etc. Gears doesn't ship these.)
  version.txt                                       (must match gears_version.json "version")
```

Anything else (`modules/metadata.py`) **must not** be in the overlay — they have zero Hebrew code in any version we shipped, and including stale copies will break Gears.

(As of 2.0.7 `service.py` IS legitimately patched — we add a `TorBoxSubscriptionCheck` class that shows a Hebrew toast on addon startup with days remaining on the user's TorBox subscription. The patch is layered on top of clean gears 2.0.7 service.py, not a stale FenLight copy.)

**Scanning gotcha (learned 2026-05-15):** the FenLight Hebrew patches use **uppercase** `KODIRDIL` as their comment marker and helper functions like `get_meta_language()`/`extra_ratings`/`tmdb_rating` — they don't necessarily contain the lowercase strings `kodirdil`, `hebrew`, or `is_hebrew`. When auditing for patches, grep for `KODIRDIL` (case-sensitive) too.

## Decision flow: which task is the user asking for?

Match the user's intent to one of these workflows and read the matching reference file. Don't try to hold every detail in head — each reference is self-contained.

| Intent | Reference |
|---|---|
| Upstream Gears released a new version, update the Hebrew mod to match | `references/hebrew-mod.md` |
| Rebuild `gears_hebrew_subtitles.zip` from a working tree | `references/hebrew-mod.md` + `scripts/rebuild_hebrew_zip.py` |
| Update / sync the `FenLight_Estuary/` base build (sources.xml, favourites.xml, DBs, addon_data) | `references/base-build.md` |
| Update `Arctic_Fuse_Skin/` (skinvariables shortcuts, hub widgets, skin XML) | `references/arctic-fuse.md` |
| Run a full build + deploy (EXE + APKs + upload + push wizard) | `references/wizard-build.md` |
| Understand/modify the wizard's UPDATE engine, self-update, menus, backup, or how updates propagate to users (what's automatic vs needs an EXE/APK rebuild) | `references/wizard.md` |
| Build the release zips (`FenLight_Estuary.zip`, `Arctic_Fuse_Skin.zip`) for the v1.0 release | `references/release-packaging.md` + `scripts/build_release_zips.py` |
| Debug a build / deploy failure on Windows | `references/gotchas.md` |
| Base addon died/forked — re-home the Hebrew mod onto a new base | `references/migration.md` |
| Maintain the AI Subs addon (service.subtitles.gearsai) + community pool + wizard self-updater | `references/ai-subs.md` |
| Add/modify a TorBox or Real-Debrid shortcut in the build | `references/base-build.md` (favourites.xml + navigator.db shortcut folder sections) |

## Critical constants (memorize these)

URLs and paths the rest of the ecosystem depends on. If any of these change, multiple files break.

```
# Upstream gears source of truth (single endpoint)
GEARS_ADDONS_XML  = https://raw.githubusercontent.com/unhingedthemes/zips/main/_zips/addons.xml
GEARS_ZIP_FMT    = https://raw.githubusercontent.com/unhingedthemes/zips/main/_zips/plugin.video.gears/plugin.video.gears-{version}.zip

# Hebrew mod source of truth (where the wizard fetches from at runtime)
HEB_REPO          = https://github.com/asaf27064/pov-modified-heb
HEB_RAW_BASE      = https://raw.githubusercontent.com/asaf27064/pov-modified-heb/main
HEB_VERSION_JSON  = {HEB_RAW_BASE}/gears_version.json
HEB_ZIP           = {HEB_RAW_BASE}/gears_hebrew_subtitles.zip

# Releases + GitHub Pages
PAGES_REPO        = https://github.com/asaf27064/asaf27064.github.io
WIZARD_PATH       = zips/plugin.program.masterkodi.il.wizard/   (inside PAGES_REPO)
RELEASES_BASE     = https://github.com/asaf27064/asaf27064.github.io/releases/
BUILD_TXT         = https://asaf27064.github.io/assets/build.txt  (wizard reads this on first-run)

# Addon IDs
WIZARD_ID         = plugin.program.masterkodi.il.wizard
FIRSTRUN_ID       = service.kodi.il.firstrun
REPO_ID           = repository.masterkodi.il
GEARS_ID          = plugin.video.gears
CHAINSREPO_ID     = repository.chainsrepo
GEARSSCRAPERS_ID  = script.module.gearsscrapers   (required dependency)
```

## Universal invariants — never violate these

1. **Setting key prefix is `gears.*` not `fenlight.*`.** Kodi exposes settings to window properties as `<addon_id_basename>.<setting_id>`. Inside Gears that's `gears.X`. Any file that reads `get_setting('gears.hebrew_subtitles.enable_matching')` is correct; `fenlight.*` is wrong and will silently return empty.

2. **Setting IDs themselves don't carry the prefix.** When defining a setting in `settings_cache.py` you write `{'setting_id': 'hebrew_subtitles.enable_matching', ...}` — no `gears.` prefix.

3. **Version triple stays in sync.**
   - `pov-modified-heb/gears_version.json` `"version"` field
   - `version.txt` inside `gears_hebrew_subtitles.zip`
   - The wizard setting `gears_hebrew_version` set on successful install

4. **Don't ship `auto_update`-friendly origin.** All three Hebrew-build addons (`plugin.video.gears`, `repository.chainsrepo`, `script.module.gearsscrapers`) must have `installed.origin=''` and `update_rules.updateRule=2` (Never) in `Addons33.db`, so the wizard controls updates and Kodi doesn't fight it.

5. **Release zips have flat structure.** `FenLight_Estuary.zip` and `Arctic_Fuse_Skin.zip` must have `addons/`, `userdata/`, `media/` at the root — **not** wrapped in a `FenLight_Estuary/` folder. The wizard's `extract_zip()` calls `zin.extract(item, HOME)` where HOME is Kodi's home dir.

6. **No FenLight residue.** Search the working tree for `fenlight`, `FenLight`, `FenlightAnonyMouse`, `plugin.video.fenlight` before declaring a migration done. Acceptable exceptions: changelog text in version JSONs, doc/skill names referencing the migration.

## When in doubt

Before writing changes:
1. Read the relevant `references/*.md`. They have the file-by-file detail this top-level doc deliberately omits.
2. Check the current state on disk — the working tree drifts and the user often re-runs things.
3. For destructive actions (git push, gh release upload, deploying), ask the user before running. Use `AskUserQuestion` with the exact command listed.

## Scripts shipped with this skill

| Script | Purpose |
|---|---|
| `scripts/rebuild_hebrew_zip.py` | Rebuild `gears_hebrew_subtitles.zip` from a working tree of the overlay files. Bumps `version.txt` to match a target version. |
| `scripts/build_release_zips.py` | Build `FenLight_Estuary.zip` and `Arctic_Fuse_Skin.zip` with the flat layout the wizard expects (excludes `service.subtitles.gearsai/cloudflare/`). |
| `scripts/publish.py` | One-command AI Subs release: rebuild `gearsai_subtitles.zip` into pov-modified-heb (cloudflare-excluded + asserted), bump `gearsai_version.json`, `--reinject` rebuilds `FenLight_Estuary.zip`, `--push`/`--upload` deploy. |
| `scripts/scan_residual_fenlight.py` | Audit all four working trees + DBs for residual `fenlight` references. |

Run them with `python <script>` — they're idempotent and print what they did.
