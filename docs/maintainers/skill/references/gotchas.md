# Gotchas — Windows + Hebrew + Kodi specifics

Sharp edges encountered while operating on Asaf's Windows 11 machine. Most aren't documented anywhere else.

## Settings action buttons do nothing in a SUBTITLE addon (gearsai, 2026-06-27)
gearsai declares only `xbmc.subtitle.module` + `xbmc.service` — NOT `xbmc.python.pluginsource` — so `plugin://service.subtitles.gearsai/` is not a routable plugin path and **`RunPlugin(plugin://...)` from settings action buttons silently does nothing**. (Subtitle search/download still work — Kodi's subtitle system invokes `default.py` via the module mechanism, not plugin://.) Fix: invoke `default.py` via **`RunScript(special://home/addons/service.subtitles.gearsai/default.py,<action>)`** and have `default.py` handle BOTH paths: the action word arrives as `sys.argv[1]` (RunScript) vs a numeric handle in argv[1] + `?query` in argv[2] (subtitle). Also guard `HANDLE = int(sys.argv[1])` with try/except (the action word isn't an int → crashes at import otherwise).

## Kodi settings labels render BLANK (gearsai, 2026-06-27)
The **new** settings format (`<settings version="1">` with `<section>/<category>/<group>/<control>`, which gearsai uses) resolves `label`/`<heading>`/`<option label>` as **numeric string IDs from `resources/language/resource.language.*/strings.po`** — plain-text labels render **blank** (unlike the OLD format the wizard uses, where text labels work). gearsai shipped text labels + EMPTY `strings.po` → every label blank. Fix: number every label (`label="32001"`) + populate `strings.po` for en_gb (`msgstr ""` → falls back to msgid English) AND he_il (`msgstr "<Hebrew>"`). Kodi picks the file by GUI language. Conversion script pattern: regex-replace `label="<eng>"`/`<heading><eng></heading>` → IDs keyed off an `{id:(en,he)}` map, then emit both .po. (gearsai uses 32001-32049.)

## From the 2026-06-26 session (wizard 2.3.x + gearsai 0.4.0)
- **Dual project roots.** `kodi/` (active: wizard build, the pushed `pov-modified-heb` git repo, gears_222) vs `kodi_project/` (the `FenLight_Estuary/gearsai` source, Arctic_Fuse_Skin, OLDER copies). Check mtimes / which `pov-modified-heb` is a git repo before editing — easy to touch a stale copy. See `wizard.md`.
- **Verifying an addon's version in addon.xml**: `grep version=... | head -1` is WRONG — it grabs the `<?xml version="1.0"?>` prolog. Use `<addon[^>]*version="..."` (or `tail -1`).
- **`build_masterkodi_all.py main()` has interactive `input("Proceed?")`** that hangs under piped/background stdin (even with `--deploy`, which only auto-answers the upload/push prompts). To build non-interactively, call the functions directly: `build_windows(config,base)` / `build_android(config,base)` / `collect_outputs(...)` / `upload_releases(...)`. EXE → release tag **`exe`**, APKs → tag **`apk`** (each keeps a `_old` backup). Build auto-refreshes the bundled wizard from the highest wizard zip; prereqs: ISCC, `android/apktool_*.jar`, Java, Pillow, `android/kodi-il.keystore`.
- **EXE/APK are a bootstrap** (wizard+firstrun+repo only, NOT gears/gearsai/skin) — the build is downloaded from `FenLight_Estuary.zip` on first run. So most updates DON'T need an EXE/APK rebuild; pushing the zips suffices (see `wizard.md` propagation table).
- **Build menu names come from `assets/build.txt`** (server data), not code — rename builds there.
- **gh-pages CDN lags ~1 min** behind the committed `addons.xml`/`build.txt`/files (Pages redeploy); `gh api .../contents` shows the committed truth immediately.
- **`push_wizard` stale temp clone**: `%TEMP%\masterkodi_ghpages` `.git` is read-only → `rmtree` `PermissionError`. Pre-clear with a chmod `onexc` handler.
- **gearsai translation invariant**: `gender_analysis` + `fast_mode` both OFF must produce byte-identical prompts to pre-0.4.0 (verify via offline `prompt.build` with/without `gender_map`). `thinkingConfig` only valid on 2.5/3.x models (400 otherwise) — gated by `gemini._supports_thinking`.

