# Wizard, Build, and Deploy (`MasterKodi_Build/`)

The build pipeline lives in `MasterKodi_Build/`. It compiles a Windows installer (Inno Setup) + two Android APKs (armv7 and arm64), uploads to GitHub Releases, and pushes the wizard zip to GitHub Pages.

## Pipeline entry points (.bat files)

All call `build_masterkodi_all.py` with different flags. Each pauses at the end so a non-cli user can read the output.

| .bat | Args | What it does |
|---|---|---|
| `Build_Windows.bat` | `--windows` | EXE only, local |
| `Build_Android.bat` | `--android` | APKs only, local |
| `Build_Both.bat` | `--both` | EXE + APKs, local |
| `Deploy_Windows.bat` | `--windows --deploy` | EXE + upload + push wizard |
| `Deploy_Android.bat` | `--android --deploy` | APKs + upload + push wizard |
| `Deploy_Both.bat` | `--both --deploy` | Everything (the "do it all" button) |
| `Upload_Only.bat` | `--upload` | Re-upload existing `Output/` artifacts |
| `Push_Wizard_Only.bat` | `--push-wizard` | Push wizard zip to GitHub Pages repo |
| `Interactive_Mode.bat` | (no flag) | Interactive prompts |

## `build_masterkodi_all.py` flow

```
detect_addon_zips()                  # finds wizard, firstrun, repo zips by glob
  └─ "Proceed? [Y/n]"                # interactive prompt — pipe `yes y` to bypass
  ├─ if --windows or --both:
  │   build_windows()
  │     ├─ extracts wizard/firstrun/repo zips into windows/portable_data/addons/
  │     ├─ fix_addons_db(portable_data/userdata/Database/Addons33.db)
  │     ├─ rewrites MasterKodiIL.iss AppVersion= to match wizard version
  │     ├─ copies portable_data → KodiFiles/portable_data
  │     ├─ runs 7za to package KodiFiles/* → package.7z
  │     └─ runs ISCC.exe to compile MasterKodiIL.iss → MasterKodiIL_Setup.exe
  ├─ if --android or --both:
  │   build_android()
  │     ├─ for each ABI (armv7, arm64):
  │     │   calls android/build_kodi_il_apk_v3.py
  │     │     ├─ apktool d <kodi-base.apk> → work_masterkodi_il/
  │     │     ├─ injects wizard, firstrun, repository.masterkodi.il into assets/python/addons/
  │     │     ├─ copies script.module.requests + deps into the APK
  │     │     ├─ patches addon-manifest.xml (NOT including wizard/repo to avoid origin lock)
  │     │     ├─ replaces app name + icon + splash + banner
  │     │     ├─ apktool b → unsigned.apk
  │     │     └─ zipalign + apksigner sign → MasterKodiIL_21.3_<bits>bit.apk
  ├─ collect outputs into Output/
  ├─ if --deploy:
  │   upload_releases(outputs)        # gh release upload exe/apk tags on PAGES_REPO
  │   push_wizard()                   # git clone PAGES_REPO, replace zips/.../wizard-X.zip, commit, push
```

## Required configuration

`config.ini` at `MasterKodi_Build/`. The script auto-creates it from defaults on first run. Default paths assume Asaf's Windows machine:

```ini
[paths]
windows_dir = windows
android_dir = android
output_dir = Output
innosetup_compiler = C:\Program Files (x86)\Inno Setup 6\ISCC.exe
android_build_tools = C:\Users\asaf2\AppData\Local\Android\Sdk\build-tools\35.0.0

[android]
keystore = kodi-il.keystore
alias = kodiil
storepass = kodiil123
keypass = kodiil123
app_name = MasterKodi IL
```

**Critical**: the *default* value in the script is `build-tools\36.0.0-rc5` which **does not exist** on Asaf's machine. Always create `config.ini` first (or after a Python upgrade rewrites defaults) and point it at the highest installed build-tools (`35.0.0` as of 2026-05).

To check what's installed: `ls /c/Users/asaf2/AppData/Local/Android/Sdk/build-tools/`.

## Other dependencies on PATH

- `gh` (GitHub CLI) — authenticated as `asaf27064`. Check with `gh auth status`.
- `git` — used for the wizard push.
- `java` (OpenJDK 11 or higher) — apktool needs it.
- Python 3.13 — at `/c/Python313/python`. The script runs as `python` (Windows resolves via App Execution Aliases).

The `7za.exe` packager ships **inside** `MasterKodi_Build/windows/`, not on PATH. Don't move it.

## Constants in `build_masterkodi_all.py`

```python
WIZARD_ID         = "plugin.program.masterkodi.il.wizard"
FIRSTRUN_ID       = "service.kodi.il.firstrun"
REPO_ID           = "repository.masterkodi.il"
GITHUB_REPO       = "asaf27064/asaf27064.github.io"
GITHUB_CLONE_URL  = "https://github.com/asaf27064/asaf27064.github.io.git"
```

The wizard zip is detected by glob across these patterns: `plugin.program.masterkodi.il.wizard*.zip`, `MasterKodi_IL_Wizard*.zip`.

## Wizard version bumping

The wizard exists in **two** places with versioning that must stay aligned:
- `addon.xml` inside the wizard zip — what Kodi reads
- The zip filename — `MasterKodi_IL_Wizard_v<X.Y.Z>.zip` / `plugin.program.masterkodi.il.wizard-<X.Y.Z>.zip`

`push_wizard()` has built-in protection:
1. Determines version from the highest of (addon.xml, filename).
2. Clones the Pages repo and reads the current published version.
3. **Refuses to push if `new <= current`** — exits with a warning.

If you bump locally but forget what's published, run `Push_Wizard_Only.bat` once to find out — it'll skip with "v<X> is NOT newer than v<Y>" if you're behind.

