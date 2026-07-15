---
name: af3-menu-generator-rebuild
description: AF3/Nimbus home categories come from COMPILED skinvariables includes; editing node JSON needs a forced buildtemplate or it stays empty
metadata:
  type: project
  originSessionId: 3dfd12c0-e979-4f10-a359-3f2151554677
---

**AF3 (and Nimbus) home menu is generated, not read live.** The homeswitcher
categories render from COMPILED skin includes in
`skin.arctic.fuse.3/1080i/script-skinvariables-generator-includes-.xml` (e.g.
`skinvariables-1103widgets-combined-info`). Those includes are produced by the
script.skinvariables GENERATOR (`ShortcutsTemplate`, `action=buildtemplate`, config
`shortcuts/skinvariables-generator.json`) FROM the node JSONs under
`userdata/addon_data/script.skinvariables/nodes/skin.arctic.fuse.3/skinvariables-shortcut-*.json`.

**Trap (cost us 2 empty-category misses, 2026-07-14):** editing a
`skinvariables-shortcut-NNNNwidgets.json` (or -submenu) does NOT change the menu.
The generator hash-skips (`update_xml` `is_updated()` compares Skin.String hash) and
only reruns on its own triggers, so the compiled include stays empty/stale. Also:
AF3 home CATEGORIES render `*widgets` (folder-style), NOT `*submenu` action buttons —
submenu items for a category don't show.

**Fix (general, in wizard service.py `_process_pending_view_rebuild`):** on the
post-install marker, run BOTH `RunScript(script.skinvariables,action=buildtemplate,force=true)`
(recompiles menu/shortcut includes) AND `...action=buildviews` (view types). Belt-and-
suspenders: blanking the `script-skinvariables-generator-hash` skin setting forces regen.
Any config-driven AF3/Nimbus menu change must trigger buildtemplate, else it never lands.

**Action tiles that aren't gears modes:** AF3 category widgets open a folder and render
its items as tiles (like the TorBox gears shortcut-folder). Gears folder items can ONLY
run gears modes, so for maintenance actions (GearsAI RunScript, wizard send_logs/
check_updates) we added a WIZARD plugin folder `?mode=maintenance_folder` (default.py,
uses xbmcplugin) listing the 4 actions; the 1103 widget points at it. See
[[skin-settings-review-workflow]], [[estuary-review-changes]].
