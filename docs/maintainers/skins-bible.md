# THE SKINS BIBLE — everything about all four skins (2026-07-15)

How each skin works, what it needs, which files to edit for every kind of change,
how settings are delivered without clobbering users, and every trap we've hit.
Written at the end of the full 4-skin review (config 26, wizard 2.4.58).

---

## 0. The universal delivery model (applies to ALL skins)

### Three delivery channels
| Channel | What it carries | Reaches |
|---|---|---|
| **Manifest** (`manifest.json` + `addons-latest` release, built by CI from `addons/`) | the skin addon itself (XML, fonts, language) | everyone, next update check; sha-change is enough (no version bump strictly required, but bump anyway) |
| **Config** (`config-N.zip` built from `config/`, applied per `config/config_policy.json`) | userdata: skin settings.xml, skinvariables nodes, viewtypes json, guisettings, DBs | everyone on `config_version` bump; fresh installs on install |
| **Base/skin zips** (github.io v1.0: `FenLight_Estuary.zip`, `Arctic_Fuse_Skin.zip`, `nimbus.zip`) | first-install payload (addons + userdata + curated DBs) | NEW installs only; must be rebuilt repo-based after a review batch |

### Settings without clobbering (config_policy modes)
- `merge_seed` (per `<setting id>`): adds ids the user lacks, NEVER overwrites — the default for all skin settings.
- `force_ids` list on a file entry: those ids ARE overwritten (build wins) — use for every reviewed default you want on all devices; `exclude_ids` (credentials) always win.
- `replace` / `seed_if_absent` / `merge_name`: whole-file modes (favourites=replace, DBs=seed_if_absent, sources=merge_name).
- Bump `config_version` in BOTH `build.json` and `config_policy.json` on ANY config change.

### THE THREE CLOBBER TRAPS (memorize)
1. **Kodi exit-rewrite**: Kodi holds guisettings + every skin settings.xml in memory and REWRITES them on exit. Any live-file edit while Kodi runs is silently reverted. ALWAYS close Kodi before editing (`taskkill //IM kodi.exe`, poll, force after 20s).
2. **First-boot materialization**: a freshly installed skin writes dozens of its own default keys to settings.xml on first boot — AFTER config applied. merge_seed then "respects" those skin defaults forever. Fix: `force_ids` for every default we care about.
3. **Startup factory blocks** (worst): some skins ACTIVELY run `Skin.SetBool/SetString` on first boot. Zephyr's `1080i/Startup.xml` `!Skin.HasSetting(SkinInit)` block set `HomeMultiVertical` + `Icons=monochrome`, defeating config on every fresh install (caused the stacked-layouts frozen home). When a default keeps reverting on FIRST boot, grep the skin for `Skin.SetBool(<id>)` / `Skin.SetString(<id>` — check Startup.xml first. Fix at the source (overlay the file) AND keep the force_id.

