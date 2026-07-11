# Base build (`FenLight_Estuary/`)

This is the working tree of the **base build** — what the wizard installs as the user's `~/AppData/Roaming/Kodi` (Windows) or `/sdcard/Android/data/.../files/.kodi` (Android). The folder is misleadingly named "FenLight_Estuary" for historical reasons; **its contents target Gears, not FenLight**. Don't rename unless you also update all the build scripts.

## Layout

```
FenLight_Estuary/
├── addons/
│   ├── plugin.video.gears/           ← Gears 2.0.7 + Hebrew overlay applied
│   ├── repository.chainsrepo/        ← Chains repo (where Gears comes from)
│   ├── script.module.gearsscrapers/  ← Required Gears dep
│   ├── skin.estuary/                 ← Customized Estuary with gears.* rating properties
│   ├── repository.MasterKodiIL/      ← Asaf's repo (wizard updates from here)
│   ├── repository.burekasKodi/       ← Hebrew subtitles repo
│   ├── plugin.program.autocompletion/
│   ├── service.subtitles.All_Subs/
│   ├── service.subtitles.all_subs_plus/
│   ├── resource.language.he_il/      ← Hebrew locale
│   └── [supporting modules: requests/urllib3/certifi/idna/chardet/cocoscrapers/magneto/...]
├── userdata/
│   ├── sources.xml                   ← Repo sources for Kodi's file browser
│   ├── favourites.xml                ← Hebrew shortcuts (RD/TorBox connect, networks, etc.)
│   ├── guisettings.xml
│   ├── Database/
│   │   ├── Addons33.db               ← Installed addons + repo + update_rules
│   │   └── ViewModes6.db             ← Saved view modes per plugin path
│   └── addon_data/
│       ├── plugin.video.gears/       ← Settings, navigator, custom shortcut folders
│       ├── script.module.cocoscrapers/
│       ├── script.module.magneto/    ← Magneto scraper config (Hebrew sources)
│       └── ...
└── media/
    └── build_icons/                  ← PNGs referenced by favourites.xml thumbs
```

## `userdata/sources.xml`

Repository sources for "Install from zip". The chainsrepo entry is critical — without it, the gears addon has no update path.

Required source for Gears:
```xml
<source>
    <name>repository.chainsrepo</name>
    <path pathversion="1">https://unhingedthemes.github.io/</path>
    <allowsharing>true</allowsharing>
</source>
```

Other entries (burekasKodi, coco, kodifitzwell, kodi7rd, peno64, jurialmunkey, MasterKodiIL) carry over from previous builds and aren't related to the Gears migration.

## `userdata/favourites.xml`

The Hebrew top-level favourites shown on Estuary's home screen. Standardized format:
```xml
<favourite name="<Hebrew label>" thumb="special://home/media/build_icons/<png>">RunPlugin(plugin://plugin.video.gears/?mode=<action>)</favourite>
```

Three categories of entries:

**Debrid connect/disconnect trios (top of file):**
- Real Debrid: `real_debrid.authenticate`, `real_debrid.rd_account_info`, `real_debrid.revoke_authentication`
- TorBox: `torbox.authenticate`, `torbox.tb_account_info`, `torbox.revoke_authentication`

**Main content shortcuts:** Movies/TV trending, in-progress, next episodes, genres. URLs use `mode=build_movie_list&action=trakt_movies_trending` etc. — the action handlers are unchanged from FenLight (Gears didn't rename them).

**Network tiles:** `mode=build_tvshow_list&action=tmdb_tv_networks&key_id=<N>`. Network IDs: Netflix=213, Amazon=1024, Apple TV+=2552, Disney+=2739, HBO Max=3186, Hulu=453, Paramount+=4330, ABC=2, FOX=19, HBO=49, NBC=6, The CW=71.

**Pitfall:** Don't leave `iconImage=https%3A%2F%2F...FenlightAnonyMouse...` URL params in favourites — they 404 (FenLight's developer pulled the project). Strip them or replace with local `special://home/media/build_icons/<X>.png` references.

**Image files referenced (must exist in `media/build_icons/`):**
- `Real_Debrid_Auth.png`, `Real_Derbid.png` (yes, typo carried over), `Real_Debrid_Revoke.png`
- `TorBox_Auth.png`, `TorBox.png`, `TorBox_Revoke.png` ← **these need to be added** if you haven't yet
- All the network logo PNGs (Netflix, Amazon, etc.)
- Content category icons (Movies, TV_Shows, Movies_In_Progress, etc.)

## `userdata/addon_data/plugin.video.gears/`

### `databases/settings.db`

This is Gears's settings store. Schema: `settings(setting_id TEXT PK, setting_type, setting_default, setting_value)`. The Hebrew build needs these customizations:

| setting_id | value | Why |
|---|---|---|
| `meta_language` | `he` | Hebrew synopses |
| `mpaa_region` | `IL` | Israeli MPAA ratings |
| `mpaa_region_display_name` | `Israel` | |
| `external_scraper.module` | `script.module.magneto` | Gears uses Magneto as the external scraper |
| `external_scraper.name` | `Magneto Module` | |
| `provider.external` | `true` | Enable external scraper |
| `external.cache_check` | `true` | Cache-only sources |
| `update.action` | `3` | Off (wizard manages updates) |
| `update.action_name` | `Off` | |
| `view.movies` | `51` | Poster view |
| `view.tvshows` | `51` | Poster view |
| `omdb_api` | `459c898b` | OMDB key |
| `addon_icon_choice` | `resources/media/addon_icons/gears_icon_01.png` | Not fenlight_icon_01 |
| `addon_icon_choice_name` | `gears_icon_01.png` | |
| `auto_start_gears` | `false` | Setting renamed from `auto_start_fenlight` |
| `update.username` | `unhingedthemes` | Not FenlightAnonyMouse |
| `update.location` | `unhingedthemes.github.io` | Not FenlightAnonyMouse.github.io |

