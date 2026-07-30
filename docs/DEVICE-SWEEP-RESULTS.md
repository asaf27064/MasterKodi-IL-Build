# Device sweep results — Xiaomi (MiTV-AFMU0), Kodi 21.3, 2026-07-30

Full supervised run of docs/DEVICE-TEST-PLAN.md, driven over ADB (JSON-RPC +
keyevents + screencap), with the log and Addons33.db pulled and asserted after
every step. Started at wizard 2.4.145 / config 53; ended at 2.4.147 / config 54.

**Headline: the failure that started this — "reinstalled and everything broke",
"chose Zephyr and got Estuary" — is fixed and verified on real hardware.**

## Phases

| # | Flow | Result | Bug found → fix |
|---|------|--------|-----------------|
| 1 | Reinstall POV → **Gears + Estuary** | PASS | — |
| 2 | Reinstall Gears → **POV + Estuary** | PASS | cross-source keep silently no-opped → **2.4.146** |
| 3 | Reinstall POV → **Gears + AF3** | PASS | AF3 home widgets came out Gears-based instead of TMDb → **config 54** + delivery guard |
| 4 | Reinstall Gears → **POV + AF3** | FAIL → re-run PASS | **skin choice lost on POV installs → booted Estuary** → **2.4.147** |
| 5 | Reinstall POV → **Gears + Nimbus** | PASS | — |
| 6 | Reinstall Gears → **POV + Nimbus** | PASS | — |
| 7 | Reinstall POV → **Gears + Zephyr** (manifest-install path) | PASS | — |
| 7b | In-place content switch **Gears→POV→Gears** | PASS | — |
| 8 | Reinstall Gears → **POV + Zephyr** (the Shield combo) | PASS | — |
| 9 | **Same-source** reinstall POV+Zephyr → itself | PASS | — |
| 10 | Skin-switch sweep on POV: Zephyr→AF3→Nimbus→Estuary→Zephyr, **both** השאר and הסר | PASS | — |
| 11 | Auto-update pass | PASS (observed twice) | — |

## The three bugs (all found on-device, none by static review)

1. **Cross-source keep collapsed (2.4.146).** `keep.backup` read the live
   `content_source` setting to decide the source -- but install_build flips that
   setting to the TARGET before the keep step, so source==target and the whole
   source-aware branch no-opped (log: "deferred 7 kept gears cred(s)" on a
   POV-target install). Fixed by passing the previous source explicitly; regression
   test added.
2. **AF3 widgets regressed to Gears (config 54).** `config_policy.json` never
   delivered `skinvariables-shortcut-1101widgets.json` + `-homewidgets.json`, so
   the AF3 bundle's older Gears-based copies survived: device showed 15 gears / 0
   tmdb where the repo config has 10 tmdb / 5 gears. This silently restored the
   configuration the TMDb switch had deliberately replaced (the python-crash fix).
   Fixed + `tools/check_config_delivery.py` added to CI so an undelivered config
   file fails the build.
3. **Skin choice lost on POV installs (2.4.147)** -- THE Shield bug.
   `set_default_skin` only edited an existing guisettings.xml and gave up with
   "guisettings.xml not found" otherwise. The Gears bundle ships that file, the POV
   bundle does not, so on POV + any non-default skin nothing was written, Kodi
   recreated the file with its own defaults, and the box booted **Estuary** with
   the chosen skin installed-but-unused. POV+Estuary masked it entirely. Fixed by
   creating a minimal guisettings.xml (skin + Hebrew fontset); regression test added.

## What was verified in every passing phase

- 0 × `SQLITE_READONLY_DBMOVED` on the addon registry (the bug that bricked the
  Shield/Xiaomi -- see memory reinstall-dbmoved-bug)
- correct skin actually loaded (no silent Estuary fallback), 0 × "Unable to find
  plugin", 0 × "Failed to load skin"
- registry reconciled: ghost rows removed (11-22 per install), 0 remaining at end
- content-source isolation: only the chosen engine on disk; shortcuts/menus point
  at that engine only (e.g. POV+Zephyr compiled home = 86 POV / 0 Gears)
- widgets populating, Hebrew RTL + fonts correct, engines answering over JSON-RPC
  (Gears 12 root items / POV 11)

## Data-safety proof (phase 9)

Sentinels planted before a same-source reinstall, verified after:
- fake credential in a POV login field -> **survived**
- POV `watched.db` row `SURVIVE_ME_9` -> **survived**
- user-installed addons (4) -> restored
Log: `backed up groups: pov_services, pov_content, extras (7 staged, 0 failed)` ->
`restore complete (0 failed)`. Cross-source phases correctly did the opposite
(dropped the other engine's data, parked its favourites).

## Skin-switch branches (phase 10)

- **השאר**: previous skin stays on disk, no removal marker -> switching back later
  was instant (no re-download). Verified with Zephyr.
- **הסר**: marker `pending_skin_removal` written, consumed on the next boot ->
  `removed skin skin.nimbus`, folder gone. Verified with Nimbus.
- The credential sentinel survived all four switches (`preserved user login` in the
  log = the POV cred-preserving merge working on every variant re-apply).

## Known / accepted, not bugs

- **First-boot flicker (POV + Zephyr):** the Zephyr bundle ships a Gears-oriented
  compiled home menu, so the first boot after install logs ~13 "Unable to find
  plugin plugin.video.gears" for about a second until the wizard's rebuild replaces
  it (after that: 86 POV / 0 Gears, second boot completely clean). Cosmetic.
- **In-place switching leaves both engines installed** (backlog A3) -- unchanged,
  owner-accepted.
- `Textures13.db` SQLITE_MISUSE noise (Kodi's thumbnail cache, self-heals).
- Dialog button layout differs per skin (AF3 stacks vertically, Nimbus/Estuary
  horizontally) -- only relevant when scripting input over ADB.

## Open item

- Phase 9's keep list did not offer "מועדפים (Kodi)". Most likely correct (the
  zephyr-pov variant ships no favourites.xml, so there is nothing to keep -- Zephyr
  uses skinshortcuts menus), but re-confirm on the device: if favourites.xml exists
  non-empty and the group is still not offered, that is a real gap in
  `keep._group_has_data`.
