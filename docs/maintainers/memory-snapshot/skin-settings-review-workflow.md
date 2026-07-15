---
name: skin-settings-review-workflow
description: "Asaf's skin-by-skin settings review process + the local-vs-push rule; ALWAYS ask before pushing"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 3dfd12c0-e979-4f10-a359-3f2151554677
---

Asaf's working agreement (2026-07-14) for curating each skin's default settings +
arrangement. He reinstalls Kodi fresh on his PC and goes skin by skin; for each
skin he tells me what setting is missing / should change, and that becomes the
shipped DEFAULT for everyone. Every skin has its own settings + arrangement.

**ALWAYS ASK / confirm before pushing.** He wants to be asked. Don't push or bump
config on my own — surface the change, confirm, then ship. Aligns with
[[local-first-workflow]].

**local vs push:** edits to his live install (`C:\MasterKodi IL\portable_data`) are
LOCAL, only for him, until he says push. When he says push, it goes to EVERYONE via
the config system.

**ALWAYS VERIFY THE CHANGE IS BANKED IN THE REPO — "בלי פאדיחות" (2026-07-14).** After
capturing any live setting change, immediately confirm it landed in the REPO
(`config/userdata/...` or the skin's `addons/<skin>/xml/...`), not just live on the
box. Grep the repo file for the new value every time. A change that only lives on the
box is LOST at ship time. This bit us on Estuary: subtitle font was set to Rubik on the
box but the repo config still said "Noto Sans Hebrew" — caught only on a re-check.

**How a default reaches "everyone" (see [[zephyr-home-performance]] for modes):**
per-skin defaults live in `config/userdata/addon_data/<skin_id>/settings.xml`,
delivered by config_policy (`update: merge_seed`). 
- NEW setting (users don't have it) -> merge_seed adds it. Just add to settings.xml
  + bump `config_version` (both build.json and config_policy.json).
- CHANGING an existing default users already have -> merge_seed will NOT overwrite;
  add that setting id to the file's `force_ids` in config_policy.json to push it to
  everyone, then bump config_version. Remove the id afterward if user changes to it
  should stick again.
Skins covered: skin.estuary, skin.nimbus, skin.arctic.fuse.3,
skin.arctic.zephyr.2.resurrection.mod.