## Windows console encoding (cp1255)

Asaf's default PowerShell codepage is **cp1255 (Windows Hebrew)**. Python's stdout uses this by default. Any `print('✅')` or other non-cp1255 character crashes with:

```
UnicodeEncodeError: 'charmap' codec can't encode character '✅' in position 2: character maps to <undefined>
```

This was hit in `MasterKodi_Build/android/build_kodi_il_apk_v3.py:413` (`✅ SUCCESS!`). Workaround:
```bash
export PYTHONIOENCODING=utf-8   # MINGW
$env:PYTHONIOENCODING="utf-8"    # PowerShell
set PYTHONIOENCODING=utf-8       # cmd
```

Better fix: avoid emojis in print statements. The session patched the existing instance to `[OK] SUCCESS!`. When writing new Python that runs in `.bat` chains, stick to ASCII output.

When reading a JSON file written elsewhere:
```python
# Wrong (uses cp1255 on Asaf's box):
with open('file.json') as f: json.load(f)

# Right:
with open('file.json', encoding='utf-8') as f: json.load(f)
```

## Windows `shutil.rmtree` on git directories

Git stores `.git/objects/pack/*.idx` files as **read-only**. Python's `shutil.rmtree` doesn't strip the read-only attribute before unlinking, so it fails:

```
PermissionError: [WinError 5] Access is denied:
'C:\\Users\\asaf2\\AppData\\Local\\Temp\\masterkodi_ghpages\\.git\\objects\\pack\\pack-XXXX.idx'
```

Two fixes:
1. **`onerror` handler** (proper) — already patched at `MasterKodi_Build/build_masterkodi_all.py:266`:
   ```python
   def _rm_readonly(func, path, _):
       os.chmod(path, 0o666)
       func(path)
   shutil.rmtree(clone_dir, onerror=_rm_readonly)
   ```
2. **PowerShell brute force** (emergency):
   ```bash
   powershell.exe -Command "Remove-Item -Recurse -Force -ErrorAction SilentlyContinue '<path>'"
   ```

This hits any code that clones a repo to temp and tries to clean up. The patched `build_masterkodi_all.py` has the handler at the pre-clone cleanup site; the post-clone cleanup sites already used `ignore_errors=True` so they silently leak temp files instead of crashing.

## Android build-tools default mismatch

`build_masterkodi_all.py` defaults to `C:\Users\asaf2\AppData\Local\Android\Sdk\build-tools\36.0.0-rc5`. That version does **not** exist on Asaf's machine. The latest installed is `35.0.0`.

**Always** create `config.ini` before the first run (or after a Python upgrade rewrites defaults). Check installed versions:
```bash
ls /c/Users/asaf2/AppData/Local/Android/Sdk/build-tools/
```

Then write to `MasterKodi_Build/config.ini`:
```ini
[paths]
android_build_tools = C:\Users\asaf2\AppData\Local\Android\Sdk\build-tools\35.0.0
```

The build script needs `zipalign.exe` and `apksigner.bat` — both ship with build-tools 33+. Lower versions might not have them.

## Interactive prompt blocking on `--deploy`

`build_masterkodi_all.py` has a `"Proceed? [Y/n]"` prompt **before** the build starts, separate from the deploy prompts that `--deploy` bypasses. Running headless or via tool calls, you'll hit `EOFError`. Fix:

```bash
yes y | python build_masterkodi_all.py --both --deploy
```

`yes y` keeps pumping `y\n` until the process closes stdin, satisfying the Proceed prompt without affecting later non-interactive logic.

## `gh` CLI ambient repo

`gh` commands default to "current directory's repo" if not given `-R`. Inside `MasterKodi_Build/` there's no git repo, but inside `pov-modified-heb/` there is. **Always pass `-R asaf27064/asaf27064.github.io`** when uploading releases — `build_masterkodi_all.py` does this correctly but ad-hoc commands often don't.

