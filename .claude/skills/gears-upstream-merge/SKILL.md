---
name: gears-upstream-merge
description: Re-base the Hebrew overlay onto a new upstream release of The Gears (plugin.video.gears). Use when unhingedthemes ships a new Gears version, or when check_upstream reports plugin.video.gears out of date.
---

# Gears Upstream Re-merge

Re-bases the Hebrew overlay in `C:\Users\asaf2\Desktop\kodi\MasterKodi-IL-Build`
onto a new upstream release of **The Gears**. Same flow as
[pov-upstream-merge](../pov-upstream-merge/SKILL.md) — only the constants and
the overlaid file list differ. This exact flow shipped overlay 2.2.11 (Gears
2.4.0) with zero regressions.

Gears is Asaf's **primary** content engine; POV is the alternative. Both ship in
every build and both fleets (Kodi 21 Omega + Kodi 22 Piers) get the same addon,
so a mistake here reaches every user on every device.

## Constants

```
REPO       = C:\Users\asaf2\Desktop\kodi\MasterKodi-IL-Build
OVERLAY    = overlays/plugin.video.gears        (files/ = source of truth)
ADDON      = addons/plugin.video.gears          (mirror: clean base + overlay)
ADDONS_XML = https://raw.githubusercontent.com/unhingedthemes/zips/main/_zips/addons.xml
ZIP        = https://raw.githubusercontent.com/unhingedthemes/zips/main/_zips/plugin.video.gears/plugin.video.gears-{version}.zip
LIVE       = C:\MasterKodi IL\portable_data\addons\plugin.video.gears
```

**Upstream keeps ONLY the latest zip.** That is why the clean base of the
version we ship is committed at `base_zip_local`. It is the 3-way-merge
reference — never edit it, never delete it before the new one is committed.

Note: as of 2.4.0 `plugin.video.gears` is **absent from that addons.xml** (it
lists genocide/zoro/gearsscrapers etc.). Do not conclude "no update" from the
addons.xml alone — `python tools/check_upstream.py` is authoritative and knows
where to look.

## The 10 files we patch

Everything else in the addon is clean upstream. Verify this list against
`python tools/verify_overlay_merge.py plugin.video.gears`, which prints it.

```
resources/lib/caches/settings_cache.py      baked build defaults + 10 hebrew_subtitles.* settings
resources/lib/service.py                    DebridSubscriptionCheck (Hebrew days-remaining banner)
resources/lib/modules/sources.py            Hebrew-subs thread + GearsAI prefetch at top of get_sources
resources/lib/windows/sources.py            Hebrew filters, per-source matching, SDR filter, tried-source badge
resources/lib/apis/tmdb_api.py              meta_language substituted into 4 TMDb URLs (Hebrew synopses)
resources/lib/indexers/movies.py            OMDb prefetch + gears.<rating>_rating listitem props
resources/lib/indexers/tvshows.py           same pattern as movies.py
resources/lib/indexers/navigator.py         per-genre icons via i.get('icon', 'genres')
resources/lib/modules/meta_lists.py         'icon': 'genre_X' on every genre dict
resources/skins/Default/1080i/settings_manager.xml   Hebrew Subtitles submenu (id=90)
```

Plus 131 files that are **ours alone** and never conflict:
`resources/lib/kodirdil/` (10), `resources/media/icons/` (44),
`resources/media/network_icons/` (76), `version.txt`.

**Baked defaults live in `settings_cache.py`** and WILL look like "upstream
lines lost" in a merge audit. They are intentional — check each against the
upstream line before worrying:
`max_threads` 100, `omdb_api` key, `external.cache_check` true,
`results.timeout` 12, `results.auto_rescrape_cache_ignored` 0,
`autoscrape_next_episode` true.

## Workflow

### 1. Recon
`python tools/check_upstream.py` — reports the new version AND names the
overlaid files upstream touched. That list is what you merge; the rest carries
forward untouched.

### 2. Download, extract, diff
Work in the scratchpad. Extract the committed old base zip and the new zip to
`old/` and `new/`. **Pass Windows-style paths to python** — a Git-Bash `/c/...`
path silently extracts nowhere.

**Before diffing, check line endings on all three sides** (see Traps):
```python
b = open(P, 'rb').read(); print('CRLF' if b.count(b'\r\n') else 'LF')
```
Then `diff -rq old/plugin.video.gears new/plugin.video.gears` for the change
surface, and read `changelog.txt`.

### 3. 3-way merge (never hand-port)
Normalise ours/base/new to the NEW upstream convention first, then per file:
```
git merge-file -L OURS -L BASE-<old> -L NEW-<new> merged/<f> old/.../<f> new/.../<f>
```
rc=0 clean, rc>0 = conflict count. Resolve by understanding the upstream
change, not by picking a side.

