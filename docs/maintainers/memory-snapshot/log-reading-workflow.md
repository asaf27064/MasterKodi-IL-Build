---
name: log-reading-workflow
description: "Where to read Kodi logs — default LOCAL, Cloudflare only for other devices"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 3dfd12c0-e979-4f10-a359-3f2151554677
---

Default to the LOCAL install for logs. We almost always work on Asaf's local PC
install (`C:\MasterKodi IL\portable_data\`), which I have direct Bash/file access
to — read `kodi.log` / `kodi.old.log` there directly (as done all session).

**Why:** the send-logs power item + Cloudflare logs worker
(`masterkodi-logs.asaf27064.workers.dev/v1/logs/recent?key=...`, see
[[masterkodi-build-repo]]) exist for devices I CANNOT reach (Android boxes, other
users). Reaching for Cloudflare when the log is in the local file is wasted steps.

**How to apply:** for a bug on the local box → read the local log file directly.
Only fetch the Cloudflare `/recent` endpoint when Asaf explicitly says he
**sent/uploaded from another device** (e.g. Android). Related:
[[close-kodi-before-editing]].
