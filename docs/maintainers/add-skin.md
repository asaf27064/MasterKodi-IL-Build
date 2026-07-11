# Adding an optional skin (worked example: Arctic Zephyr 2 Resurrection Mod)

How to add a third-party Kodi skin to the build as an **optional** skin, the way
Nimbus and AF3 are added. "Optional" means: it ships in `build.json`'s
`channels.optional`, so the modular updater only installs it for users who
actually picked it (`if channel=='optional' and installed is None: continue`) —
it is **never** auto-pushed to everyone.

The Zephyr integration (wizard 2.4.21) is the reference. Repo:
`github.com/DenDyGH/skin.arctic.zephyr.2.resurrection.mod`.

---

## 0. Pick the right *release*, not the main branch

Skins on GitHub track their author's dev branch, which usually targets the
*next* unreleased Kodi and demands dependency versions we don't ship.

- Zephyr **main** = Piers/Kodi 22, wanted `script.skinshortcuts 3.0.0` (doesn't
  exist on any mirror).
- The per-Kodi **Omega (Kodi 21) release** (`v1.0.51`) only requires
  `script.skinvariables >= 2.1.28` and `plugin.video.themoviedb.helper >= 5.5.11`
  and takes `script.skinshortcuts` + the resource-image packs as `(any)`.
  Our build already ships skinvariables 2.1.35 and themoviedb.helper 6.15.0, so
  **zero conflict with AF3**.

> Rule: always take the release tagged for the Kodi version the build runs on
> (Omega / Kodi 21 today). Read its `addon.xml` `<requires>` and match every
> `<import>` against what the build already ships before sourcing anything.

## 1. Resolve every dependency

A skin will not `enable` unless every `<import>` addon is present **and enabled**.
List them from the release `addon.xml`, then subtract what the build already has.

Zephyr Omega needed: `script.skinshortcuts`, `script.skinhelper`,
`script.module.simplejson`, `script.module.unidecode`,
`resource.images.studios.white`, `resource.images.moviegenreicons.transparent`,
`resource.images.moviecountryicons.maps` (skinvariables + themoviedb.helper
already shipped).

Sourcing order of preference:
1. The skin author's own repo/datadir (Zephyr: `dendygh.github.io` hosts
   themoviedb.helper + skinvariables; `github.com/DenDyGH/script.skinhelper`
   releases host skinhelper 0.0.4).
2. The **Kodi mirror** `mirrors.kodi.tv/addons/omega/<addon.id>/` — authoritative
   for stock resource packs + script modules.

Take the `(any)`-compatible version that exists on a mirror. Zephyr used
`script.skinshortcuts 2.0.3` from the Kodi mirror (Omega accepts `(any)`), not
the 3.0.0 the main branch wanted.

### Patching out an unshippable dependency

`script.skinhelper 0.0.4` hard-required `script.module.pil`, which isn't on any
mirror. PIL was used **only** for a cosmetic button-gradient. Fix:
- Remove `<import addon="script.module.pil" .../>` from skinhelper's `addon.xml`.
- Guard the import: `try: from PIL import Image ... except Exception: Image = None`
  and skip the gradient when `Image is None`.

Do this in the extracted addon under `addons/`, and note it — an upstream
skinhelper bump would re-introduce the requires.

## 2. Extract into `addons/` and de-version nothing

Extract each addon into `addons/<id>/`. Do **not** rename or strip versions —
the CI (`.github/workflows/build-and-release.yml`) zips each `addons/<id>` into
`<id>-<version>.zip` and writes the manifest from each `addon.xml`.

## 3. Hebrew: fontset, not lookandfeel

`lookandfeel.font` is **global** across skins. A skin whose fontsets are all
Latin (Zephyr's `Default` = RobotoCondensed) renders Hebrew as "NO GLYPH" boxes.
Fix per-skin:
- In the skin's `<res>/Font.xml` (Zephyr: `1080i/Font.xml`), clone the `Default`
  `<fontset>` to a new `<fontset id="Hebrew">` with `name` "Hebrew (Rubik)" and
  swap each `<filename>` from Roboto* to `Rubik-*.ttf` / `NotoSansHebrew-*.ttf`.
- Copy those TTFs into the skin's `fonts/` dir (borrow from the AF3 overlay).
- The wizard's `set_skin_font(skin_id)` selects the right fontset on switch —
  register the mapping in `builds.py` `SKIN_FONTSET`
  (`'<skin.id>': 'Hebrew (Rubik)'`).

## 4. Wire the wizard (`resources/libs/builds.py`)

Add the skin to **all** of these, or the skins menu / font / install path breaks:

- `SKIN_FONTSET[<skin.id>] = 'Hebrew (Rubik)'`
- `OPTIONAL_SKINS[<key>] = {'id':.., 'name':.., 'manifest_install': True, 'deps':[..all dep ids..]}`
- `_SKIN_CATALOG += (<key>, <name>, <skin.id>, '<preview>.jpg')`
- `_OPTIONAL_SKIN_IDS += <skin.id>`

**`manifest_install`** matters: AF3/Nimbus ship as a single `build.txt` zip that
bundles their deps, so their installer just extracts one zip. A skin whose deps
are separate addons (Zephyr) needs `_install_from_manifest(addon_id, deps, name)`
— it fetches the live manifest and installs **deps first, then the skin** via
`mu._apply_one(entry)`, then `UpdateLocalAddons()`. `install_skin()` and
`_skin_switch_flow()` branch on `manifest_install` (no build.txt URL).

Add a preview `<key>.jpg` (a screenshot from the skin's `resources/` works) for
the picker.

## 5. `build.json`

Add the skin **and every dep** to `channels.optional`. Bump `config_version`
only if you also changed `config/`.

## 6. Verify before shipping

```
py tools/apply_overlay.py overlays addons --verify   # if the skin touches an overlay (rare)
py -c "import ast; ast.parse(open('addons/plugin.program.masterkodi.il.wizard/resources/libs/builds.py',encoding='utf-8').read())"
# CI builds every addons/<id> into a reproducible zip and writes manifest.json
```

After push, confirm the shipped `manifest.json` lists the skin + all deps with
`channel == optional`, and the wizard version bumped.

## 7. What is NOT free (needs on-device work)

- **Home arrangement / hub widgets** — driven by the skin's menu engine
  (`script.skinshortcuts` for Zephyr, `script.skinvariables`/HomeSwitcher for
  AF3). The curated Hebrew home menu is bespoke per skin and must be built
  against that skin's actual node/widget templates. Build a default and test it
  live; do not fabricate a `.DATA.xml` blind.
- **he_il translation** — copy the skin's `en_gb/strings.po` to a new
  `resource.language.he_il/` and translate `msgstr`s. Reuse AF3's vetted he_il
  where an English `msgid` matches. Missing strings fall back to en_gb.
- **Power menu items** — if the skin's power menu is a skinshortcuts group
  (`skinshortcuts-group-powermenu`), add items via skinshortcuts defaults, not
  static `<item>` XML.
- **On-device load test** — enable the skin on a real box; confirm no missing
  dependency dialog, Hebrew renders, home menu populates.
