# Arctic Fuse skin layer (`Arctic_Fuse_Skin/`)

The wizard installs Arctic Fuse 3 as an **optional layer on top of the base Estuary build**. Selecting it during wizard install (or via the "Add Arctic Fuse to existing build" flow) extracts this folder's contents into Kodi home, on top of whatever the base build already has.

## Layout

```
Arctic_Fuse_Skin/
├── addons/
│   ├── skin.arctic.fuse.3/        ← Skin XMLs, fonts, includes
│   ├── plugin.video.themoviedb.helper/  ← TMDB Helper (provides metadata for skin widgets)
│   ├── script.skinvariables/      ← Dynamic skin var engine that drives shortcut tiles
│   ├── script.module.jurialmunkey/
│   ├── script.module.infotagger/
│   ├── script.module.addon.signals/
│   ├── script.module.qrcode/
│   ├── script.module.six/
│   ├── script.texturemaker/
│   ├── resource.font.robotcjksc/
│   ├── resource.images.studios.coloured/
│   └── resource.images.weathericons.white/
└── userdata/
    ├── Database/
    │   ├── Addons33.db
    │   └── ViewModes6.db
    └── addon_data/
        ├── plugin.video.themoviedb.helper/  ← TMDB Helper cache (blur_v3, crop_v2, database_07)
        ├── script.skinvariables/             ← The hub widgets users see on the home screen
        │   ├── nodes/skin.arctic.fuse.3/    ← Pre-configured shortcut tiles
        │   └── skin.arctic.fuse.3-viewtypes.json
        └── skin.arctic.fuse.3/settings.xml
```

## What carries Gears references

After a FenLight→Gears migration, these files in Arctic_Fuse_Skin need to point at Gears, not FenLight:

### `addons/skin.arctic.fuse.3/1080i/Includes_Info.xml`

Has 19 references to `fenlight.<rating>` ListItem properties (tmdb_rating, imdb_rating, metascore_rating, tomatometer_rating, tomatousermeter_rating + their icon variants). All must become `gears.<rating>` — Gears sets the same property names but with the `gears.` prefix (since Kodi auto-prefixes by addon ID basename).

### `userdata/addon_data/script.skinvariables/skin.arctic.fuse.3-viewtypes.json`

Has a `"plugin.video.fenlight": {...}` key mapping content types to view IDs. Rename the key to `"plugin.video.gears"`.

### `userdata/addon_data/script.skinvariables/nodes/skin.arctic.fuse.3/*.json`

Hub widget configs. Files: `skinvariables-shortcut-1101widgets.json`, `1101submenu.json`, `1102widgets.json`, `1102submenu.json`, `homewidgets.json`, `homesubmenu.json`, `searchwidgets.json`, `powermenu.json`, `powertray.json`.

The widget entries use the structure:
```json
{
    "label": "<Hebrew\\uXXXX>",
    "path": "plugin://plugin.video.gears/?mode=...",
    "icon": "<image-url-or-special-path>",
    "target": "videos",
    "guid": "guid-<8hex>",
    "widget_style": "Card" | "Square"
}
```

For the Gears migration, all `plugin.video.fenlight` paths → `plugin.video.gears`. The `iconImage=` URL params pointing to `FenlightAnonyMouse.github.io` are dead — replace with simple icon names like `"folder"`, `"trending"`, etc. (Gears resolves these via its own icon resolver).

### `userdata/Database/Addons33.db` + `ViewModes6.db`

Same migration as the base build (`references/base-build.md`):
- `installed`: remove fenlight, add gears + chainsrepo + gearsscrapers with `origin=''`
- `update_rules`: 3 rows with `updateRule=2`
- `ViewModes6.db`: `REPLACE(path, 'plugin.video.fenlight', 'plugin.video.gears')`

## Hub widgets architecture

Arctic Fuse organizes its home screen as **hubs**. Each hub has a content area (widgets) and an optional submenu strip. Hub IDs in use:

