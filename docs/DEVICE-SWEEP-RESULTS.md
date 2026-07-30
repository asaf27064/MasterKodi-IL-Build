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

## Follow-up sweep: search / backup / maintenance (same box, 2.4.147 -> 2.4.148)

| Flow | Result | Bug found -> fix |
|------|--------|------------------|
| חיפוש (search) | PASS | — |
| גיבוי מהיר (quick backup) | PASS | — |
| תחזוקה (maintenance) | FAIL -> re-run PASS | 3 defects -> **2.4.148** |

- **Search**: RPC probes movies 21 / tv 21 / **collections 13** (the flow the `conditiom`
  typo had broken) / people 20; driven through the skin UI, "batman" -> "תוצאות עבור
  BATMAN" with 21 movies + 20 series, Hebrew posters.
- **Quick backup**: `MasterKodiIL_quick_20260730_165648.zip`, 9 items / 16 KB.
  Contents verified to include `addon_data/plugin.video.pov/settings.xml` **with the
  planted credential sentinel**, gearsai, all three skins' settings, guisettings /
  sources / favourites -- the 2.4.137 quick-backup fix confirmed live.

### The three maintenance defects (2.4.148)

1. **Cache clear deleted kodi.log.** `clear_cache` wiped all of `special://temp`,
   including `kodi.log` / `kodi.old.log`. Kodi holds that file OPEN, so unlinking it
   on Android leaves a dangling handle: every later log line goes nowhere and the
   log is unrecoverable until Kodi restarts. Measured on device -- after one cache
   clear the temp dir was completely empty and the log was gone. This silently
   destroys the only support channel we have (see memory log-reading-workflow).
   Fixed: `purge_dir(..., keep_suffixes=('.log',))` protects log files in the temp
   root; everything else still goes. Verified: `cache cleared: 3 items, 0.0 B
   (logs kept)` with kodi.log intact.
2. **Double dialog, English text, `נמחקו: None`.** The lib workers ran their own
   confirm + result dialogs in **English** and returned `None`, while `default.py`
   wrapped them in its own Hebrew confirm and printed the return value. Actual UX
   was: Hebrew confirm -> English confirm -> English result -> Hebrew result reading
   "נמחקו: None". `clear_all` was worse -- three nested English prompts on top of a
   progress bar. Fixed: workers are now silent and return a real summary
   (`17 פריטים (13.4 MB)`); the caller owns every dialog, all Hebrew.
3. **Thumbnail clear dropped the OPEN Textures DB.** `clear_thumbnails` deleted
   `Textures*.db` unconditionally, then merely *offered* a restart -- decline it and
   the texture cache is broken for the rest of the session. Same class as the bug
   that bricked the Shield. Fixed: `drop_texture_db` is only True when the caller is
   actually restarting; the menu now asks "סגור בסיום" vs "ניקוי חלקי" (default:
   partial). Verified on device: partial clear freed 28.7 MB, kept Textures13.db,
   **0 DBMOVED**, and thumbnails regenerated (0 -> 2.0 MB) with the DB still growing.

Also added: live sizes on every maintenance row (Cache 61.2 KB / Packages 13.4 MB /
Thumbnails 28.7 MB / total), with descriptions kept Hebrew-leading + one Latin run so
bidi doesn't drop the size into the middle of the sentence. Regression test
`test_maintenance_keeps_logs` added (suite now 92 checks, 0 failures).

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

## Open item — RESOLVED (re-checked on device)

Phase 9's keep list did not offer "מועדפים (Kodi)" -- **correct behaviour, not a
bug.** Only `estuary-pov` ships a favourites.xml (verified across all 9 variant
index.json files); `zephyr-pov` does not, because Zephyr's home comes from
skinshortcuts. So on the Zephyr+POV box at phase 9 there was genuinely no
favourites.xml to keep. The file that exists now (23 POV entries) was written at
16:24 -- during phase 10, when the sweep switched through **Estuary** -- and
persisted after switching back. `keep._group_has_data` behaved correctly throughout.

## Final box state (verified after the sweep)

skin `skin.arctic.zephyr.2.resurrection.mod` · content_source `pov` · wizard
2.4.147 · only POV on disk (isolation holds) · POV engine answering (11 root items,
21 popular movies) · favourites 23/23 POV · phase-9 credential sentinel still
present · **0** boot errors, **0** DBMOVED, **0** failed-skin, **0** missing-plugin.

## Two false alarms worth recording (so they are not re-chased)

1. **"22 ghost registry rows"** -- they are Kodi's OWN system addons
   (`audioencoder.kodi.builtin.*`, `game.controller.*`, `repository.xbmc.org`,
   `resource.language.en_gb`, `script.module.pil`, `webinterface.default`, ...),
   which live in `special://xbmc/addons`, not in home/addons. The measurement
   script only listed home addons. `_reconcile_addons_db` checks BOTH dirs, so it
   never deletes them. Any future ghost audit must include the system addon dir.
2. **"POV engine returns 0 items"** -- an artifact of probing while the box was
   dozing/just-woken (JSON-RPC times out or answers empty). After a wake +
   `am start` + RPC ping, the same probe returned 11 root items and 21 movies.
   Always confirm `mWakefulness=Awake` + a successful ping before trusting a probe
   (see memory adb-device-driving).
