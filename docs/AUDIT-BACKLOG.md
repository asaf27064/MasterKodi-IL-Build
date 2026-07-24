# Audit backlog & deferred items

**Purpose:** a durable resume point after a long series of external security/robustness
audits. Everything that was *safe to fix without a device* has been fixed and shipped.
What remains is listed here so we can pick it up deliberately later.

- **Current release:** wizard **2.4.139** (commit `1359acd`), both fleets (Omega/Kodi 21
  + Piers/Kodi 22) green.
- **State:** clean installs, updates, backups, and Gears/POV isolation are confirmed
  working. Nine external audits found **no outstanding critical credential exposure**.
- **Test suite:** `python tools/tests/run_tests.py` (58 checks, real wizard modules via a
  Kodi shim). Credential scan: `python tools/check_no_credentials.py`. Variants:
  `python tools/verify_variants.py` + `python tools/gen_variant_index.py --check`.

---

## A. Deferred — real, but need a REAL Kodi device to fix safely

These live in the update/switch/install engine. A blind change risks trading a rare
failure for a visible bug (frozen home, stale menus, half-installed build), so each needs
"fix + verify live" together, not static edits.

### A1. `ReloadSkin` crash paths — **recommended to do first**
- **Progress:** 1 of 5 paths closed. The deferred-restart approach is now **proven on
  device** (the reapply path below) — the remaining 4 convert the same way.
- **What:** `ReloadSkin()` tears down + rebuilds the whole skin; if a Python widget is
  loading at that instant, Kodi hits a native `python3.8.dll` access violation (the crash
  we chased). The main content-switch path already uses `RestartApp`; these still reload
  in place:
  - active-skin update — `resources/libs/modular_update.py` (skin-update path)
  - automatic menu repair — `resources/libs/modular_update.py` (`repair_skin_menu`)
  - ~~reapply-current-source — `resources/libs/content_source.py`~~ ✅ **CLOSED in
    2.4.142** — crash reproduced live on this path, converted to the deferred-restart
    pattern `switch_to` uses, and **confirmed fixed on device** (2026-07-24).
  - startup service — `service.py`
  - Nimbus power menu — `config-variants/nimbus-pov/skin-overrides/DialogButtonMenu.xml`
- **Fix approach:** convert to the deferred pattern already used elsewhere (arm
  `pending_view_rebuild` + restart, or defer the reload to next boot) instead of an
  in-place `ReloadSkin`. (Exactly what 2.4.142 did for the reapply path.)
- **Caveat (important):** per our own investigation the crash is **addon-agnostic** and the
  real driver was widget count (tmdb-service widgets), so removing reloads *narrows* the
  window but may not eliminate the crash. Treat `ReloadSkin` as a trigger, not the root.
- **Downstream damage now contained:** a crash on ANY of these paths mid-`run_update` left
  the op-lock stranded (dead owner), which silently blocked boot auto-updates for up to 30
  min. Fixed in **2.4.143** (`acquire_op_lock` reclaims a dead-owner lock at once — see
  [[oplock-dead-pid-fix]]). So a remaining ReloadSkin crash no longer strands updates, but
  it still crashes Kodi — hence still worth converting.
- **Verify live:** trigger each path on the box, watch `kodi.log` for the crash window and
  confirm the home screen still refreshes (no frozen/stale menu).

### A2. Update + config transaction atomicity
- **What:** in `modular_update._run_update_impl`, a successful addon's state is committed
  immediately, and after an addon update *fails* the config + POV-variant apply still run
  (only *removals* are gated on `not failed`). Result: a parent can update while its dep
  fails, then config advances over a mixed set.
- **Fix approach:** gate `_maybe_apply_config` / `_maybe_apply_content_variants` on
  `not failed` (mirror the removals gate); consider staging addon-state commits.
- **Verify live:** force one addon to fail mid-update; confirm config/variant versions do
  NOT advance and the box stays coherent.

### A3. In-place engine isolation (Gears ↔ POV switch)
- **What:** switching in place installs the new engine but does **not** remove/disable the
  old one, so both coexist (stored `content_source` says one). Clean fresh installs ARE
  isolated; this only affects the in-place switch feature.
- **Fix approach:** on switch, disable (or uninstall) the inactive engine's closure
  (`plugin.video.gears`/`plugin.video.pov` + scrapers) — **destructive**, so test hard.
- **Verify live:** Gears→POV→Gears cycle; confirm only the active engine is enabled.

### A4. Wipe / extraction rollback (mixed old+new install)
- **What:** `builds.wipe()` now counts undeletable files + warns, but the install still
  continues; a Windows-locked file (DLL/py/db) survives and the new build extracts over it
  → hybrid build. Base `extract_zip` also tolerates a few non-critical errors.
- **Fix approach:** on undeletable files, offer abort; or stage-then-swap the build so a
  partial extract can roll back.
