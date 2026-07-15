---
name: close-kodi-before-editing
description: HARD RULE — always make sure Kodi is fully closed before touching any live-box file
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 3dfd12c0-e979-4f10-a359-3f2151554677
---

Asaf's rule (2026-07-13, after settings got wiped twice): **ALWAYS verify Kodi is completely closed BEFORE editing anything under `C:\MasterKodi IL\portable_data\`** — check with `tasklist | findstr -i kodi` and tell him to close it if running.

**Why:** Kodi keeps guisettings + every skin/addon settings.xml in memory and REWRITES them on exit. Any edit made to a live settings.xml while Kodi runs is silently reverted when Kodi closes — this wiped the Zephyr custom-ratings config and the disable-match/profile settings, and made it look like "skin settings randomly reset".

**How to apply (updated per Asaf 2026-07-14 evening: "תפסיק לפתוח את הקודי"):** CLOSE Kodi myself, but DO NOT relaunch it — opening is Asaf's: (1) `taskkill //IM kodi.exe` (graceful, lets Kodi write its settings cleanly); (2) poll until the process is gone (force `//F` if it hangs >20s — Kodi has a known stuck-exit issue); (3) THEN edit (Kodi's exit rewrite has already happened, so edits stick); (4) tell Asaf it's ready and HE opens Kodi when he wants. (Earlier that day he asked me to open it too; he reversed that after I kept auto-launching mid-review.) Addon/skin XML files are safer (only read at startup) but the same close-first discipline applies to everything. Related: [[masterkodi-build-repo]]
