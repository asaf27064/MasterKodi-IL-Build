# Two-fleet architecture — Omega (Kodi 21, users) + Piers (Kodi 22, Asaf-only)

*Established 2026-07-16 when the Kodi 22 port was validated end-to-end (fresh
EXE install → full build → Hebrew → per-skin views) and `kodi-22-port` merged
into main.*

## The model

ONE repo, ONE branch (`main`), TWO delivery fleets. Everything python is shared;
skins and two pinned addons differ per fleet.

| | Omega fleet (users) | Piers fleet (Asaf-only) |
|---|---|---|
| Kodi | 21.3 (python 3.8, gui 5.17) | 22.0 Piers (python 3.14, gui 5.18) |
| Manifest | `manifest.json` | `manifest-piers.json` |
| Addon release | `addons-latest` | `addons-piers` |
| Installer release | `installers` | `installers-piers` (EXE rebranded: own AppId, installs to `C:\MasterKodi IL Piers` alongside the Omega build; APKs same package id = in-place upgrade) |
| Base binaries | `build-inputs` | `build-inputs-piers` |
| Skins built from | `overlays/` | `overlays-piers/` (applied AFTER `overlays/`, so the gui-5.18 variants win) |
| skinshortcuts | 2.0.3 (source tree) | **3.0.1** (official piers-repo zip, `build.json piers.replacements`) |
| CI | `build-and-release`, `build-exe`, `build-apk` | `build-and-release-piers`, `build-exe-piers`, `build-apk-piers` |

The wizard is ONE addon serving both: `KODI_MAJOR >= 22` switches the manifest
URL and gates Piers-only behavior (e.g. the Zephyr menu-bundle relay never runs
on Piers — the v3 skin declares its own menus).

## How a change ships

- **Shared python addon** (gears, gearsai, wizard…): edit under `addons/` or
  `overlays/<id>/files/`, push main → BOTH `build-and-release` and
  `build-and-release-piers` rebuild and refresh their manifests. One change,
  both fleets.
- **Omega skin**: `overlays/<skin>/` → only the Omega pipeline reships it.
- **Piers skin**: `overlays-piers/<skin>/` → only the Piers pipeline.
- **Config**: shared `config/`; `config_policy.json` entries support
  `kodi_min`/`kodi_max` gates for per-fleet files (none needed yet).
- **Per-fleet addon version pin**: `build.json` → `piers.replacements`
  (id → version+url of an official zip CI downloads instead of zipping source).

## Per-fleet skin bases (overlays-piers base.json `base_type`)

- Zephyr: upstream zip (DenDyGH ships a Piers zip in each release)
- AF3: upstream zip (self-port — gui bump; drop when jurialmunkey ships piers)
- Nimbus: `local_committed` (base = our committed `addons/skin.nimbus`)
- Estuary: `kodi_bundled` (base = committed zip of Kodi 22's own Estuary;
  refresh when adopting a new Kodi 22 version)

`upstream-watch` checks BOTH overlay dirs (separate tracking issues);
`local_committed`/`kodi_bundled` are skipped (no watchable upstream). Piers
updates are always adopted manually (re-verify on 22 first).

## Known interim facts

- Kodi 22 is Beta1: users stay on Omega until 22 final + soak. The Piers
  installers are NOT linked on the download page.
- `resource.language.he_il` 11.0.79 is currently byte-identical in the omega
  and piers repos → no replacement needed; revisit when new K22 strings get
  translated (bump via the piers repo's newer zip in `piers.replacements`).
- The `kodi-22-port` branch is retired after the merge; kept only so pre-merge
  Piers boxes (wizard ≤2.4.79 reading the branch manifest URL) can update to
  a main-reading wizard. Its manifest-piers.json was synced one final time.
- Fresh-install ordering trap (fixed in wizard 2.4.79, keep in mind for new
  seeding code): at config-apply time gears has NEVER run on a fresh box —
  its settings.db doesn't exist. Anything writing gears settings must also
  run post-`_prewarm_gears` (which creates the db).
