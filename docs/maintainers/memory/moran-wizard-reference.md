---
name: moran-wizard-reference
description: "Where Moran's POV-IL Kodi build + wizard is installed locally, and its KEEP-data framework we borrow from"
metadata: 
  node_type: memory
  type: reference
  originSessionId: 0f3f4e68-6db0-4da2-9a12-aba8db7486cc
---

Moran's POV-IL build is installed locally at **`C:\Program Files\Kodi POV IL\`** (portable, `portable_data/` alongside `kodi.exe`). His wizard addon: **`portable_data/addons/plugin.program.kodipovilwizard`** (v1.0). Use it as a reference implementation when building MasterKodi wizard features.

His clean-install "keep my data" system (classic Ezra/Merlin build-wizard lineage) — the model I adapted for our [[masterkodi-build-repo]] `keep.py`:
- `resources/libs/common/config.py`: per-category `KEEP*` flags read from wizard settings (`get_setting('keeptrakt'/'keepdebrid'/'keeplogin'/'keepfendata'/'keepfavourites'/'keepsources'/'keepadvanced'/'keepguisettings'/'keepaddons33db'/'keepwhitelist'...)`.
- `resources/libs/common/custom_save_data_config.py`: overrides those defaults from a central GitHub JSON (`MoranTheKing/Kodi-POV-IL/main/wizard/assets/custom_save_data_config/custom_save_data_config.json`) + an addon whitelist/blacklist (keep extra user-installed addons).
- `resources/libs/traktit.py` / `debridit.py`: a table mapping each known addon -> its `settings.xml` path + the exact setting-ids holding trakt/debrid tokens; save to a folder, restore after install.
- `save.py` / `restore.py` / `backup.py`: the actual snapshot/restore.

DONE in ours (wizard 2.4.12/2.4.13): our `resources/libs/keep.py` clean-install "what to keep" multiselect (Debrid/Trakt/Gemini-personal/Gears-content/favourites) + the **addon whitelist** (`detect_extras()` = home/addons minus manifest minus our machinery; Kodi system addons live in special://xbmc/addons so they're excluded). Backup-before-wipe -> restore-after, extras re-enabled in Addons33.db.
