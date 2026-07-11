# The MasterKodi IL Wizard — runtime architecture, updates & propagation

How the wizard (`plugin.program.masterkodi.il.wizard`) actually *works* at runtime
and how updates reach users. (For *building* the EXE/APK see `wizard-build.md`.)

## Where the source lives (IMPORTANT — dual roots)
There are TWO project roots on disk and it's easy to edit the wrong one:
- `C:\Users\asaf2\Desktop\kodi\` — the **active** wizard/gears work area: `MasterKodi_Build` (the LATEST wizard zip + `_wizard_src/` extracted tree), `pov-modified-heb` (the git repo that's actually pushed), `gears_222`.
- `C:\Users\asaf2\Desktop\kodi_project\` — has `FenLight_Estuary/` (the **gearsai** addon source-of-truth) + `Arctic_Fuse_Skin/` + an OLDER `MasterKodi_Build`/`pov-modified-heb`.

Rules of thumb: **gearsai** edits → `kodi_project/FenLight_Estuary/...`; **wizard** edits + **builds** + **pov pushes** → `kodi/...`. The wizard is maintained as a **zip** (no permanent working tree) — extract the latest `plugin.program.masterkodi.il.wizard-X.Y.Z.zip` to `_wizard_src/`, edit, re-zip (13 files, single top folder, exclude `__pycache__`/`.pyc`), bump `addon.xml`, push.

## Pushing the wizard
`push_wizard()` in `build_masterkodi_all.py` (or `--push-wizard`): clones gh-pages, drops the new zip into `zips/plugin.program.masterkodi.il.wizard/` (removes old zips), pushes. Then the **`kodi-index.yml` GitHub Action regenerates `addons.xml` automatically** (≈15s) — never hand-edit addons.xml. The live Pages CDN lags ~1 min behind the committed file.
- To call it cold: `import build_masterkodi_all as b; b.push_wizard("<zip>", None)` (the `config` arg is unused).
- GOTCHA: it `rmtree`s a stale `%TEMP%\masterkodi_ghpages` clone whose `.git` is read-only → `PermissionError`. Pre-clear it with an `onexc` chmod handler before calling.

## Runtime: the service (`service.py`, class `POVHebrewService` — legacy name, it's the Gears service)
`run()` on Kodi startup:
1. early-return if setting `skip_update_check=='true'` (set after a build install; auto-resets to false → so a fresh build can take **two** restarts before it updates).
2. `xbmc.sleep(5000)` settle.
3. `_cleanup_old_addon_dirs()` — sweeps stale `*_old_<ts>` backup folders.
4. `check_wizard_self_update()` — the wizard updates ITSELF.
5. `check_all_updates()` — ONE combined check for everything else.

**Self-update** (`check_wizard_self_update`): GET `https://asaf27064.github.io/addons.xml`, regex the wizard version (`<addon ... id="..." ... version="X">` — id BEFORE version), if newer download `…/zips/<id>/<id>-X.zip` and `shutil.rmtree`+`copytree` over its own folder. Independent of Kodi's repo system. Runs once per startup; the running instance is the OLD code, so the new version is active on the NEXT start.

**`check_all_updates(silent_if_none=True)`** — the single update brain (replaced two older split-dialog methods, now deleted):
- Gathers into two lists: `addon_updates` (full add-on replace: Gears base [gated on `compatible_gears==upstream`], Skin) and `hebrew_updates` (Gears overlay, gearsai, Skin-Hebrew).
- ONE yesno dialog ("עדכן הכל").
- Performs **file/overlay updates first (no restart), then add-on updates (single restart at the very end)** so a MIX completes in one pass. If add-ons all fail but files succeeded, a fallback restart fires. `perform_hebrew_updates(restart=)` returns count; `perform_addon_updates(restart=)` returns whether it restarted.
- `silent_if_none=False` ignores the global toggle + reports "up to date" — used by the manual **"בדוק עדכונים עכשיו"** button (main menu + settings action `?mode=check_updates` → `run_update_check()`).

