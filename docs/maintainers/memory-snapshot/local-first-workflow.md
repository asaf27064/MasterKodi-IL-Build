---
name: local-first-workflow
description: "Asaf's rule (2026-07-10) - make changes LOCALLY on the live install first, push/deploy only after he tests and approves"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 0f3f4e68-6db0-4da2-9a12-aba8db7486cc
---

Asaf said: "כרגע שום שינוי אל תדחוף. תעשה רק מקומית ורק אחרי שנבדוק את הכל נדחוף."

**Why:** the AF3 OSD-button attempt shipped straight through the whole pipeline (repo + CI + install zips + overlay) and rendered broken on-screen — reverting meant touching five places. Skin/UI changes especially can only be judged visually.

**How to apply:** for changes in the MasterKodi ecosystem ([[masterkodi-build-repo]]), edit only the live install (C:\MasterKodi IL\portable_data) first; user tests in Kodi; only after his explicit OK propagate to MasterKodi-IL-Build (+ zips/overlay as relevant) and push. IMPORTANT gotcha: the manifest updater overwrites local edits to a managed addon on next Kodi start (sha mismatch → re-download). To keep a local test edit alive, set that addon's sha in userdata/addon_data/plugin.program.masterkodi.il.wizard/applied_manifest.json to the CURRENT manifest sha so the updater skips it.
