---
name: fresh-install-completion
description: EXE/APK bundle a STALE bootstrap; the current build arrives via manifest+config — install now applies it before exit
metadata: 
  node_type: memory
  type: project
  originSessionId: 3dfd12c0-e979-4f10-a359-3f2151554677
---

The EXE/APK installers bundle a THIN BOOTSTRAP `portable_data_template` (from the
`build-inputs` GH release) — base Kodi + wizard + an OLD baked snapshot of our
config. The CURRENT build (latest modified skins + config) is meant to arrive via
the **manifest + config-apply**, which OVERWRITES the bootstrap. So a fresh install
is NOT vanilla — it's our OLD snapshot until the manifest/config apply.

**Bug (fixed wizard 2.4.47, 2026-07-14):** after a build install the wizard skipped
the completing update on the first re-entry (`skip_update_check`), so the box sat on
the stale bootstrap (old giant-icon search button present, new power-menu skin-switch
missing — a stale mix, NOT vanilla). Asaf caught that it was our old config, not
stock.

**Fix:** `builds.install_build` now runs `mu.run_update(silent=True, no_reload=True)`
as its LAST step, before Kodi exits — fresh config mode replaces the old bootstrap
config and the manifest installs the current modified skins. So re-entry shows the
LATEST build, no extra restart. `no_reload` flag added to `run_update` /
`repair_skin_menu` so the completion doesn't ReloadSkin out from under the install UI
(a full app restart follows). For an install made the OLD way, one manual restart
runs the same apply.

**Base build REFRESHED (2026-07-14).** The slow 33-item install was the stale base
build zip `FenLight_Estuary.zip` (Jul-10) that `install_build` downloads from
`asaf27064.github.io` release `v1.0` (per build.txt `url=`), plus an empty wizard
state that re-pulled even same-version addons. Rebuilt it REPO-BASED, Estuary-only,
and re-uploaded to v1.0 (`gh release upload v1.0 --repo asaf27064/asaf27064.github.io
FenLight_Estuary.zip --clobber`). Method (Asaf insisted: repo is the only source, no
cruft, no cache, boot-test first):
- addons/ = 33 CORE manifest addons (dropped 3 music scrapers + `repository.burekasKodi`
  — not in manifest).
- userdata config from repo `config/` (Estuary + core only; device UUID reset).
- `Addons33.db` CLEANED: purge catalog tables (Kodi rebuilds), drop cruft/orphan
  `installed` rows, neutralize machine-UUID origins (`LIKE '%-%-%-%-%'` -> ''), keep
  `update_rules` pins (gears/gearsscrapers/gearsai/skipintro/chainsrepo/skin.estuary
  = `origin=''`).
- `ViewModes6.db` CLEANED to 55 curated Estuary views (movies/TV->poster,
  seasons/episodes->list); dropped AF3's 53 + browsing rows; committed to
  `config/userdata/Database/ViewModes6.db` + config_policy `seed_if_absent` (config 19).
- ALL other DBs dropped (Epg/TV/Music/Video/Textures = cache/Kodi-defaults).
- `applied_manifest.json` seeded = manifest core shas (0 addon downloads), NO
  `__config__` (so config-apply still runs once to enforce Hebrew/Gears settings.db).
Boot-tested by swapping it in as portable_data (backup `portable_data_REVIEW`, launch,
0 errors / Estuary+Hebrew / Gears up, then restore). TODO: same for
`Arctic_Fuse_Skin.zip` (incl AF3's 53 views) + `nimbus.zip`. If something stays old
even after completion, that fix was a LOCAL edit never committed to config/manifest —
the review ([[skin-settings-review-workflow]]) catches those. Pushing a WIZARD change
auto-rebuilds EXE/APK; a config-only change does NOT.