## Sqlite text_factory landmines

When opening `navigator.db` or `settings.db` for inspection, **don't** use `con.text_factory = bytes` and then mix string+bytes parameters in queries:

```python
# This works for SELECT but UPDATE silently fails for some rows
con.text_factory = bytes
cur.execute("UPDATE settings SET setting_id=? WHERE setting_id=?", (b'new', b'old'))
```

The behavior depends on whether the column was originally stored as TEXT (Python str) or BLOB. Symptom: SELECT returns the row but UPDATE matches 0 rows.

Safer pattern: **don't set `text_factory`**, just use strings everywhere:
```python
con = sqlite3.connect(db_path)
cur = con.cursor()
cur.execute("UPDATE settings SET setting_id='new' WHERE setting_id='old'")
```

This works because Python writes `str` as TEXT, and Kodi reads it back the same way. Use `text_factory=bytes` only for **forensics** (when you need to see how data is stored), not for **modification**.

## `gh release upload` clobber semantics

Existing assets with the same name will **fail** without `--clobber`. With `--clobber`, the existing asset is renamed to `<name>_old.<ext>` (the build script does this manually before uploading the new one). So after a re-deploy, the release will have:
- `MasterKodiIL_Setup.exe` (new)
- `MasterKodiIL_Setup_old.exe` (previous)

If you re-deploy twice in a row, the `_old` version gets overwritten with the most-recent-previous (not lost entirely; the original previous is gone). Acceptable for testing.

## File path slash semantics

`zipfile` uses forward slashes in archive entries regardless of platform. Use `os.path.relpath(...).replace(os.sep, '/')` defensively when building archives on Windows, or rely on `zipfile.ZipFile.write(path, arcname)` which normalizes for you.

Reading entries back: `zf.namelist()` returns forward-slash paths. To extract to a Windows path: `name.replace('/', os.sep)`.

## Kodi's "Never auto-update" semantics

Three things must be true for Kodi to never touch an addon:
1. `installed.origin = ''` (manually installed)
2. `update_rules` row with `updateRule = 2` ("Never")
3. The addon's repo (if any) is also set to Never auto-update

If any of these is missing, Kodi may decide to "helpfully" update the addon, blowing away the Hebrew overlay. The wizard's `setup_wizard_repo_in_db()` ensures the wizard's own repo origin is correct; for `plugin.video.gears`, the wizard's first-run installer + the working-tree-shipped Addons33.db handle this.

## When fenlight-named files target gears

A historical artifact: the **working directory** is still called `FenLight_Estuary/`. The renamed Hebrew zip is `gears_hebrew_subtitles.zip` but inside `FenLight_Estuary/addons/` the addon folder is `plugin.video.gears/`. This dual-naming is confusing but consistent.

Don't rename `FenLight_Estuary/` — it's referenced in:
- All the documentation
- `MasterKodi_Build/build_masterkodi_all.py` (build pipeline)
- Future muscle memory

Just be aware that "FenLight_Estuary" is a **historical label**, not a description of the contents.

## Verifying nothing references FenLight

Run the audit script after any migration touch-up:
```bash
python ~/.claude/skills/masterkodi-il-builder/scripts/scan_residual_fenlight.py
```

Acceptable residuals:
- Changelog text in `gears_version.json` mentioning the migration
- This skill's documentation
- The folder name `FenLight_Estuary/` itself

Anything else is a bug.

## Wizard installer's `extract_zip` filters

When `BuildManager.extract_zip()` walks the build zip, it silently **skips** these:
- `'Database' in filename and filename.endswith('.db')` — DBs handled separately by `merge_addon_databases()`
- `ADDON_ID in filename` — won't overwrite the wizard itself
- `'__pycache__' in filename` or `.pyc` / `.pyo`
- `filename.endswith('.csv')`
- Non-ASCII filenames (`filename.encode('ascii')` fails)

So a non-ASCII filename in the build zip is **silently dropped**. If you ever want a Hebrew filename in the build, you'd need to patch the wizard's extract loop too. So far we keep filenames ASCII and put Hebrew inside file *contents*.