## How each component reaches users (the propagation model)
Two audiences:
- **Existing users** → EVERYTHING via the wizard on startup (with internet). Never need a new EXE/APK.
- **New users** → install a **bootstrap** EXE/APK that contains ONLY wizard + firstrun + repo + script.module deps (NOT gears/gearsai/skin). On first run firstrun launches the wizard, which downloads `FenLight_Estuary.zip` (the real build) from the v1.0 release + installs it.

| You changed… | Existing users get it by… | For NEW installs also… | Rebuild EXE/APK? |
|---|---|---|---|
| Wizard | self-update (push wizard zip → addons.xml auto-regens) | bundled wizard self-updates on first run | Optional |
| gearsai / Gears overlay / Skin-Hebrew | wizard reads `*_version.json` and installs (push zip+json to pov) | rebuild+upload `FenLight_Estuary.zip` (`publish.py --reinject --upload`) | No |
| firstrun / repo / script.module deps | ❌ no self-update path | MUST rebuild EXE/APK | **Yes** |

So: **pushing the zips is usually enough.** Only the *bootstrap* (firstrun/repo/deps) requires an EXE/APK rebuild.

## The Build menu list is SERVER DATA, not code
`builds.py fetch_builds_list()` reads `https://asaf27064.github.io/assets/build.txt` live (format: `name="..." version="..." url="..." skin_url="..." description="..."`). To rename/add/remove a build shown in "Build Installation", edit **`assets/build.txt`** in gh-pages — NOT the wizard code. (2026-06-26: it still said "FenLight" + a broken "POV" → rewrote to one `name="MasterKodi IL (Gears)"` pointing at `FenLight_Estuary.zip`.)

## Menus & UX (`default.py`)
- Main menu = `Dialog().select(..., useDetails=True)` rich cards (icon+title+status) built via `menu_item()` + standard `DefaultAddon*.png` textures; a **parallel handler list** (not fixed indices) so the optional POV row (shown only if `plugin.video.pov` installed) can't desync clicks.
- Rows: **Gears + עברית** (`gears_menu`, shows base+overlay), **כתוביות AI (Gemini)** (`gearsai_menu` → settings/info/install), **Skin**, POV-if-installed, Build, Maintenance, Backup, "בדוק עדכונים עכשיו", Settings.
- Status helpers: `get_gears_status` (uses `GearsHebrewInstaller.is_gears_installed/is_installed`), `get_gearsai_status` (`GearsaiInstaller`).
- A true full-screen `WindowXML` UI is the only way to force RTL/custom layout but is high-risk (untestable, could brick the wizard for all users) → would gate behind an opt-in setting + device-test first. Built-in dialogs follow Kodi's GUI language for RTL; the addon can't override per-dialog.

## Backup / Restore (`resources/libs/backup.py`, `BackupManager`)
FILE-based: copies each addon's `addon_data/<id>/settings.xml` (grabs the **Gemini key**, **debrid/Trakt tokens**, skin+wizard settings) + guisettings/sources/favourites. Two scopes: QUICK (settings+keys) / FULL (whole userdata minus Thumbnails/packages/temp/cache/Textures*.db). Each zip has a `manifest.json`. Setting `backup_location` (folder) → point at external storage to survive a full reinstall. UI flows: `create_flow`/`restore_flow`/`manage_flow`; `default.py` Backup menu calls them.

## FenLight is fully removed from ACTIVE wizard code (2026-06-26, 2.3.8)
Gone from default.py/service.py/settings.xml; deleted dead `addon_manager.py`. Harmless DEAD code still in `installer.py` (`FenLightHebrewInstaller`) + `config.py` (`FENLIGHT_*`) — invisible, never runs; removing is untested surgery, left intentionally.

## Current version map (2026-06-26)
wizard **2.3.8** · gearsai **0.4.0** · Gears overlay **2.1.0** (compatible_gears 2.2.2) · firstrun 2.1.2 · repo 1.0.0.