| Hub | Submenu file | Widgets file | Role |
|---|---|---|---|
| home | homesubmenu.json | homewidgets.json | Main home screen tiles |
| 1101 | 1101submenu.json | 1101widgets.json | "סרטים" (movies) hub |
| 1102 | 1102submenu.json | 1102widgets.json | "חשבון ותחזוקה" (account & maintenance) hub — holds RD/TorBox tiles |
| search | — | searchwidgets.json | Search hub |
| power | powermenu.json | powertray.json | Power menu shortcuts |

The **TorBox Services tile** lives in `1102widgets.json` — a Square widget whose `path` opens the navigator-built shortcut folder named `TorBox+Services`. Symmetric with the existing `RD+Services` tile.

```json
{
    "label": "שירותי TorBox",
    "path": "plugin://plugin.video.gears/?mode=navigator.build_shortcut_folder_contents&name=TorBox+Services&iconImage=folder",
    "icon": "folder",
    "target": "videos",
    "guid": "guid-torbox001",
    "widget_style": "Square"
}
```

The `name=TorBox+Services` URL param must match the `list_name` of a `shortcut_folder` row in `addon_data/plugin.video.gears/databases/navigator.db` (see base-build.md). If the name doesn't match, the tile opens an empty folder.

## What does NOT need a separate "Arctic Fuse Hebrew" mod

Unlike the gears addon (which needs a Hebrew **code** overlay), Arctic Fuse Hebrew support is achieved purely via:
1. Hebrew strings in the skin's already-shipped `strings.po` (Arctic Fuse upstream supports Hebrew).
2. The customizations in `addons/skin.arctic.fuse.3/1080i/*.xml` for Gears-specific rating displays.
3. The pre-configured shortcut JSONs.
4. The skin's `userdata/addon_data/skin.arctic.fuse.3/settings.xml` with Hebrew-friendly preferences.

There is a `skin_hebrew_files.zip` in `pov-modified-heb/` that the wizard installs separately — it's mostly Hebrew fonts + a few skin overlay XMLs. Not part of this `Arctic_Fuse_Skin/` folder; that zip is its own thing maintained in the pov-modified-heb repo's `skin_version.json`.

## Arctic Fuse skin patches (KODIRDIL)

Two patches we apply on top of upstream `skin.arctic.fuse.3` to fix bugs Asaf hit. Both must be re-applied after any Arctic Fuse upstream upgrade.

### 1. OSD auto-close timeout (fixes "info bar doesn't go down without pressing Back")

**File:** `addons/skin.arctic.fuse.3/1080i/Includes_Actions.xml`

**Bug:** The `osd_timeout` alarm uses `$INFO[Skin.String(OSD_Timeout),00:,]` to build the AlarmClock interval — produces malformed strings like `00:5` (for value 5) and `00:0` (for value 0). Kodi can't parse those, so the alarm never fires and the OSD stays open until the user presses Back. Also, the condition gates on `!String.IsEmpty(Skin.String(OSD_Timeout))` — since the default `OSD_Timeout` is empty string, the alarm never starts at all out-of-box.

**Fix:** Use a skin variable to handle empty/0 fallback (`Skin.String(name,value)` is a BOOLEAN comparison, NOT a default-getter — `$INFO[Skin.String(OSD_Timeout,5)]` evaluates as a condition and returns junk, breaking AlarmClock). Plain seconds interval. Keep the empty-string gate so users can opt-out by clearing the setting; build settings.xml ships `5` so default works OOB.

```xml
<variable name="OSD_Timeout_Seconds">
    <value condition="String.IsEmpty(Skin.String(OSD_Timeout))">5</value>
    <value condition="Skin.String(OSD_Timeout,0)">5</value>
    <value>$INFO[Skin.String(OSD_Timeout)]</value>
</variable>

<!-- in Action_OSD_SuspendAutoClose: replace the interval -->
<onunload condition="!String.IsEmpty(Window(Home).Property(osd_timeout_$PARAM[window]))">AlarmClock(osd_timeout,RunScript(script.skinvariables,run_executebuiltin=special://skin/shortcuts/builtins/skinvariables-closeosd.json,use_rules=True),$VAR[OSD_Timeout_Seconds],silent)</onunload>

<!-- in Action_OSD_Button (onfocus + onclick): keep !String.IsEmpty gate, replace interval -->
<onfocus condition="!String.IsEmpty(Skin.String(OSD_Timeout)) + [Window.IsVisible(videoosd) | Window.IsVisible(musicosd)]">AlarmClock(osd_timeout,RunScript(...),$VAR[OSD_Timeout_Seconds],silent)</onfocus>
```

