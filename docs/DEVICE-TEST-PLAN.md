# Supervised device test plan (Xiaomi, ADB-driven)

**Goal:** verify every install / skin / content-source combination on a REAL box,
with Claude driving via ADB (JSON-RPC + `input keyevent` + `screencap`) and pulling
the log + Addons DB after every step. Asaf watches the TV and confirms the few
things automation can't judge. Built after the 2026-07-30 DBMOVED reinstall bug —
first run doubles as the live verification of wizard 2.4.144/145.

**Control channel:** `adb connect <ip>:5555` → `adb forward tcp:19090 tcp:9090`
(JSON-RPC), `adb shell input keyevent` (DPAD/OK) to drive dialogs, `screencap` for
visual proof, log pulls from `.kodi/temp/kodi.log`. Recovery playbook ready
(memory: reinstall-dbmoved-bug) if any step bricks the box.

---

## Phase order — the FULL matrix on device

Install-time skin selection is a DIFFERENT code path per skin (Estuary baked-in,
AF3/Nimbus bundled-zip, Zephyr manifest-install; on POV each also triggers its own
variant apply), so every skin is chosen AT INSTALL TIME on BOTH sources. The
sequence alternates sources, so every reinstall is also a cross-source keep test
(both directions, 8 times). Starting state: POV+Zephyr (post-recovery).

| # | Reinstall to | Uniquely covers |
|---|---|-----|
| 1 | Gears + Estuary | cross POV->Gears; default-skin path |
| 2 | POV + Estuary | cross Gears->POV; estuary-pov variant |
| 3 | Gears + AF3 | install-time non-default skin (bundled-zip) |
| 4 | POV + AF3 | af3-pov variant applied at install |
| 5 | Gears + Nimbus | Nimbus bundled-zip + cpath seed |
| 6 | POV + Nimbus | nimbus-pov variant (incl. the search fix) |
| 7 | Gears + Zephyr | Zephyr MANIFEST-install path (breakage suspect) |
| 7b | in-place switch ->POV ->back to Gears | switch_to both ways from a Gears state; then verify a clean-POV box REFUSES in-place Gears (by design) |
| 8 | POV + Zephyr | THE combo that broke Shield/Xiaomi; ends at today's state |
| 9 | POV + Zephyr (same-source) | keep roundtrip: watched/favourites/creds survive |
| 10 | Skin sweep on POV: Zephyr->AF3->Nimbus->Estuary->Zephyr | every skin as a SWITCH target + per-skin traps |
| 11 | Auto-update pass (next bump) | op-lock fix, silent update, config apply |

Estimated 2.5-3.5h supervised; the sequence is RESUMABLE (state recorded after
every phase — can split across sittings). Cross-source phases log debrid out by
design (re-login once when asked). The full checklist below runs after EVERY state.

## Per-state checklist (run after EVERY phase; ✎ = Asaf confirms on TV, rest is
checked from logs/DB/screencap automatically)

**Boot & registry health**
- Correct skin loaded (`load skin from:` in log) — no Estuary fallback
- 0 × `Unable to find plugin`, 0 × `SQLITE_READONLY_DBMOVED` / `SQL error`
- Addons DB rows == addons on disk (no ghosts), all build addons ENABLED
- No stale markers (op-lock, pending_view_rebuild consumed), no crash in log

**Home & menus**
- Home categories match the content source (POV vs Gears menus), correct order ✎
- Hebrew labels render RTL, correct fontset (no tofu) ✎
- Power menu: source-correct items; שלח לוגים works; "פתח את האשף" present; fast exit relaunches
- Movies/TV shortcuts open the ENGINE's lists (not files/playlists) ✎

**Widgets**
- Every home row renders content: engine-based widgets (POV/Gears), TMDb-helper
  widgets, networks row (shortcut-folder seed), continue-watching ✎
- No "Add content…" placeholders on default rows

**Views & arrangement**
- Per-skin views applied (the views config — a known Xiaomi weak spot)
- Section viewtypes correct after skin switch (viewtypes re-seed) ✎

**Search**
- Skin search → correct provider for the source; movies, TV, collections
  (the `conditiom` fix), person search

**Playback & subtitles**
- Scrape a known movie: sources appear (magneto per scraper setting)
- Start playback ~30s; GearsAI subtitle flow (Kodi window opens, list loads) ✎
- Stop cleanly; tried-source badge updates (POV)

**Services (חיבור שירותים)**
- Same-source flows: debrid + Trakt still logged in, Gemini key preserved
- Cross-source flows: debrid EXPECTED logged-out (by design — re-login once);
  Gemini/gearsai preserved; subtitle pool reachable
- TMDb metadata loads (posters/Hebrew synopses)

**Wizard itself**
- Main menu opens; סטטוס shows right build+versions; בדוק עדכונים = up-to-date
- מקור תוכן menu shows correct active source (branded WizardMenu)
- תחזוקה: clear cache/packages runs without error
- גיבוי מהיר: creates + appears in the list (captures settings.db + POV creds)

**Data integrity**
- Same-source: watched/continue-watching + favourites survived
- Cross-source: old favourites PARKED as favourites.pre_<src>.xml; new source's
  favourites intact; downloads folders intact; user extras present + enabled

**Skin-specific traps**
- Zephyr: skinshortcuts home rebuild lands (no frozen home / stub includes)
- AF3: compiled menu categories non-empty (menu generator ran)
- Nimbus: cpath seed present (custom menus), 6-digit label issue N/A on K21
- Estuary: mod glyphs/arrows correct

**After the whole sweep**
- kodi.old.log + kodi.log clean of native crashes (python3.8.dll)
- Auto-update still armed (no leftover skip_update_check)

## Division of labor
- **Claude:** drives every flow, pulls log+DB+screencap after each step, asserts
  the automatic checks, stops the sweep on first red flag.
- **Asaf:** watches the TV, answers the ✎ checkpoints (a yes/no each), presses
  nothing unless asked (remote input can race the scripted input).

## CI follow-up (separate task)
Extend the shim harness (test_dbmoved_install pattern) into a bundle × skin ×
source install-simulation matrix run in CI on release, asserting the same
registry/config/shortcut invariants — so structural breakage never reaches a
device again.