After a migration, audit with: `python ~/.claude/skills/masterkodi-il-builder/scripts/scan_residual_fenlight.py`.

### `databases/navigator.db`

Custom shortcut folders. Schema: `navigator(list_name TEXT, list_type TEXT, list_contents TEXT)` where `list_contents` is a Python `repr()` of a list of dicts.

Required `shortcut_folder` rows:
- **`RD Services`** — 5 items: Real Debrid auth/info/revoke + clear_all_cache + clean_databases_cache
- **`TorBox Services`** — 3 items: torbox.authenticate / tb_account_info / revoke_authentication
- **`SELECTED NETWROKS`** (typo intentional, don't fix or it will break existing shortcut bindings) — TV networks as TMDB tvshow lists

Format example for TorBox Services:
```python
[
    {'mode': 'torbox.authenticate',           'name': '[B]התחבר ל-TorBox[/B]',     'iconImage': 'torbox', 'isFolder': 'false'},
    {'mode': 'torbox.tb_account_info',        'name': '[B]פרטי מנוי TorBox[/B]',   'iconImage': 'torbox', 'isFolder': 'false'},
    {'mode': 'torbox.revoke_authentication',  'name': '[B]התנתק מ-TorBox[/B]',     'iconImage': 'torbox', 'isFolder': 'false'},
]
```

Store as UTF-8 bytes; sqlite3 handles the encoding fine if you write `str` directly (the table doesn't declare a custom encoding).

### `databases/navigator.db` — default lists

`RootList`, `MovieList`, `TVShowList`, `AnimeList` are `default` rows (the standard top-level menus). Don't touch them unless you're rearranging the main menu.

## `userdata/Database/Addons33.db`

The Kodi database tracking installed addons. After a Hebrew-build migration these must hold:

### `installed` table
For each of `plugin.video.gears`, `repository.chainsrepo`, `script.module.gearsscrapers`:
- `enabled = 1`
- `origin = ''` (empty string — installed manually, not from a repo)
- `disabledReason = 0`

The empty origin is the trick that prevents Kodi from auto-updating these addons. Combined with the `update_rules` entry below, it's belt-and-suspenders.

### `update_rules` table
For each of the three IDs above: `(addonID, updateRule=2)`. The value `2` means "Never" (Kodi never auto-updates this addon).

### `repo` table
Insert `(addonID='repository.chainsrepo', checksum='', lastcheck='', version='0.0.15', nextcheck='')`. This lets Kodi recognize chainsrepo as a repository (so the user can install other chains addons from it manually).

### `addons` table
Stores addon metadata read from `addon.xml`. The Hebrew build's `addons.description` text **must not** mention FenLight. After cloning the working tree from an old fenlight build, run a `REPLACE(description, 'FenLight', 'Gears')` update.

## `userdata/Database/ViewModes6.db`

Schema: `view(idView, window, path, viewMode, sortMethod, sortOrder, sortAttributes, skin)`.

All `path` values starting with `plugin://plugin.video.fenlight/` must be rewritten to `plugin://plugin.video.gears/`. There are about 115 such rows after a clone-and-migrate.

The same paths often contain `iconImage=https%3A%2F%2F...FenlightAnonyMouse...` URL-encoded params — strip those too (~53 of the 115 paths). The view-mode lookup uses the full path as a key, so leaving stale FenLight URLs causes Kodi to miss your saved view mode at runtime.

## Skin layer: `addons/skin.estuary/xml/`

Estuary is customized to read **Gears's** extra rating properties. Properties to support (set by Gears at runtime via `ListItem.setProperty`):
- `gears.tmdb_rating`, `gears.imdb_rating`, `gears.metascore_rating`
- `gears.tomatometer_rating` + `gears.tomatometer_icon`
- `gears.tomatousermeter_rating` + `gears.tomatousermeter_icon`

Skin XML asset paths use `gears_flags/` (not `fenlight_flags/`) — texture files live in the gears addon at `resources/skins/Default/media/gears_flags/ratings/`.

Files that reference these props: `Includes.xml`, `DialogVideoInfo.xml`, `Custom_1107_SearchDialog.xml`, all `View_*.xml`.

## `media/build_icons/`

Build-bundled PNGs referenced by `favourites.xml` and skinvariables shortcuts. Currently missing TorBox icons — add:
- `TorBox_Auth.png` (TorBox logo with green plus or arrow)
- `TorBox.png` (plain TorBox logo for account info)
- `TorBox_Revoke.png` (TorBox logo with red X or unlink)

Match the visual style of the existing `Real_Debrid_Auth.png` / `Real_Derbid.png` / `Real_Debrid_Revoke.png` trio.

## Sync workflow: working tree → Windows build

Every wizard build pipeline-step reads from `MasterKodi_Build/windows/portable_data/`, **not** from `FenLight_Estuary/`. The `portable_data/` tree is a **minimal bootstrap** — just enough to run the wizard. The Hebrew build is installed by the wizard at first-run, fetched from the GitHub Pages release.

So when you change `FenLight_Estuary/`, you do NOT need to copy anything into `windows/portable_data/`. The path is:
- `FenLight_Estuary/` → packaged into `FenLight_Estuary.zip` → uploaded to GitHub Release → wizard downloads on first-run.

See `references/release-packaging.md` for the zip step.
