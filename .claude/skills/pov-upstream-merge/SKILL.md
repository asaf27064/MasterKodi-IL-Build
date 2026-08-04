---
name: pov-upstream-merge
description: Re-merge the MasterKodi POV Hebrew overlay onto a new upstream POV release (plugin.video.pov). Use when Asaf says a new POV version is out / "בוא נתאים אותה" / asks to adapt POV to upstream.
---

# POV Upstream Re-merge

Re-bases the Hebrew overlay in `C:\Users\asaf2\Desktop\kodi\MasterKodi-IL-Build`
onto a new upstream POV release. This exact flow shipped 0.1.2 (6.07.89) and
0.1.10 (6.08.01) with zero regressions. Follow it in order; do not freestyle.

## Constants

```
REPO      = C:\Users\asaf2\Desktop\kodi\MasterKodi-IL-Build
OVERLAY   = overlays/plugin.video.pov            (files/ = source of truth)
ADDON     = addons/plugin.video.pov              (mirror: clean base + overlay)
MIRROR    = https://kodiyashimaru.github.io/repo (kodifitzwell.github.io is EMPTY)
ADDONS_XML= {MIRROR}/packages/addons.xml
ZIP       = {MIRROR}/plugin.video.pov/plugin.video.pov-{version}.zip
ISSUES    = gh issue list -R kodifitzwell/repo   (upstream bug reports live here)
LIVE      = C:\MasterKodi IL\portable_data\addons\plugin.video.pov   (Windows box)
```

Overlaid files (the ONLY files we own — everything else is clean upstream):
```
resources/lib/kodirdil/**            (self-contained, never conflicts)
resources/lib/windows/sources.py     (biggest: filters, per-source Hebrew match,
                                      tried-source badge, publish_player_release)
resources/lib/modules/sources.py     (Hebrew-subs thread + gearsai prefetch at top
                                      of get_sources)
resources/lib/modules/kodirdil_ratings.py
resources/lib/menus/{movies,tvshows,navigator}.py   (ratings props, genre icons)
resources/lib/service.py             (debrid days-remaining banner, bidi-safe)
resources/settings.xml               (10 hebrew_subtitles.* settings appended)
resources/media/**, resources/skins/** (icons -- copy-only, never conflict)
```

## Workflow

### 1. Recon
- New version: `curl -s {ADDONS_XML} | grep -oE 'plugin\.video\.pov" version="[0-9.]+"'`
- Current base: `grep base_version overlays/plugin.video.pov/base.json`
- **Check upstream issues** (Asaf always wants this): `gh issue list -R kodifitzwell/repo --state all --limit 15` — read any recently-closed issue that touches TorBox / subtitles / sources; note which fixes landed.

### 2. Download + extract + diff
Work in the scratchpad (`pov-merge/`): extract old base zip (in `{OVERLAY}/base/`)
and new zip to `old/` and `new/`. **Pass Windows-style paths to python** (a
`/c/...` Git-Bash path silently extracts to the wrong place — burned once).
- `diff -rq old/plugin.video.pov new/plugin.video.pov` → the change surface
- `diff old/.../changelog.txt new/.../changelog.txt` → what upstream says changed
- Count changed lines per OVERLAID file to size the merge.

### 3. 3-way merge (never hand-port)
For each overlaid `.py`/`.xml` that upstream touched:
```
cp overlays/.../files/<f> merged/<f>
git merge-file -L OURS -L BASE-<old> -L NEW-<new> merged/<f> old/.../<f> new/.../<f>
```
rc=0 clean, rc>0 = conflict count. Resolve each conflict by UNDERSTANDING the
upstream change, not by picking a side blindly:
- Upstream MOVED code that sits inside our block → keep our block, drop the
  duplicate (verify the moved copy exists elsewhere in the merged file).
- Upstream REMOVED a call/branch our patch rode on → our patch goes with it if
  its reason is gone (e.g. 6.08.01 made UNCACHED click add-to-cloud-only — no
  playback → no publish_player_release there). Leave a dated comment.
- Upstream RENAMED a field our code reads → re-point ours (URLName →
  display_name in 0.1.2 — textual merge is clean but READS still break; grep
  our identifiers against the new upstream producer).

### 4. Verify (all of it, every time)
- 0 conflict markers: `grep -r "<<<<<<<\|>>>>>>>" merged/`
- AST-parse every merged .py; minidom-parse settings.xml
- KODIRDIL marker count per file matches pre-merge
- Key symbols alive: `publish_player_release` (+ clear-on-open, only-when-idle),
  `hebrew_subtitles` settings ×10, `gearsai_prefetch`, banner string
  `נותרו %d ימים`, `display_name` still produced upstream, 0 `URLName`
