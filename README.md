# MasterKodi IL — Build

Single source of truth for the **MasterKodi IL** Hebrew Kodi build. Every addon
lives here; a GitHub Action ships changes automatically with a hash-verified
manifest. You bump a version and push — CI does the rest.

## How it works

```
addons/<id>/            all addon sources (consolidated)
config/userdata/        clean default config (secrets scrubbed)
build.json              build metadata + channels (core vs optional skins)
manifest.json           GENERATED — the client update contract
tools/                  build + manifest pipeline
.github/workflows/      CI
```

### Pipeline (on every push to `main`)

1. `tools/build_addons.py` — zips each `addons/<id>/` into a **reproducible**
   `dist/<id>-<version>.zip` (fixed entry order + timestamps, so the sha256 only
   changes when content changes).
2. `tools/build_config.py` — zips `config/` into `config-<n>.zip`.
3. `tools/gen_manifest.py` — writes `manifest.json` (id, version, channel,
   sha256, size, url per addon) and computes `dist/changed.txt`.
4. CI uploads **only changed** assets to the rolling `addons-latest` release,
   prunes orphans, and commits the refreshed `manifest.json`.

`check-upstream-gears.yml` watches upstream The Gears every 6h and opens a
tracking issue when a new version appears.

### The client (wizard)

The MasterKodi wizard reads `manifest.json`, compares each addon's installed
version+sha256, downloads only what differs, **verifies the hash**, extracts, and
registers it in Kodi's `Addons33.db`. Optional skins (`skin.arctic.fuse.3`,
`skin.nimbus`) update only if already installed. First install still ships via
the native EXE/APK bootstrap.

Manifest URL: `https://raw.githubusercontent.com/asaf27064/MasterKodi-IL-Build/main/manifest.json`

## Releasing a change

1. Edit the addon under `addons/<id>/` and bump its `addon.xml` `version`.
2. `git commit && git push`.
3. CI ships it. Users get it on next wizard update check.

## Security

Public repo. Never commit secrets: debrid/Trakt tokens, Gemini/API keys, the
signing keystore. `.gitignore` blocks the common ones; `config/userdata` ships
**clean defaults only**.