Plus set default in `userdata/addon_data/skin.arctic.fuse.3/settings.xml`:
```xml
<setting id="OSD_Timeout" type="string">5</setting>
```

**Pitfall (learned the hard way):** my first attempt used `$INFO[Skin.String(OSD_Timeout,5)]` thinking the second arg was a default. It's not — it's a comparison. The InfoLabel returned junk and AlarmClock fired immediately, closing the OSD the moment the user hovered any button. The variable above does the fallback correctly. **Skin.String(name) returns the value; Skin.String(name,X) returns true/false based on equality with X.**

### 2. OSD self-destruct timer on open (fixes "Enter-to-open never auto-closes")

**File:** `addons/skin.arctic.fuse.3/1080i/VideoOSD.xml`

**Bug:** The `osd_timeout` alarm only fires when an `onfocus` event happens on a button (via `Action_OSD_Button`'s onfocus include). But Kodi does NOT fire `onfocus` for the **initial default control** when a window opens. So when the user raises the OSD via Enter (keyboard) or the center button (remote) — which opens with focus already on Play/Pause (id=6001) — no `onfocus` ever fires for that control, no alarm gets set, and the toolbar lingers forever.

This is on top of patch #1 (which fixed the broken `00:5` interval format) — that's necessary but not sufficient because it still relies on focus events to start the timer.

**Fix:** Add an `<onload>` AlarmClock to the VideoOSD.xml window itself so opening the window always starts a self-destruct timer regardless of how it was opened:

```xml
<window>
    ...
    <onload>SetProperty(UID,$ESCINFO[Player.Title]$INFO[Player.Time(ss)],1146)</onload>
    <onload>SetProperty(UID,$ESCINFO[Player.Title]$INFO[Player.Time(ss)],1147)</onload>
    <!-- KODIRDIL: self-destruct. Guard with !System.HasAlarm so we don't stomp on an
         alarm an onfocus event may have set just before/after the window loaded. -->
    <onload condition="!System.HasAlarm(osd_timeout)">AlarmClock(osd_timeout,RunScript(script.skinvariables,run_executebuiltin=special://skin/shortcuts/builtins/skinvariables-closeosd.json,use_rules=True),$VAR[OSD_Timeout_Seconds],silent)</onload>
    <onunload>CancelAlarm(osd_timeout,true)</onunload>
```

The `!System.HasAlarm(osd_timeout)` guard prevents racing with onfocus alarms (mouse-hover scenario) — if onfocus already set a 5s timer, onload sees it and skips.

**Coverage matrix** (all 5 OSD-open paths now handled):

| Trigger | Timer source |
|---|---|
| Spacebar / remote pause button (no toolbar) | Native `Player.Paused` + skin's `osd.autoonpause` setting |
| Mouse to bottom → hover button | `onfocus` on button → AlarmClock (5s) |
| Arrow keys → focus button | `onfocus` on button → AlarmClock (5s) |
| Enter / center button → opens toolbar | NEW: `<onload>` on VideoOSD → AlarmClock (5s) |
| Enter on default-focused Play/Pause → toggle | `<onload>` set timer; `onclick` resets it; alarm fires either way |

## Validation

After editing this folder:

```bash
# JSON validity
python -c "import json, glob; [json.load(open(f, encoding='utf-8')) for f in glob.glob('Arctic_Fuse_Skin/userdata/addon_data/script.skinvariables/**/*.json', recursive=True)]"

# XML validity
python -c "import xml.etree.ElementTree as ET, glob; [ET.parse(f) for f in glob.glob('Arctic_Fuse_Skin/addons/skin.arctic.fuse.3/1080i/*.xml')]"

# No fenlight residue
grep -rln -i "fenlight\|FenlightAnony" Arctic_Fuse_Skin/ | grep -v '\.db$\|\.git/'
```

DB residual check is via the audit script (`scripts/scan_residual_fenlight.py`).