- `from entry import logger, POVMonitor` still valid (service.py depends on it)
- Debrid banner field paths still match the APIs (TorBox/AllDebrid _request
  UNWRAP the `data` envelope; RD/PM/OC do not — re-check on any debrids/ diff)

### 5. Install
- `cp merged/* overlays/plugin.video.pov/files/...`
- Delete old base zip, keep new one in `{OVERLAY}/base/`
- Rebuild mirror: `rm -rf {ADDON}` → copy clean new tree → copy every file from
  `{OVERLAY}/files/` over it (skip `__pycache__`)
- Update `base.json`: `base_version`, `base_zip_local`, bump `overlay_version`
  (+1 patch), PREPEND a changelog entry naming each conflict + resolution.

### 6. Test
- `python tools/tests/run_tests.py` → must be N/N. The static guard
  `test_pov_publishes_player_release` asserts the publish call-site COUNT — if
  upstream legitimately changed the number of play paths, update the test's
  expected count with a comment (it FAILING on a re-merge is it working).
- `python tools/verify_variants.py --strict` → 0 FAIL.

### 7. Local deploy (LOCAL-FIRST — never push before Asaf tests)
Kodi must be CLOSED. Stage-then-swap, never delete-first:
```
copy {ADDON} -> LIVE-parent\plugin.video.pov.new
move LIVE    -> plugin.video.pov.old-<oldversion>   (rollback)
move .new    -> LIVE
```
Verify live addon.xml version + KODIRDIL count. Tell Asaf what upstream changed
and what to click-test (anything the closed issues fixed, e.g. WebDL).

### 8. Push (only after Asaf approves)
Commit message: overlay bump + upstream highlights + each conflict/resolution +
verification line. `git pull --rebase` (CI pushes manifest refreshes), push,
watch both CI runs (build-and-release + build-and-release-piers) to success.
The wizard's sha-driven updater ships it fleet-wide from there — both Kodi 21
and 22 fleets get the same overlay (POV is version-agnostic).

## Traps (each cost a debugging round once)
- **Normalise line endings on all three sides BEFORE merging.** Gears 2.4.0
  converted the whole addon CRLF -> LF; the naive 3-way merge reported 12
  conflicts over ~8600 "changed" lines, every one phantom. Normalising
  ours/base/new to LF first gave ZERO conflicts and a real surface of 8-150
  lines per file. Always check first:
  `python -c "b=open(P,'rb').read(); print('CRLF' if b.count(b'\r\n') else 'LF')"`
  -- a diff whose changed-line count approaches the file length is this, not a
  rewrite. Write the merged overlay back in the NEW upstream convention.
- **A clean textual merge is not a correct merge.** After every re-merge, diff
  old-vs-new for the overlaid files and read it: upstream RENAMES break code
  that merged without conflict. Seen so far: URLName -> display_name (0.1.2),
  build_my_calendar -> build_my_calendar_trakt + setting sort.watchlist ->
  sort.watchlist_movies/_shows (0.1.11). Grep the whole repo -- including
  `config/` and `config-variants/*/pov/settings.xml` -- for every renamed
  identifier, not just the addon tree.
- **Upstream ships junk.** Gears 2.4.0 included `_tmp_tango.py`, a maintainer's
  personal sqlite debug script. Add such files to `PER_ADDON_EXCLUDES` in
  tools/common.py (honoured by apply_overlay AND the zip builder); never edit
  the committed base zip -- it is the 3-way-merge reference and must stay
  byte-identical to clean upstream.
- New upstream releases can bake NEW credential-shaped values (6.08.01 added
  mdblist.client_id for OAuth) — CI's credential scanner FAILS the build. Verify
  the value is byte-identical to CLEAN upstream (public app id, not a captured
  user secret), then `python tools/check_no_credentials.py --update-baseline`
  and commit. Run the scanner locally BEFORE pushing a re-merge.
- Git-Bash `/c/...` paths passed into `python -c` extract zips to nowhere.
- `select_dialog` renders rows with `.upper()` → `[COLOR x]` tags break — no
  colours in that dialog, ever.
- POV reads navigator shortcut folders with `json.loads`, Gears with
  `ast.literal_eval` — never copy seeds between engines.
- A notification fired before the home window exists is silently dropped —
  boot-time toasts must wait for `Window.IsVisible(home)`.
- Never leave bare `except: pass` in overlay code paths — log the outcome.
- Bidi: user-visible mixed strings are Hebrew-leading with ONE Latin run at
  the end.