### Overlay-managed vs committed skins
- **Overlay-managed** (gears, AF3, Zephyr): `addons/<id>/` is GITIGNORED; source of truth is `overlays/<id>/files/` + `base.json` (clean upstream base + our diff files). CI reconstructs via `tools/apply_overlay.py overlays addons`. EDIT WORKFLOW: edit the `addons/<id>/` working copy AND copy the file into `overlays/<id>/files/<same path>` (create if the file wasn't overlaid before — e.g. Zephyr Startup.xml), bump the skin `addon.xml` version in BOTH copies + `base.json overlay_version`, then `apply_overlay.py --verify` must say identical.
- **Committed** (Estuary, Nimbus): edit `addons/<id>/` directly, it's git-tracked. Ships by sha (Estuary/Nimbus keep Kodi-official version numbers — do NOT bump past upstream).

### Pinning (who may auto-update)
`MODDED_ADDONS` (modular_update.py) = gears, all 4 skins, skinhelper, gearsscrapers, gearsai, skipintro, firstrun → pinned (`installed.origin=''` + `update_rules updateRule=1`). Vanilla deps (skinvariables, skinshortcuts, tmdbhelper, texturemaker, jurialmunkey, resource.*, simpleeval...) DELIBERATELY auto-update from their repos; `tools/refresh_vanilla_deps.py` re-vendors them into `addons/` periodically. Zip skin installs pin via `builds._pin_addons_in_db` (filtered to MODDED_ADDONS). Do NOT over-pin vanilla deps — that was reverted once already.

---

## 1. ESTUARY (skin.estuary) — base skin, always installed
- **Delivery**: committed in `addons/skin.estuary/` (core channel), Hebrew-capable fontsets baked. First install via `FenLight_Estuary.zip` (repo-based rebuild method in fresh-install-completion memory).
- **Deps**: none beyond Kodi (it's the bundled skin, version stays 4.0.0).
- **Home menu**: STATIC XML — `addons/skin.estuary/xml/Home.xml`, `<fixedlist id="9000">` `<item>`s (TABS not spaces; items 6 tabs, props 7). Each category item has `menu_id` linking to a submenu `<control type="group" id="91X0">` (visible when focused) containing panel `91X1`. Our categories: סרטים/סדרות/רשתות/חיבור שירותים(TorBox)/תחזוקה(4 wizard actions)/החלפת סקין. Content = favourites.xml entries or direct RunPlugin/RunScript.
- **To change the menu**: edit Home.xml directly → ships by sha. No rebuild step needed (read at skin load).
- **Fonts**: `xml/Font.xml`; fontsets Default(Noto)/Hebrew (Rubik|Noto|Assistant|Heebo)/Arial. PICKER-NAME TRAP: fontsets sharing `idloc="15109"` all display "ברירת מחדל של המעטפת" — REMOVE idloc from the Hebrew fontsets so the picker shows the `id`. Global font = `guisettings lookandfeel.font` (force_id, "Hebrew (Rubik)"), subtitles = `subtitles.fontname` (Rubik).
- **Views**: per-path rows in `ViewModes6.db` (config `userdata/Database/ViewModes6.db`, seed_if_absent, whitelisted in build_config.py). 55 curated Estuary rows.
- **Widgets**: driven by the Home.xml panels + favourites; network icons row uses `media/build_icons/` (DO NOT TOUCH per Asaf) — separate from gears `network_icons/`.

## 2. ARCTIC FUSE 3 (skin.arctic.fuse.3) — optional, zip-installed
- **Delivery**: OVERLAY-managed (base = jurialmunkey tag v3.2.13 zip, 13-19 overlay files: fonts, Custom_1147 OSD button, he_il). First install via `Arctic_Fuse_Skin.zip` (12 addons + userdata + curated Addons33/ViewModes; rebuilt repo-based 2026-07-14). Updates via manifest (optional channel — only devices that installed it).
- **Deps** (in the zip / auto-updating vanilla): script.skinvariables (>=2.2.2!), script.texturemaker, plugin.video.themoviedb.helper, script.module.jurialmunkey, addon.signals, infotagger, qrcode, six, resource.font.robotocjksc, resource.images.studios.coloured + weathericons.white.
- **Home menu**: `homeswitcher.NNNN.*` skin settings (1101 סדרות, 1102 חיבור שירותים, 1103 תחזוקה, 1104 spare; name/icon/toggle per slot in `config/.../skin.arctic.fuse.3/settings.xml`, force_id'd) + **skinvariables node JSONs** in `config/userdata/addon_data/script.skinvariables/nodes/skin.arctic.fuse.3/skinvariables-shortcut-*.json` (1101/1102/1103 widgets+submenu, homewidgets, powermenu, powertray, searchwidgets — ALL must be listed in config_policy as replace!).
- **CRITICAL: the menu is COMPILED.** Node JSONs are compiled by script.skinvariables' generator (`action=buildtemplate`) into `1080i/script-skinvariables-generator-includes-.xml`. Editing a node JSON does NOTHING until buildtemplate reruns (hash-checked on node contents). The wizard service runs plain (NOT forced) buildtemplate+buildviews on the post-install marker; AF3 also self-builds on fresh-install first boot (1-2 extra splashes, upstream behavior, unavoidable).
- **Categories show WIDGETS, not submenu buttons.** A category = widget(s) opening a browsable folder. Gears folders can only run gears modes; for non-gears actions use the WIZARD's plugin folder: `plugin://plugin.program.masterkodi.il.wizard/?mode=maintenance_folder` (4 tiles; icons bundled at wizard `resources/art/maint/` — special://skin icon paths only resolve on AF3, never use them cross-skin).
- **Views**: `config/.../script.skinvariables/skin.arctic.fuse.3-viewtypes.json` (policy entry, fresh=replace/update=seed_if_absent) + ViewModes AF3 rows in the skin zip. gears: movies/tvshows/sets=53? (per json), compiled by `action=buildviews` (hash-skips unless skinviewtypes hash cleared).
- **Fonts**: fontsets incl "Hebrew (Rubik)" (default via wizard SKIN_FONTSET). AF3's "Default" fontset is LATIN-ONLY — never leave lookandfeel.font=Default on AF3.

## 3. NIMBUS (skin.nimbus) — optional, zip-installed
- **Delivery**: COMMITTED in `addons/skin.nimbus/` (0.1.43, ships by sha). First install via `nimbus.zip` (2 addons: skin + script.nimbus.helper, + settings.xml + cpath_cache.db + nimbus-only ViewModes; rebuilt repo-based 2026-07-15).
- **Deps**: script.nimbus.helper (required, generates menu XML), themoviedb.helper etc. from the base build.
- **Home menu = THREE synchronized layers** (change ALL three):
  1. Skin settings: `MenuCustomNLabel` + `homemenunocustomNbutton` (custom slots 1-3; Custom1=חיבור שירותים, Custom2=תחזוקה) — in config settings.xml with force_ids.
  2. `script.nimbus.helper`'s `cpath_cache.db` (table custom_paths: cpath_setting like `custom1.main_menu`/`custom1.widget.1`, path, header) — seeded at `config/userdata/addon_data/script.nimbus.helper/cpath_cache.db` (whitelisted in build_config).
  3. GENERATED skin XML: `xml/script-nimbus-main_menu_customN.xml` + `script-nimbus-widget_customN.xml` — committed in the repo skin (label + onclick + WidgetListCategory include with content_path/widget_header/list_id; custom1=23000/23011, custom2=24000/24011, custom3=25000).
  Static items (music/livetv/החלפת סקין etc.) are inline in `xml/Home.xml` fixedlist 9000.
- **Widgets per menu type**: `listlandscape.<type>` skin settings (0/1) control List-view layout art (seasons=1 = landscape).
- **Views**: ViewModes6.db rows (12 curated; note some path keys embed the Windows install dir via gears iconImage — they seed Windows installs, Android falls back to defaults). Views named: List=50, PosterFlow=51, IconWall=52, Flix(קיר מידע)=54, FlixScape(קיר מידע רחב)=55, FlixList=56, Wall=500, WallScape=501. Nimbus stores views ONLY in ViewModes (no viewtypes json).
- **Fonts**: same idloc picker-name trap as Estuary — fixed (idloc removed from Hebrew fontsets).
- **Hebrew rendering traps fixed**: ★ (U+2605) after ratings tofus in Hebrew fonts (removed from Includes_FlixPanel/Variables_FlixPanel/View_51_PosterFlow); `GenreVar` had 120 English-only conditions → Hebrew genres rendered an EMPTY chip (added plain `$INFO[ListItem.Genre]` fallback in Variables_MyVariables.xml).
- **Power menu**: `xml/DialogButtonMenu.xml` (has our skin-switch/cache items).

## 4. ARCTIC ZEPHYR 2 RESURRECTION MOD — optional, MANIFEST-installed
- **Delivery**: OVERLAY-managed (base = DenDyGH tag v1.1.9 **Omega** asset; overlay ~30 files). NO zip — `manifest_install: true` in builds.OPTIONAL_SKINS with explicit deps list. Current 1.0.51.26.
- **Deps** (installed by the wizard from manifest): script.skinshortcuts (+its dep script.module.simpleeval!), script.skinhelper (PIL-stripped), script.module.simplejson, unidecode, skinvariables, themoviedb.helper, resource.images.studios.white / moviegenreicons.transparent / moviecountryicons.maps. LESSON: always BFS the full dep closure — simpleeval was missed once (skin hung on load).
- **Home menu = classic script.skinshortcuts**, THREE parts in `userdata/addon_data/script.skinshortcuts/`:
  1. `mainmenu.DATA.xml` (+ per-item submenu `<slug>.DATA.xml`, widget groups `<slug>-1.DATA.xml`)
  2. `<skinid>.properties` (JSON rows `[group, rowslug, prop, value]` — widgetPath/widgetTarget/widgetAspect/icon/thumb/translatedPath)
  3. compiled `1080i/script-skinshortcuts-includes.xml` — rebuilt when `<skinid>.hash` is DELETED (the hash does NOT cover template changes!) on next boot's `buildxml`; DISPLAY picks it up one boot later (2 boots per iteration).
- **THE SLUG TRAP**: widgets/submenus are keyed by slug = `re.sub('[^a-z0-9]','',unidecode(label).lower())` (חיבור שירותים=hybvrshyrvtym, תחזוקה=thzvkh, סרטים=srtym). RENAMING a menu item label DETACHES its widgets — clone `<oldslug>-1.DATA.xml` → `<newslug>-1.DATA.xml` and duplicate the .properties rows to the new slug, remove orphans.
- **Menu delivery to users**: skin's default `shortcuts/mainmenu.DATA.xml` (overlay) = fresh installs; wizard `resources/menu_defaults/<skinid>/` bundle (skinshortcuts/ userdata files + includes/ compiled files + VERSION) re-laid by `repair_skin_menu` when menu broken OR bundle VERSION changed = EXISTING users. After menu changes: harvest the built+verified state from the live box into the bundle, bump VERSION, bump wizard. Bundle must be CLEAN (no machine paths — grep for `C%3a`/`C:\MasterKodi`).
- **Startup factory block** (`1080i/Startup.xml`, now overlaid): sets our defaults on first boot — HomeMultiFlixView + Icons=colorful (upstream set Vertical+monochrome → the frozen-home bug).
- **Home layouts** (mutually exclusive bools, ALL force_id'd in config): homemulti + homemultiflixview (OURS) / HomeMultiVertical / homemultinetflix / homemultihorizontal / homebasic. Both-true = two layouts render STACKED = frozen foreground (keyboard focuses the hidden layer). Diagnose live via JSON-RPC (TCP 9090): `Skin.HasSetting(...)`, `System.CurrentControlId`.
- **Views**: skinvariables viewtypes json `config/.../skin.arctic.zephyr.2.resurrection.mod-viewtypes.json` (policy entry): gears movies/tvshows=53, seasons=52, episodes=529, menus(none)=50. Applied by `action=buildviews` (hash-skips; the wizard's post-install rebuild clears the skinviewtypes hash strings for skinshortcuts skins only).
- **Fonts**: fontsets fine (no idloc bug). Active default "Hebrew (Noto)" history: Rubik tofus in `<textbox>`; plot fonts defined OUTSIDE fontsets in Defaults.xml (swapped to NotoSansHebrew). When ONE control tofus under EVERY fontset, grep for its font name outside Font.xml.
- **KNOWN OPEN BUG**: Kodi 21.3 python invoker crash under Zephyr's widget storm (every Home init refreshes ~13 gears CDirectoryProviders). Root-caused, mitigations failed+reverted — full writeup: `docs/maintainers/kodi-python-crash-investigation.md`. Best lead: newer Kodi build.

---

## 5. Cross-skin cookbook (which files for which change)

| Change | Estuary | AF3 | Nimbus | Zephyr |
|---|---|---|---|---|
| Menu item add/rename/reorder | `xml/Home.xml` | `homeswitcher.*` settings (config) | settings labels + cpath_cache.db + `script-nimbus-main_menu_*.xml` | `mainmenu.DATA.xml` (overlay default + wizard bundle + live) — mind the SLUG trap |
| Category widgets | Home.xml panels/favourites | node JSONs + buildtemplate | `script-nimbus-widget_*.xml` + cpath db | `<slug>-1.DATA.xml` + .properties + hash-delete |
| Non-gears action tiles | direct RunPlugin/RunScript in Home.xml | wizard `?mode=maintenance_folder` | same wizard folder | same wizard folder |
| Views | ViewModes6.db | viewtypes json (+buildviews) | ViewModes6.db | viewtypes json (+buildviews) |
| Fonts default | guisettings lookandfeel.font (force_id) | wizard SKIN_FONTSET on switch | same | same |
| Font picker names | Font.xml remove idloc | (ok) | Font.xml remove idloc | (ok) |
| Power menu | DialogButtonMenu.xml | powermenu node JSON | DialogButtonMenu.xml | Includes_Items.xml Items_PowerMenu |
| Setting default for everyone | config settings.xml + force_ids + config bump | same | same | same + check Startup.xml factory block |

## 6. Ship checklist (every skin batch)
1. Close Kodi before touching live files; Asaf opens (never auto-launch).
2. Every change goes to the REPO immediately (config/ or addons/+overlay) — verify by grep, "בלי פאדיחות".
3. Overlay skins: file into overlays/files/ too, bump addon.xml both copies + base.json, `apply_overlay.py --verify`.
4. config change → policy entry if a NEW file + force_ids for changed defaults → bump config_version (build.json + policy).
5. Wizard change → bump wizard version (manifest won't ship same-version) → EXE/APK auto-rebuild.
6. Dry-run `tools/build_config.py` and INSPECT the zip (files present, values right, DBs whitelisted).
7. Push → wait build-and-release → verify LIVE manifest (raw CDN caches ~5min) → download the actual shipped zips and inspect.
8. Zip-installed skins: rebuild the github.io v1.0 zip repo-based after the batch (`gh release upload v1.0 --repo asaf27064/asaf27064.github.io <zip> --clobber`); zips must ship WITH the config bump (new zip + old config = stale look).
9. Final proof: wipe the skin completely from the live box + reinstall through the wizard = the exact user experience.