- **Verify live:** lock a file during a reinstall on Windows; confirm no silent hybrid.

### A5. Config-write failure gating
- **What:** `modular_update` swallows per-file/per-dir write exceptions during config
  delivery, then still records the new `__config__` version → an incomplete config is never
  retried until the next version bump.
- **Fix approach:** track write failures; do not advance the recorded version when any
  required write failed.
- **Verify live:** deny one config write (read-only/full disk); confirm it retries next pass.

### A6. Manual backup / restore transactionality
- **What:** Full backup suppresses individual file errors (can return success with missing
  files) and raw-copies live SQLite + WAL separately (possible inconsistency). Standalone
  restore writes directly over live userdata with no rollback; open DB connections can
  overwrite restored state. (Quick backup snapshot-failure IS already fixed.)
- **Fix approach:** count failures for Full backup; snapshot DBs; restore to a staging area
  then swap; stop addons/close DBs first (device-dependent).
- **Verify live:** backup + restore on the box; confirm creds/dbs round-trip.

### A7. Source-switch rollback edge cases
- **What:** `_backup_once` suppresses backup failures; rollback only restores files that
  have a `.pre_gears` backup; the keep-restore `rmtree`+`copytree` for an executable addon
  dir can leave a partially-deleted addon if a file is locked (STAGE is kept, so
  recoverable). Main prefetch/abort cases ARE fixed.
- **Fix approach:** sibling-staging + atomic rename/rollback instead of delete-then-copy.
- **Verify live:** interrupt a switch on the box; confirm the prior source is intact.

### A8. Per-user Ktuvit login wiring
- **What:** settings `hebrew_subtitles.ktuvit_email` / `ktuvit_password` exist in the POV
  UI (`settings.xml`) but the login code never reads them — it always uses the shared
  account. So the "personal fallback" doesn't function.
- **Blocker:** the shipped password is a **pre-encoded** value; Ktuvit may reject a raw
  user-typed password without the same encryption (see the `kt_enc_pass` mechanism).
  Wiring it blind could break opt-in users' logins.
- **Fix approach:** determine Ktuvit's expected password format, encode the user value the
  same way, use it only when both settings are non-empty (else fall back to the shared
  account). Files: `plugin.video.{pov,gears}/…/kodirdil/websites/ktuvit.py`,
  `service.subtitles.gearsai/resources/sources/ktuvit.py` (+ the two overlays).
- **Verify live:** set personal creds; confirm Ktuvit login succeeds and clears cleanly.

---

## B. Owner-accepted — intentional, NOT to be "fixed"

Recorded so a future audit doesn't re-flag them:

- **Shared Ktuvit account** (`darksubsil1@…`) — ships so Hebrew subs work out-of-box, like
  the `mk-` pool key. Intentional/public. Baselined in the credential scanner.
- **`mk-…` pool token** — public by design (every client needs it for subtitle up/download).
- **TorBox history token** — rotated by owner (2026-07-23); git-history rewrite declined.
- **Supply chain** — mutable public build catalog, no bundle signing, unpinned GitHub
  Actions / pip / choco / build-input assets, unsigned Windows installer, and **auto-adopt
  ON** with repo write access — all accepted trade-offs.
- **Cloudflare worker** — public upload key + non-atomic/fail-open rate limit accepted;
  private log *reads* ARE authenticated (confirmed).
- **Low priority** — stale installer `AppVersion`, APK signing-pw in exception text
  (GitHub masks secrets), pyflakes unused-locals (benign).

---

## C. Credential scanner — scope (best-effort net, not a boundary)

`tools/check_no_credentials.py` catches an **accidental** committed credential:
- XML: `<setting>` text content **and** `default="…"` attributes.
- Python: plain, annotated (`x: T = "…"`), and dict-literal (`"x": "…"`) assignments whose
  id looks credential-ish and value is secret-shaped (≥12 chars or base64/email/UUID).
- Baseline model: everything currently shipped is intentional → blessed in
  `tools/known_public_keys.txt` (id + value-hash only, **no** value prefixes); a NEW value
  not in the baseline fails CI.

**Out of scope by design** (would need an AST/token scanner): bytes literals, values under
12 chars, obfuscation/concatenation, and Python *inside* built bundle zips (the repo scan
already covers the shipped `.py` that go into those bundles). Do **not** describe it as a
determined-attacker boundary.

---

## How to resume

1. `git pull` → confirm `main` is at/after `1359acd`.
2. Run the suite: `python tools/tests/run_tests.py` (expect 58 pass).
3. Pick **one** item from section A (A1 `ReloadSkin` is the recommended first).
4. Fix it, add a test where possible, and **verify on a real Kodi box** (both fleets:
   Kodi 21 + 22) before shipping — that live verification is exactly what section A is
   waiting on.