To rebuild the wizard zip at a new version:
```python
# Extract current zip, edit addon.xml, repack
import os, re, zipfile
src = r'C:\path\to\extracted\plugin.program.masterkodi.il.wizard'
addon_xml = os.path.join(src, 'addon.xml')
content = open(addon_xml, encoding='utf-8').read()
content = re.sub(r'(id="plugin\.program\.masterkodi\.il\.wizard"[^>]+version=")[^"]+(")',
                 r'\g<1>2.3.0\2', content)
open(addon_xml, 'w', encoding='utf-8', newline='\n').write(content)

# Pack two zip names (filename pattern variants)
for dst in ['plugin.program.masterkodi.il.wizard-2.3.0.zip', 'MasterKodi_IL_Wizard_v2.3.0.zip']:
    with zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(src):
            for fn in files:
                fp = os.path.join(root, fn)
                arc = os.path.relpath(fp, os.path.dirname(src))
                zf.write(fp, arc)
```

## The wizard's runtime model

The wizard (`plugin.program.masterkodi.il.wizard/`) does three jobs:

1. **First-run installer** (`builds.py`): reads `https://asaf27064.github.io/assets/build.txt`, presents builds to the user, downloads the selected build zip, extracts to Kodi `HOME`, merges DBs, installs/enables addons.

2. **Background service** (`service.py`): monitors for addon updates via `onNotification` handlers. When `plugin.video.gears`, `plugin.video.pov`, or `skin.arctic.fuse.3` gets updated/enabled by Kodi, checks if the Hebrew overlay was previously installed and reinstalls if so. Watches `gears_version.json` / `version.json` / `skin_version.json` on the pov-modified-heb repo to detect new Hebrew mod releases.

3. **Maintenance UI** (`default.py`): menu-driven plugin for the user to manually reinstall/upgrade/backup/restore.

### `service.py` Hebrew auto-reinstall handlers

`onNotification` handles three addon IDs:
- `plugin.video.pov` → `reinstall_pov_hebrew(new_version)`
- `plugin.video.gears` → `reinstall_gears_hebrew(new_version)` ← **was missing pre-migration, must exist**
- `skin.arctic.fuse.3` → `reinstall_skin_hebrew()`

Each handler:
1. Reads the saved `<addon>_hebrew_version` setting.
2. If empty/0, skip (Hebrew was never installed).
3. Detect missing Hebrew files (e.g., `kodirdil/` directory).
4. Trigger reinstall via the relevant Installer class from `installer.py`.
5. Update `last_<addon>_version` and `<addon>_hebrew_version` settings.
6. `xbmc.executebuiltin('Quit')` to force restart.

### `installer.py` source URLs

```python
GITHUB_BASE_URL = "https://raw.githubusercontent.com/asaf27064/pov-modified-heb/main"
POV_VERSION_URL     = f"{GITHUB_BASE_URL}/version.json"
POV_ZIP_URL         = f"{GITHUB_BASE_URL}/pov_hebrew_subtitles.zip"
GEARS_VERSION_URL   = f"{GITHUB_BASE_URL}/gears_version.json"
GEARS_ZIP_URL       = f"{GITHUB_BASE_URL}/gears_hebrew_subtitles.zip"
SKIN_VERSION_URL    = f"{GITHUB_BASE_URL}/skin_version.json"
SKIN_ZIP_URL        = f"{GITHUB_BASE_URL}/skin_hebrew_files.zip"
```

These are the runtime endpoints; the wizard fetches the zip and overlays it on the installed addon.

### `service.py` update check sources

```python
GEARS_SOURCES = [
    {
        'name': 'chainsrepo (unhingedthemes/zips)',
        'addons_xml_url': 'https://raw.githubusercontent.com/unhingedthemes/zips/main/_zips/addons.xml',
        'zip_url': 'https://raw.githubusercontent.com/unhingedthemes/zips/main/_zips/plugin.video.gears/plugin.video.gears-{version}.zip',
    }
]
```

The wizard parses `<addon ... id="plugin.video.gears" version="X.Y.Z">` from the addons.xml.

## Settings tracked by the wizard

`plugin.program.masterkodi.il.wizard/resources/settings.xml`:
- `auto_reinstall_pov` (bool) — reinstall POV Hebrew after Kodi updates POV
- `auto_reinstall_gears` (bool) — reinstall Gears Hebrew after Kodi updates Gears
- `auto_reinstall_skin` (bool) — reinstall Skin Hebrew after Kodi updates Arctic Fuse
- `last_pov_version`, `last_gears_version` — version strings for change detection
- `pov_hebrew_version`, `gears_hebrew_version`, `skin_hebrew_version` — version of currently-installed Hebrew overlays

If you rename any of these settings, also rename in `service.py` and `default.py`.

## Running the full deploy

The "do everything" command line:
```bash
cd C:\Users\asaf2\Desktop\kodi_project\MasterKodi_Build
export PYTHONIOENCODING=utf-8   # required to avoid cp1255 crash on emoji prints
yes y | python build_masterkodi_all.py --both --deploy
```

This will:
1. Build Windows EXE → `Output/MasterKodiIL_Setup.exe`
2. Build Android APKs → `Output/MasterKodiIL_21.3_32bit.apk` + `MasterKodiIL_21.3_64bit.apk`
3. Upload all three to GitHub Release tags (`exe`, `apk`) on `asaf27064/asaf27064.github.io`. Previous versions get renamed `*_old.{exe,apk}`.
4. Clone the Pages repo, replace the wizard zip at `zips/plugin.program.masterkodi.il.wizard/`, commit, push.

Expected duration: 5-15 minutes depending on network. The Inno Setup compile is the slowest step (~2-3 min for the 45 MB 7z).
