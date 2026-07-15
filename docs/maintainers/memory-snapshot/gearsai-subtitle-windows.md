---
name: gearsai-subtitle-windows
description: Canonical names for the two GearsAI subtitle windows (stop confusing them)
metadata: 
  node_type: memory
  type: reference
  originSessionId: 3dfd12c0-e979-4f10-a359-3f2151554677
---

Two distinct subtitle picker windows in the MasterKodi build. Always use these names:

**"Kodi window"** (חלון קודי) — Kodi's NATIVE subtitle download dialog. The plain
list with the service column on the right (ביטול / ידני / ALL SUBS PLUS /
LOCALSUBTITLE), country flags, CC/SYNC badges. Code: `resources/main.py` (builds
items via `man_search_return`, returns them to Kodi). The `● סנכרן` row lives here
as a `gearsai_sync=1` download item.

**"Wand window"** (חלון השרביט) — the custom styled MasterKodi picker
`SubsWindow.xml` / `resources/modules/sub_window.py` (`SubsXMLWindow`). Opened by
the wand/שרביט button. Colored match%, `[תורגם·מקור]`/`[SDH]` badges, 2-line
release names, sorted by match. The pinned green `סנכרון` row lives here
(`_run_sync`).

Both windows show the manual sync row; both are gated on the Home-window property
`gearsai.current_heb_sub`. Related: [[masterkodi-build-repo]].