### 4. Verify — all of it, every time
- `python tools/verify_overlay_merge.py plugin.video.gears` — proves the mirror
  is EXACTLY clean upstream + our overlay (no lost upstream file, no
  half-applied patch, no stray file). This is the load-bearing check.
- `python -m pyflakes addons/plugin.video.gears` **and the clean upstream tree**,
  then diff the two with line numbers stripped. Gears 2.4.0 ships **17
  pre-existing undefined names** — comparing against upstream is the only way to
  tell "upstream's bug" from "damage I caused". Expect ours-only findings ONLY
  in `kodirdil/`.
- 0 conflict markers; every `.py` compiles; every XML parses.
- Marker counts per patched file (8/3/1/3/2/1 KODIRDIL as of 2.2.11), 10
  `hebrew_subtitles.*` settings, `DebridSubscriptionCheck` alive, 76 network
  icons + 106 in-addon icons present.
- **New `<import>` in addon.xml?** A dependency we do not ship means Kodi
  silently disables the addon. (`script.module.pil` is declared and NOT shipped
  by us — pre-existing, resolved from Kodi's own repo. Do not "fix" it.)
- `python tools/check_setting_ids.py` — catches a silent rename of any setting
  our `gears_settings` policy enforces.

### 5. Install
- Copy merged files over `overlays/plugin.video.gears/files/...`
- Commit the new clean base zip, delete the old one
- `python tools/apply_overlay.py overlays addons` then `--verify`
- Update `base.json`: `base_version`, `base_zip_local`, bump `overlay_version`,
  PREPEND a changelog entry naming each conflict and its resolution

### 6. Test
`python tools/tests/run_tests.py` (must be N/N) and
`python tools/verify_variants.py --strict`. Best practice: run CI's whole step
list locally before pushing — apply_overlay, check_no_credentials,
check_config_delivery, run_tests, check_tmdb_widgets, verify_variants,
gen_variant_index --check, build_addons, build_config, gen_manifest.

### 7. Local deploy (LOCAL-FIRST — never push before Asaf tests)
Kodi must be CLOSED. Stage-then-swap, never delete-first:
```
copy ADDON -> LIVE-parent\plugin.video.gears.new
move LIVE  -> plugin.video.gears.old-<oldversion>     (rollback)
move .new  -> LIVE
```
Then verify the deployed tree hashes identical to the repo tree. Tell Asaf what
upstream changed and what to click-test.

### 8. Push (only after Asaf approves)
`git pull --rebase` (CI pushes manifest refreshes), push, watch **both** CI runs
to success. Do NOT commit `manifest.json` — CI regenerates and pushes it.

## Traps (each cost a debugging round once)

- **Normalise line endings on all three sides BEFORE merging.** Gears 2.4.0
  converted the whole addon CRLF → LF. The naive merge reported 12 conflicts
  across ~8600 "changed" lines, every one phantom; normalised first it was ZERO
  conflicts and a real surface of 8–150 lines per file. Tell: a changed-line
  count approaching the file length. Also normalise the overlaid files upstream
  did NOT touch, or they stay mixed and re-trigger this next time.
- **Beware your own tooling flipping endings.** Writing a file back with
  Python's text mode on Windows turns an LF file into CRLF and buries a 20-line
  edit in a 600-line diff. Write bytes, or check `git diff --stat` before
  committing.
- **A clean textual merge is not a correct merge.** Upstream renames merge
  silently and break at runtime. Grep the WHOLE repo for every renamed
  identifier, including `config/config_policy.json` (`gears_settings`).
- **Upstream ships junk.** 2.4.0 included `_tmp_tango.py`, a maintainer's
  personal sqlite debug script with hardcoded `%APPDATA%` paths. Add such files
  to `PER_ADDON_EXCLUDES` in `tools/common.py` — honoured by `apply_overlay`
  AND the zip builder.
- **New baked credential-shaped values fail CI's scanner.** Verify the value is
  byte-identical to CLEAN upstream (a public app id, not a captured user
  secret), then `python tools/check_no_credentials.py --update-baseline`. 2.4.0
  added `simkl.client`/`simkl.secret` this way.
- **Setting prefix is `gears.*`, never `fenlight.*`** when reading via
  `get_setting`; the setting_id itself carries no prefix.
- `empty_setting` is Gears' literal "unset" sentinel — it is a non-value, not a
  credential.
- Gears reads navigator shortcut folders with `ast.literal_eval`, POV with
  `json.loads` — never copy seeds between engines.
- Gears' banner class is `DebridSubscriptionCheck`; POV's differs. Grepping the
  POV name against Gears returns 0 and looks like a lost patch when nothing is
  wrong.
- Never leave bare `except: pass` in overlay code paths — log the outcome.
- Bidi: user-visible mixed strings are Hebrew-leading with ONE Latin run at the
  end.
