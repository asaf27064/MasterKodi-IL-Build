# Migrating the Hebrew mod to a NEW base addon

When the base video addon dies or is abandoned (as **FenLight → Gears** already happened, and Gears could too), this is the step-by-step playbook to re-home the entire Hebrew ecosystem onto a new fork/addon with the least pain. Read `hebrew-mod.md` and `SKILL.md` first for the overlay model.

## 0. Mental model (why migration is bounded work)
The Hebrew mod is an **overlay** — a small set of files patched on top of a clean upstream addon, plus the self-contained `kodirdil/` module. Migrating = identifying those same patch points on the new base, re-applying them, and updating every place the **old addon id / settings prefix / data path** was hard-coded. The AI Subs addon (`service.subtitles.gearsai`) is mostly base-agnostic but reads the base addon's DBs, so it needs path updates too.

## 1. Inventory the new base
- New addon **id** (e.g. `plugin.video.newfork`) and its **settings prefix** (Kodi exposes `<id-basename>.<setting>` → e.g. `newfork.X`).
- Where to fetch a **clean zip** + how to read its **latest version** (its repo's `addons.xml`). Note whether that repo **prunes old versions** (chainsrepo does — see `hebrew-mod.md`; the auto-update Action diffs against a stored `gears_baseline/` because of this).
- Get a clean copy of the current version of the new base for diffing.

## 2. Map the overlay onto the new base
The files we historically patch (adapt to the new base's layout):
```
resources/lib/kodirdil/                 (whole module — mostly portable)
resources/lib/modules/sources.py        (kodirdil import + scrape-thread injection)
resources/lib/windows/sources.py        (Hebrew filters, % match panel, SDR/tried-source)
resources/lib/caches/settings_cache.py  (the 10 hebrew_subtitles.* settings)
resources/lib/apis/tmdb_api.py          (meta_language → Hebrew synopses)
resources/lib/indexers/movies.py        (OMDB rating listitem props)
resources/lib/indexers/tvshows.py
resources/lib/indexers/navigator.py     (per-genre icons)
resources/lib/modules/meta_lists.py     ('icon' field per genre)
resources/lib/service.py                (DebridSubscriptionCheck banner)
resources/skins/Default/1080i/settings_manager.xml (Hebrew Subtitles submenu)
resources/media/icons/*, network_icons/* (bundled assets)
version.txt
```
**Do NOT assume the new base has the same file layout.** Grep the new base for the functions our patches hook (`get_sources`, `build_movie_content`, `genres()`, `auth()`, the settings list, the settings_manager content block). Some hooks may move or rename.

**TorBox lesson:** before porting `apis/torbox_api.py`, check if the new base already ships a native QR/device-code flow (Gears 2.2.2 did → we dropped our patch). Don't port a patch upstream already provides.

## 3. Port + rebrand (every hard-coded reference)
Search-and-replace across the ported overlay (case-sensitive both ways):
- `plugin.video.gears` → `plugin.video.<newfork>` (and the OLD `plugin.video.fenlight` if any residue)
- setting reads: `get_setting('gears.hebrew_subtitles.X')` → `'<newfork>.hebrew_subtitles.X'` (runtime reads use the prefix; setting *definitions* in `settings_cache.py` do NOT)
- log tag `Gears-HEBSUBS` → `<Newfork>-HEBSUBS`
- comment marker `KODIRDIL` can stay (it's our marker, not addon-specific)
- `kodirdil/db_utils.py` data path: `special://profile/addon_data/plugin.video.gears/` → the new addon's `addon_data` path (this is where `hebrew_subtitles_db.sqlite` / `media_metadata_db.sqlite` live)

## 4. Re-apply the patches (use 3-way merge)
Best method (proven this session): for each patched file, `git merge-file` —
`merge-file(our-patched-OLD, clean-OLD, clean-NEW)` — rebases our patch onto the new base, flagging only real conflicts. **Use `git merge-file`, not `patch`/diff** (the latter corrupted UTF-8 Hebrew). After merging: `py_compile` every file, validate XML with `ET.parse`, and grep for the `KODIRDIL` markers to confirm each patch landed.

## 5. Update kodirdil-dependent components
- **`service.subtitles.gearsai/resources/lib/kodirdil.py`** (the AI Subs reader) reads the base addon's `media_metadata.db` + `hebrew_subtitles.db` under `addon_data/plugin.video.gears/`. Point it at the new addon's data path + id.
- Any `gears.tb.token` etc. debrid setting reads in `service.py`'s `DebridSubscriptionCheck` → new prefix.

## 6. Build artifacts
- `gears_hebrew_subtitles.zip` (overlay) — use `scripts/rebuild_hebrew_zip.py` (update `REQUIRED_FILES` for the new base; remove any patch upstream now provides, like TorBox). Rename to `<newfork>_hebrew_subtitles.zip` if you want clean naming.
- `version.txt` MUST equal the new `*_version.json` `"version"`.
- Register the base in the build's `Addons33.db`: `installed.origin=''`, `update_rules.updateRule=2` (so Kodi never auto-updates it and strips our patches — the wizard manages updates instead).

## 7. Update the auto-update Action (`pov-modified-heb/.github/workflows/gears-auto-update.yml`)
- Point `ADDONS_XML_URL` / `ZIP_BASE` at the new base's repo.
- Update the `HEBREW_FILES` watch list to the new patch set.
- Refresh `gears_baseline/` to the new base's clean files + set `BASELINE_VERSION.txt`.
- Keep `REQUIRED_FILES` in `rebuild_hebrew_zip.py` in sync with `HEBREW_FILES`.

## 8. Update the wizard (`plugin.program.masterkodi.il.wizard`)
- `resources/libs/config.py` + `installer.py`: add/repoint the addon id + URLs.
- The `GearsHebrewInstaller` pattern (overlay onto a clean base from upstream) ports directly — change the addon id, the chainsrepo-style base URL, and the overlay zip URL.
- `service.py` `check_gears_update()`: point the gate at the new base's upstream `addons.xml` + your `<newfork>_version.json compatible_*`.
- Bump the wizard version; rebuild + `--push-wizard` (existing users auto-update the wizard, which then migrates them).

## 9. Update the build + ship
- Swap the patched new base into `FenLight_Estuary.zip` (replace the old `plugin.video.gears` folder; update `Addons33.db`). Re-upload to the `v1.0` release.
- Rebuild EXE/APK only if the wizard changed (`build_masterkodi_all.py --both`; needs Pillow + `PYTHONIOENCODING=utf-8`).
- Push the new overlay + version json to `pov-modified-heb`.

## 10. Residual scan + verify
- `python scripts/scan_residual_fenlight.py` — adapt it to scan for the **OLD** addon id you're leaving (don't ship stale references).
- `py_compile` the whole `resources/lib`, `ET.parse` all XML, grep `KODIRDIL` markers, confirm `version.txt == *_version.json`.
- **Kodi smoke test** (mandatory): the base loads, a source plays, Hebrew subs match in the source screen, debrid auth works, AI Subs still translate.

## Checklist
- [ ] New base id, settings prefix, version source, prune behavior known
- [ ] Overlay files mapped onto the new base layout
- [ ] TorBox (and any other) upstream-native features dropped from the overlay
- [ ] 3-way merge done; py_compile + XML + KODIRDIL markers all green
- [ ] kodirdil data path + gearsai reader repointed
- [ ] overlay zip rebuilt; version.txt == version json
- [ ] Addons33.db registration (origin='', updateRule=2)
- [ ] auto-update Action repointed; baseline refreshed
- [ ] wizard installer + gate repointed; version bumped; pushed
- [ ] build zip rebuilt + uploaded; EXE/APK rebuilt if wizard changed
- [ ] residual scan clean; Kodi smoke test passed

## Lessons baked in (gotchas)
- **Upstream prunes old zips** → diff against a stored baseline, never the pruned old upstream zip.
- **Use `git merge-file`** for re-patching Hebrew files (preserves UTF-8; `patch`/diff corrupts it).
- **Windows console is cp1255** → set `PYTHONIOENCODING=utf-8` for build scripts that print emoji (apktool builder crashed otherwise); APK builder also needs `pip install pillow`.
- **Wizard can't downgrade** an addon — a bad wizard push is fixed by a forward version (2.3.1 → 2.3.2), not a revert. Smoke-test before `--push-wizard`.
- **Gate every user-facing update on a version YOU publish** (`compatible_*`), never raw upstream — the new base's overlay may need re-patching first (the 2.2.2 lesson).
