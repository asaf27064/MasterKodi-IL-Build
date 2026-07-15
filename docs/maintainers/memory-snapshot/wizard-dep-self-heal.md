---
name: wizard-dep-self-heal
description: Wizard zip-install bypasses Kodi dep resolution → modular_update.py self-heals missing manifest deps
metadata: 
  node_type: memory
  type: project
  originSessionId: 3dfd12c0-e979-4f10-a359-3f2151554677
---

The wizard installs addons by **extracting zips** (`modular_update._apply_one`),
which BYPASSES Kodi's own dependency resolution. So any addon we install can end
up missing its dependencies — e.g. TMDbHelper without `script.module.jurialmunkey`
→ "No module named 'jurialmunkey'" crash on every startup.

**Fix (wizard 2.4.34, 2026-07-14):** `modular_update.py` has a GENERALIZED dep
self-heal — `_missing_deps_of_installed(manifest)` parses every installed addon's
real `<import>` deps (comments stripped) and reinstalls any that ARE in our
manifest but missing. Replaced the old hardcoded `DEP_INTEGRITY` map. `compute_updates`
adds these to `needed`, and the optional-skip guard is `aid not in needed` so an
optional dep of an installed parent still gets repaired.

**Deliberately NOT healed (correctly ignored):**
- `script.module.pil` — binary/per-platform, we don't ship it; addons (gears,
  TMDbHelper, qrcode, nimbus.helper) degrade gracefully. Live box runs fine WITHOUT
  it — proven. This was task #9's decision; it holds. Not in manifest → self-heal skips it.
- `xbmc.gui` / `xbmc.metadata` / `kodi.resource` — Kodi core built-ins, never addon-installed.
- Commented-out `<import>` lines (e.g. `script.subskeys` in all_subs_plus).

**Second failure mode — DISABLED, not missing (wizard 2.4.35, 2026-07-14):** the
persistent Android TMDbHelper "No module named 'jurialmunkey'" was NOT missing
files — jurialmunkey was on disk + FindAddons listed it installed, but it was
DISABLED. A disabled `xbmc.python.module` is not put on the Python path, so import
fails. Kodi's orphan-dependency cleanup disabled it when Zephyr was removed
(our zip-installed parents never registered in Kodi's dep graph, so it looked
unused). Re-extracting does NOT fix it — the disabled flag lives in Addons33.db,
not on disk. `modular_update.repair_disabled_deps()` scans installed addons'
`<import>` deps and re-enables any installed-but-disabled one via JSON-RPC
`Addons.SetAddonEnabled` (restores files first too). Runs every check; reported
as `enabled` in the summary. So the two self-heals are complementary: 2.4.34 =
files absent, 2.4.35 = files present but disabled. Diagnosis tell: log shows
`FindAddons: <dep> installed` AND `ModuleNotFoundError` for the same module.

**Third self-heal — empty Zephyr home menu (wizard 2.4.36, 2026-07-14):**
switching to Arctic Zephyr gave category labels but empty content + dead nav
(log: `Skin has invalid include: skinshortcuts-mainmenu` + `Control 301 ... asked
to focus, but it can't`). Zephyr uses classic `script.skinshortcuts`, which must
generate `<res>/script-skinshortcuts-includes.xml` from menu DATA. On a fresh box
buildxml DOES run but with an unseeded/empty userdata cache it writes an EMPTY
includes file (log: `invalid include: skinshortcuts-mainmenu`, `Control 301 ...
asked to focus, but it can't`), then keeps regenerating empty from that poisoned
cache. AF3 is immune — it drives its menu from `script.skinvariables` JSON nodes,
no build step. NOTE the 2.4.36 attempt (re-run the skin's own buildxml) FAILED for
exactly this reason — buildxml isn't the problem, the empty userdata source is.
FINAL FIX (wizard 2.4.37): ship a known-good menu captured from a working install
at `wizard/resources/menu_defaults/<skin>/` (built includes + skinshortcuts
userdata DATA/properties/hash). `modular_update.repair_skin_menu()` detects broken
(includes missing OR not containing `skinshortcuts-mainmenu`) and lays the bundle
down: userdata (menu SOURCE, so later rebuilds reproduce it) + includes into the
skin, then `ReloadSkin()`. Idempotent. Runs every update check → self-heals with NO
navigation (2 restarts: one to self-update the wizard, one to run the repair). To
refresh the shipped menu after redesigning it, re-capture from a working install
into that bundle dir.

**Audit result (2026-07-14):** across ALL skins/addons the ONLY real gap was
`resource.font.robotocjksc` (in manifest + shipped, but missing on the box because
AF3 was zip-installed) — the new self-heal repairs it automatically. Related:
[[masterkodi-build-repo]], [[close-kodi-before-editing]].
