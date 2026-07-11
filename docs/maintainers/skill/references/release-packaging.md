# Release packaging — `FenLight_Estuary.zip` and `Arctic_Fuse_Skin.zip`

These two zips are the "builds" the wizard installs at first-run, hosted as **release assets** on `https://github.com/asaf27064/asaf27064.github.io/releases/tag/v1.0` (or whatever release tag is current).

## The expected zip structure

The wizard's `extract_zip()` method calls `zipfile.extract(item, HOME)` where `HOME` is Kodi's home directory (`special://home/`). So every entry in the zip is interpreted as a path relative to Kodi home.

**Required:** the zip's namelist must start with directories Kodi expects at home root:
```
addons/...
userdata/...
media/...
```

**Forbidden:** wrapping in a top-level folder like `FenLight_Estuary/addons/...`. Doing this puts files at `<KodiHome>/FenLight_Estuary/addons/` where Kodi can't find them.

## Build the zips with the bundled script

```bash
cd C:\Users\asaf2\Desktop\kodi_project
python ~/.claude/skills/masterkodi-il-builder/scripts/build_release_zips.py
```

The script enters each working tree, zips the **contents** (not the folder itself), and writes:
- `kodi_project/FenLight_Estuary.zip` (~54 MB, ~2300 files)
- `kodi_project/Arctic_Fuse_Skin.zip` (~169 MB, ~4400 files)

It excludes `__pycache__/`, `*.pyc`, `*.pyo`, `.DS_Store`, `.git/`.

## What's inside each zip

### `FenLight_Estuary.zip`
- Base Estuary build with Hebrew customizations
- Includes `addons/plugin.video.gears/` with the Hebrew overlay already applied (so the addon works Hebrew-out-of-the-box; the wizard separately fetches `gears_hebrew_subtitles.zip` for fresh installs to ensure latest version)
- Includes the chainsrepo + gearsscrapers + cocoscrapers + magneto + Hebrew subtitle services
- Includes `addons/service.subtitles.gearsai/` — **MasterKodi AI Subs**, our standalone AI Hebrew subtitle translator (Gemini) + Cloudflare community pool. Standalone subtitle addon, NOT part of the Gears overlay; registered in Addons33.db like the gears trio (`origin=''`, `updateRule=2`). The pool URL/token are baked into `resources/lib/pool.py` (Cloudflare Worker `masterkodi-subpool` is separate deployed infra, not a release artifact). To update it: edit under `FenLight_Estuary/addons/service.subtitles.gearsai/`, bump its `addon.xml` version, rebuild this zip. Existing (already-installed) users don't get it from this zip — they'd need a wizard reinstall or it published to `repository.masterkodi.il` (undecided).
- Pre-populated `Addons33.db` with `installed.origin=''` and `update_rules.updateRule=2` for the gears trio
- `userdata/favourites.xml`, `userdata/sources.xml`, `userdata/addon_data/plugin.video.gears/*.db` all gears-pointed

### `Arctic_Fuse_Skin.zip`
- Arctic Fuse 3 skin + its dependencies (TMDB Helper, skinvariables, jurialmunkey lib)
- TMDB Helper artwork caches (blur_v3, crop_v2) ← bulk of the size
- Pre-configured skinvariables hub widgets (homewidgets, 1101widgets, 1102widgets) with RD + TorBox tiles
- Customized `Includes_Info.xml` reading `gears.*` ListItem properties

## Upload workflow

After running the build script:

```bash
# Manual upload via gh CLI
gh release upload v1.0 FenLight_Estuary.zip Arctic_Fuse_Skin.zip \
    -R asaf27064/asaf27064.github.io --clobber
```

`--clobber` replaces existing assets with the same name. If the release doesn't exist yet, create it first:
```bash
gh release create v1.0 \
    -R asaf27064/asaf27064.github.io \
    --title "MasterKodi IL v1.0" \
    --notes "Initial Gears-based build"
```

## Updating `build.txt` so the wizard sees the new builds

The wizard fetches `https://asaf27064.github.io/assets/build.txt` to discover available builds. Format (one build per line):

```
name="<display name>" url="<zip url>" version="<X.Y.Z>" type="estuary|arctic-fuse" description="<text>"
```

After uploading a new release, edit `build.txt` in the Pages repo (`asaf27064/asaf27064.github.io/assets/build.txt`):

```
name="MasterKodi IL — Estuary"   url="https://github.com/asaf27064/asaf27064.github.io/releases/download/v1.0/FenLight_Estuary.zip"   version="1.0"  type="estuary"      description="Hebrew Kodi build with Gears + Estuary skin"
name="MasterKodi IL — Arctic Fuse" url="https://github.com/asaf27064/asaf27064.github.io/releases/download/v1.0/Arctic_Fuse_Skin.zip" version="1.0"  type="arctic-fuse"  description="Adds Arctic Fuse 3 skin to existing build"
```

The wizard's `BuildManager.fetch_builds_list()` parses this with a homemade splitter on `'" '` — keep the quoting consistent.

## Why two separate zips and not one

The wizard supports two install flows:
1. **Fresh install** — wipe Kodi home, install base build, optionally add Arctic Fuse.
2. **Add Arctic Fuse to existing build** — overlay Arctic Fuse on a running Hebrew Estuary build without wiping.

Flow #2 requires Arctic Fuse to be a separate zip so its extraction doesn't touch the base build's addons/userdata.

## Validating a built zip before upload

Quick smoke test:

```python
import zipfile
with zipfile.ZipFile('FenLight_Estuary.zip') as zf:
    names = zf.namelist()
    # Must start at root
    assert any(n.startswith('addons/') for n in names), "addons/ not at root"
    # No FenLight_Estuary/ prefix
    assert not any(n.startswith('FenLight_Estuary/') for n in names), "wrapped in folder"
    # Gears overlay is present
    assert 'addons/plugin.video.gears/resources/lib/kodirdil/__init__.py' in names
    # Version.txt matches gears_version.json
    v_zip = zf.read('addons/plugin.video.gears/version.txt').decode().strip()
    import json
    v_json = json.load(open('pov-modified-heb/gears_version.json'))['version']
    assert v_zip == v_json, f"version mismatch {v_zip} vs {v_json}"
    print(f"OK: {len(names)} files, gears version {v_zip}")
```

For `Arctic_Fuse_Skin.zip`, just check `addons/skin.arctic.fuse.3/` is at root.

## Common pitfalls

- **Forgetting `--clobber` on re-upload.** `gh release upload` will fail with "asset already exists" unless you pass `--clobber`. The script in this skill handles it.
- **Missing icons.** If `favourites.xml` references `TorBox_Auth.png` but `media/build_icons/` doesn't contain it, the favourite shows up with a broken thumbnail. Always add the PNGs to `FenLight_Estuary/media/build_icons/` **before** building the zip.
- **Stale `__pycache__`.** Python caches inside the working tree bloat the zip. The script strips them but doesn't strip `.pyo` (older bytecode); strip those too if you find them.
- **Hidden git files.** Don't zip up `Arctic_Fuse_Skin/.git/` or similar. The script filters `.git/` paths but double-check with a `zf.namelist() | grep .git` after build.
