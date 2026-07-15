---
name: skin-zip-dep-pinning
description: Pin ONLY MODDED_ADDONS; vanilla deps auto-update by design (Asaf corrected my over-pinning of AF3's jurialmunkey deps)
metadata:
  type: feedback
  originSessionId: 3dfd12c0-e979-4f10-a359-3f2151554677
---

**The pin policy is SELECTIVE and already decided** (modular_update.py `MODDED_ADDONS`,
wizard 2.4.23): pin ONLY Hebrew-modified addons (gears, the 4 skins, skinhelper,
gearsscrapers, gearsai, skipintro, firstrun) with `origin=''` + `update_rules
updateRule=1`. **Vanilla deps (script.skinvariables, script.texturemaker,
script.module.jurialmunkey, resource.font.robotocjksc, tmdbhelper, skinshortcuts,
resource.images.*) are DELIBERATELY left to auto-update from their upstream repos**,
and `tools/refresh_vanilla_deps.py` re-vendors their new packages into `addons/`
periodically. Precedent: skinvariables auto-updated 2.1.35->2.2.2 harmlessly.

**Why:** Kodi offering updates for AF3's deps is EXPECTED, not a bug. When Asaf asks
"is this dep update critical?" the answer is: no — vanilla deps updating is the design.

**My mistake (2026-07-14, Asaf caught it):** I pinned AF3's 4 vanilla jurialmunkey deps
— contradicting the policy. Reverted on the live DB (origin back to
repository.jurialmunkey, rules deleted); `skin.arctic.fuse.3` itself stays pinned
(it IS modded). The wizard's `builds._pin_addons_in_db(aids)` (runs after every zip
skin-install in `install_skin`/`install_skin_only`) now FILTERS to MODDED_ADDONS.

**Shipping a dep update deliberately (if ever needed):** new version -> `addons/<id>/`
(refresh_vanilla_deps or manual) -> test -> push -> CI manifest ships it. The manifest
channel bypasses Kodi pinning entirely (we write files directly). See
[[wizard-dep-self-heal]], [[af3-menu-generator-rebuild]].
