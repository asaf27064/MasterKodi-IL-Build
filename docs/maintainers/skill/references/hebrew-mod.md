# Hebrew mod maintenance (`pov-modified-heb` repo)

This reference covers maintaining `gears_hebrew_subtitles.zip` — the runtime overlay the wizard fetches and applies on top of clean Gears at install time and after each Gears upgrade.

## File-by-file contents of the overlay

The overlay must contain exactly these files. Anything outside this list either does nothing or actively breaks Gears.

### `resources/lib/kodirdil/` — self-contained Hebrew subtitles module

| File | Purpose | Notes |
|---|---|---|
| `__init__.py` | Package marker | Has a comment "Hebrew Subtitles Integration for Gears" |
| `db_utils.py` | Reads/writes `hebrew_subtitles_db.sqlite` and `media_metadata_db.sqlite` in `special://profile/addon_data/plugin.video.gears/` | Path must be gears, not fenlight |
| `hebrew_subtitles_search_utils.py` | Orchestrates the parallel subtitle search across ktuvit/wizdom/opensubtitles | Reads `gears.hebrew_subtitles.*` settings |
| `string_utils.py` | Hebrew/RTL string normalization | No addon-specific refs |
| `thread_utils.py` | Spawns + supervises the search thread | Log tag `Gears-HEBSUBS` |
| `websites/ktuvit.py` | Ktuvit.me API client | Reads `gears.hebrew_subtitles.match_ktuvit` |
| `websites/wizdom.py` | Wizdom.xyz API client | Reads `gears.hebrew_subtitles.match_wizdom` |
| `websites/opensubtitles.py` | OpenSubtitles.com API client | Reads `gears.hebrew_subtitles.match_opensubtitles` + `gears.hebrew_subtitles.opensubtitles_apikey` |
| `websites/hebrew_embedded.py` | Embedded-subs detection via tagline DB | Has a `plugin.video.gears` path ref |
| `websites/__init__.py` | Package marker | Empty |

When kodirdil is copied from an old fenlight-targeted mod, **rebrand**:
- `plugin.video.fenlight` → `plugin.video.gears`
- `fenlight.hebrew_subtitles.` → `gears.hebrew_subtitles.`
- `FenLight-HEBSUBS` → `Gears-HEBSUBS`
- `Integration for FenLight` → `Integration for Gears`
- `FenLight's quality` → `Gears's quality`

### `resources/lib/apis/tmdb_api.py` — Hebrew TMDb language

Gears's stock `tmdb_api.py` hardcodes `language=en` in the movie/tv detail URLs (lines 21, 28). Patch the gears base file:

**At top, after imports:** add `from caches.settings_cache import get_setting` and two helpers:
```python
########### KODIRDIL - Hebrew Language Support ###########
def get_meta_language():
    return get_setting('gears.meta_language', 'en') or 'en'

def get_include_image_language():
    lang = get_meta_language()
    if lang == 'en':
        return 'en,null'
    return '%s,en,null' % lang
##########################################################
```

**In `movie_details()` and `tvshow_details()`:** replace hardcoded `language=en` and `include_image_language=en,null` with `language=%s` (lang) and `include_image_language=%s` (img_lang), threading `get_meta_language()` and `get_include_image_language()` through.

**Why this file matters and why it was missed in the first migration:** FenLight's KODIRDIL comment marker is **uppercase**; my initial migration scanned for lowercase `kodirdil|hebrew|is_hebrew` only and missed all 3 of: tmdb_api.py, indexers/movies.py, indexers/tvshows.py. When auditing for patches, grep for case-sensitive `KODIRDIL` (or `get_meta_language`, `extra_ratings`, `tmdb_rating`) to catch them.

### `resources/lib/apis/torbox_api.py` — ⚠️ NO LONGER OVERLAID (as of Gears 2.2.2)

**Update (2026-06-22): dropped from the overlay.** Gears **2.2.2 added a native TorBox QR/device-code flow** upstream (its `torbox_api.py` grew 275→538 lines with `device_code`/`make_qrcode`), which does exactly what our patch did. So we **removed `torbox_api.py` from the overlay and use upstream's as-is.** Do NOT re-add it. (Removed from `rebuild_hebrew_zip.py` REQUIRED_FILES and the auto-update watch list is unaffected since it watches upstream changes, not our overlay.)

The historical patch (for Gears ≤2.1.x, no longer used) was:
Gears 2.0.7 shipped TorBox auth as a single `kodi_dialog().input('TorBox API Key:')` prompt — typing an 80-char key on a TV remote is awful. The patch replaced `auth()` with a device-code flow that mirrors RD/POV UX.

**Protocol** (reverse-engineered from torbox.app's web client; not currently documented in TorBox's official SDK):
1. `GET https://api.torbox.app/v1/api/user/auth/device/start?app=MasterKodi+IL` → returns:
   ```json
   {"success": true, "data": {
     "device_code": "<opaque 32-hex>",
     "code": "471331",                                  // 6-digit PIN shown to user
     "verification_url": "https://torbox.app/oauth/device",
     "friendly_verification_url": "https://tor.box/link",  // short URL, good for QR
     "interval": 5,
     "expires_at": "<ISO timestamp, ~10 min>"
   }}
   ```
2. User scans QR (= friendly_verification_url) or types the URL into a browser, enters the 6-digit `code`, clicks Continue. The website (already authenticated via Supabase) POSTs to `/user/auth/device/check` to approve — addon doesn't touch this endpoint.
3. Addon polls `POST https://api.torbox.app/v1/api/user/auth/device/token` with `{"device_code": "<opaque>"}` every `interval` seconds:
   - Pending: `{"success": false, "error": "DEVICE_CODE_NOT_USED", ...}`
   - Expired: `{"success": false, "error": "ITEM_NOT_FOUND", ...}`
   - Success: `{"success": true, "data": {...}}` — the data shape isn't fully documented; the `_extract_token()` helper probes for keys `token`, `api_key`, `apikey`, `access_token`, `auth_token`, `session_token` (or `data` being a raw string).

**Patch shape:** add a `KODIRDIL - TorBox OAuth Device Flow` block at module-top with helpers `_device_flow_request_code()`, `_device_flow_poll()`, `_extract_token()`, then replace `auth()` with a device-flow implementation. Keep a `_auth_fallback_apikey()` that's the legacy paste-key prompt — call it when the user cancels the QR dialog (still useful for headless setups).

**Imports added at top:** `import time`, `from modules.kodi_utils import ... progress_dialog, sleep`, `from modules.utils import copy2clip, make_tinyurl, make_qrcode`.

**Why fallback matters:** if TorBox ever changes their device-flow protocol or temporarily blocks the addon, users aren't stuck.

### `resources/lib/indexers/movies.py` and `indexers/tvshows.py` — listitem rating properties

Gears's stock build only fetches OMDB ratings when the user opens the Extras window. Patch both indexer files so that every listitem carries its ratings as window properties, which the Estuary + Arctic Fuse Hebrew skins read for the inline rating bar.

**Two injection points per file:**

1. **In `build_movie_content`/`build_tvshow_content`**, right after `if not meta or 'blank_entry' in meta: return`, pre-fetch OMDB ratings if not already cached:
```python
########### KODIRDIL - OMDB Ratings - Fetch if not cached ###########
if not meta.get('extra_ratings') and meta.get('imdb_id') and self.omdb_api_key:
    try:
        from apis.omdb_api import fetch_ratings_info
        fetch_ratings_info(meta, self.omdb_api_key)
    except: pass
#####################################################################
```

2. **Just before the final `set_properties({...})` call**, compute and merge rating props:
```python
########### KODIRDIL - Per-item rating properties for skin display ###########
rating_props = {}
tmdb_rating = meta_get('rating')
if tmdb_rating:
    try: rating_props['gears.tmdb_rating'] = str(round(float(tmdb_rating), 1))
    except: pass
extra_ratings = meta_get('extra_ratings')
if extra_ratings:
    for key in ('metascore', 'tomatometer', 'tomatousermeter', 'imdb'):
        rating_data = extra_ratings.get(key, {})
        if rating_data.get('rating') and rating_data['rating'] not in ('', '%'):
            rating_props['gears.%s_rating' % key] = rating_data['rating']
            rating_props['gears.%s_icon' % key] = rating_data.get('icon', '')
##############################################################################
```
Then add `**rating_props` to the end of the `set_properties({...})` dict literal.

3. **In `worker()`**, initialize the OMDB key:
```python
########### KODIRDIL - OMDB API key for pre-fetching ratings into listitems ###########
self.omdb_api_key = settings.omdb_api_key() if settings.extras_enable_extra_ratings() else None
if self.omdb_api_key in ('empty_setting', ''): self.omdb_api_key = None
########################################################################################
```

The skin layers (Estuary + Arctic Fuse) read `ListItem.Property(gears.tmdb_rating)`, `gears.imdb_rating`, `gears.metascore_rating`, `gears.tomatometer_rating` + `_icon`, and `gears.tomatousermeter_rating` + `_icon`. Their visibility conditions guard against empty strings so partial ratings render fine.

### `resources/lib/modules/sources.py` — scrape orchestration

This is the **gears** base file (not a stale fenlight copy), with two injection blocks added:

**Top of file, after the imports:**
```python
########### KODIRDIL - Hebrew Subtitles Integration ###########
from kodirdil import thread_utils
from kodirdil import hebrew_subtitles_search_utils

def is_hebrew_subtitles_enabled():
    return get_setting('gears.hebrew_subtitles.enable_matching', 'true') == 'true'
###############################################################
```

**Inside `get_sources(self)`:** start the search thread at the top, wait for it before `play_source(results)`. The injection wraps the existing logic:
```python
def get_sources(self):
    enable_hebrew_subtitles = is_hebrew_subtitles_enabled()
    search_hebrew_subtitles_thread = None
    if enable_hebrew_subtitles:
        try:
            # build metadata from self.meta, call thread_utils.create_search_hebrew_subtitles_thread
            ...
        except Exception as e:
            kodi_utils.logger("Gears-HEBSUBS", f"Error starting Hebrew subtitles thread: {str(e)}")

    # [original gears get_sources body]

    if enable_hebrew_subtitles and search_hebrew_subtitles_thread is not None:
        try:
            search_hebrew_subtitles_thread.join(timeout=30)
        except Exception as e:
            kodi_utils.logger("Gears-HEBSUBS", f"Error waiting for Hebrew subtitles thread: {str(e)}")

    # [original gears tail: process_post_results / autoscrape / play_source]
```

### `resources/lib/windows/sources.py` — results-window UI

Patches to the gears base file:
1. **Top imports:** add `get_setting` to the `caches.settings_cache` import line, and import `kodirdil` modules.
2. **Helper functions** after imports: `is_hebrew_subtitles_enabled()`, `get_minimum_sync_percent()`, `is_embedded_search_enabled()` — each reads a `gears.hebrew_subtitles.*` setting.
3. **Filter handler** in the filter dispatch (look for `elif filter_value == 'showuncached'`): add three new branches `hebrew_subs_only`, `sort_hebrew_subs` (filter on `has_hebrew_subs` listitem property), and `sdr_only` (filter via `self._is_hdr_item(i)`).
4. **`make_items` setup:** before `def builder(results)`, add the Hebrew matching context (load `total_subtitles_found_list`, `hebrew_embedded_taglines`, initialize counters).
5. **Inside the builder:** before the final `set_properties({'name': ...})`, run subtitle matching against the source, append the subtitle text to `size_label`, prefix `extraInfo` with `'[B][COLOR red]הופעל[/COLOR][/B] | '` if `self._is_tried_source(item)`, and add `'has_hebrew_subs': has_hebrew_subs` to the set_properties dict.
6. **`make_filter_items`:** before `data.extend(qualities)`, append the two Hebrew filter buttons (lime + cyan) and the SDR-only filter button (yellow) — see the existing file for exact wording. The SDR button is only shown when `0 < sdr_count < len(self.item_list)`.
7. **`set_properties`:** after `self.setProperty('title', ...)` and before the final `total_results` set, compute `hebrew_subtitles_panel_text` via `hebrew_subtitles_search_utils.generate_subtitles_match_top_panel_text_for_sync_percent_match` and append it to the `total_results` property.

**Class-level attributes and methods to add** (after `__init__`, before `get_provider_and_path`):

```python
# Tried-source tracking - flag clicked sources with red "הופעל" badge
self.tried_sources_key = 'tried_sources_%s_%s_%s' % (self.meta_get('tmdb_id', ''),
                                                       self.meta_get('season', ''),
                                                       self.meta_get('episode', ''))   # in __init__

def _get_tried_sources(self):            # reads csv from Home property
def _add_tried_source(self, source):     # appends to set, cap 50, persists
def _is_tried_source(self, source):      # source is a DICT, not a listitem

# HDR detection for SDR-only filter
_hdr_tags = ('[B]HDR[/B]', '[B]D/VISION[/B]')                                  # class attrs
_hdr_words = ('.HDR.', '.HDR10.', ..., '.DOLBYVISION.', '.HLG.')               # class attrs
def _is_hdr_item(self, item):            # item is a LISTITEM, not a dict
```

**onAction injection (before `self.selected = ('play', chosen_source)`):**
```python
self._add_tried_source(chosen_source)
```

**Why these were missed:** The SDR filter and tried-source tracking use plain `########### KODIRDIL - X ###########` and `########### SDR Only Filter ###########` comment headers — my initial migration scan grepped only for `kodirdil` lowercase and missed the SDR section entirely (no kodirdil text). Always grep both case-sensitive `KODIRDIL` AND feature-name strings like `_is_hdr_item`, `tried_sources_key`, `filter_value == 'sdr_only'`. The audit script (`scripts/scan_residual_fenlight.py`'s sibling `rebuild_hebrew_zip.py`) now guards these.

### `resources/lib/caches/settings_cache.py` — settings registry

Take the gears base file and append 10 settings before the closing `]` of the settings list:
```python
{'setting_id': 'meta_language', 'setting_type': 'string', 'setting_default': 'en', 'settings_options': {'en': 'English', 'he': 'Hebrew (עברית)'}},
{'setting_id': 'hebrew_subtitles.enable_matching', 'setting_type': 'boolean', 'setting_default': 'true'},
{'setting_id': 'hebrew_subtitles.minimum_sync_percent', 'setting_type': 'string', 'setting_default': '70'},
{'setting_id': 'hebrew_subtitles.match_embedded', 'setting_type': 'boolean', 'setting_default': 'true'},
{'setting_id': 'hebrew_subtitles.match_ktuvit', 'setting_type': 'boolean', 'setting_default': 'true'},
{'setting_id': 'hebrew_subtitles.match_wizdom', 'setting_type': 'boolean', 'setting_default': 'true'},
{'setting_id': 'hebrew_subtitles.match_opensubtitles', 'setting_type': 'boolean', 'setting_default': 'true'},
{'setting_id': 'hebrew_subtitles.ktuvit_email', 'setting_type': 'string', 'setting_default': ''},
{'setting_id': 'hebrew_subtitles.ktuvit_password', 'setting_type': 'string', 'setting_default': ''},
{'setting_id': 'hebrew_subtitles.opensubtitles_apikey', 'setting_type': 'string', 'setting_default': ''}
```

### `resources/skins/Default/1080i/settings_manager.xml` — UI surface

Two changes to the gears base XML:

1. **Add a top-level category item.** In the main category `<content>` block (look for `id="80"` Playback), add after it:
   ```xml
   <item id="90">
     <property name="setting_label">Hebrew Subtitles</property>
   </item>
   ```

2. **Add the submenu items.** After the last `Container(2000).HasFocus(80)` item (the Playback section's last item, currently `auto_enable_subs`), insert a HEBREW SUBTITLES 90 block — about 10 `<item>` elements, all visible only when `Container(2000).HasFocus(90)`, with onclick handlers like `RunPlugin(plugin://plugin.video.gears/?mode=settings_manager.set_boolean&amp;setting_id=hebrew_subtitles.enable_matching)`. Match the structure of the existing Playback items so layout stays consistent.

### `version.txt` at zip root

A single line — must match `gears_version.json` `"version"` exactly. If you forget, the wizard's install-status check will think Hebrew was never installed.

### `resources/lib/indexers/navigator.py` + `resources/lib/modules/meta_lists.py` — per-genre icons

Gears stock renders every genre with the same `genres.png` icon. FenLight assigned a distinct `genre_X.png` to each. This is a **two-file** patch:

**`modules/meta_lists.py`** — add an `'icon'` field to every genre dict in `movie_genres()`, `tvshow_genres()`, `anime_genres()`:
```python
{'id': '28', 'name': 'Action', 'icon': 'genre_action'},
{'id': '12', 'name': 'Adventure', 'icon': 'genre_adventure'},
# ... 45 genres total across the 3 functions
```

**`indexers/navigator.py`** (in `genres()` method, around line 358 in gears 2.0.7) — read the `'icon'` field:
```python
# stock gears:
for i in function(): self.add({'mode': mode, 'action': action, 'key_id': i['id'], 'name': i['name']}, i['name'], 'genres')

# patched:
for i in function(): self.add({'mode': mode, 'action': action, 'key_id': i['id'], 'name': i['name']}, i['name'], i.get('icon', 'genres'))
```

The `.get('icon', 'genres')` fallback keeps the patch safe — if upstream changes the genre dict shape and drops `'icon'`, the addon still renders (just back to one icon).

The 24 `genre_*.png` files are bundled in `resources/media/icons/` (see below).

### `resources/lib/service.py` — Debrid subscription banner

Patches gears stock service.py to add a `DebridSubscriptionCheck` class that runs once per Kodi session at addon startup. Iterates a `DEBRID_SUBS` table of all 6 supported debrids and queries each one the user has authenticated. For each, shows a Hebrew toast with days remaining (or hours if <24h) and expiration date.

**The `DEBRID_SUBS` table** — one tuple per debrid:
```python
# (display_name, enabled_setting, token_setting, api_module, api_class, field_path, ts_format)
('Real Debrid', 'gears.rd.enabled', 'gears.rd.token', 'apis.real_debrid_api', 'RealDebridAPI', 'expiration',            'iso'),
('AllDebrid',   'gears.ad.enabled', 'gears.ad.token', 'apis.alldebrid_api',   'AllDebridAPI',  'data.user.premiumUntil', 'unix_s'),
('Premiumize',  'gears.pm.enabled', 'gears.pm.token', 'apis.premiumize_api',  'PremiumizeAPI', 'premium_until',          'unix_s'),
('Offcloud',    'gears.oc.enabled', 'gears.oc.token', 'apis.offcloud_api',    'OffcloudAPI',   'expirationDate',         'unix_ms'),
('EasyDebrid',  'gears.ed.enabled', 'gears.ed.token', 'apis.easydebrid_api',  'EasyDebridAPI', 'expiry_unix_seconds',    'unix_s'),
('TorBox',      'gears.tb.enabled', 'gears.tb.token', 'apis.torbox_api',      'TorBoxAPI',     'data.premium_expires_at','iso'),
```

`field_path` is a dotted JSON path (handles nested response wrappers). `ts_format` controls parsing: `iso` (string), `unix_s` (seconds since epoch), `unix_ms` (milliseconds).

**Banner format (Option C):**
- Heading: `<Service> · <N> ימים נותרו` (or `<N> שעות נותרו` when <24h, or `· פג תוקף` when expired)
- Body: `פג תוקף: DD/MM` (adds `HH:MM` when <24h or expired)
- Date in local timezone via `.astimezone()` so user sees their local time, not UTC
- Duration: 10s when ≤1 day, 8s when ≤7 days, 6s otherwise

**Per-service icon:** uses `resources/media/icons/{realdebrid|alldebrid|premiumize|offcloud|easydebrid|torbox}.png` from the bundled overlay so the user knows visually which service the banner is about.

**Stacking:** if user has multiple debrids enabled, banners fire sequentially with `time.sleep(1.0)` between them so they don't replace each other in Kodi's single-slot notification area.

**Throttle:** Kodi window property `gears.debrid_subscription_banner_shown=true`. Once per Kodi session. Set up-front (before the loop) so even a mid-loop exception doesn't double-fire on the next start.

**Silent no-ops** (never crashes startup):
- `<svc>.enabled` ≠ `'true'`
- `<svc>.token` empty or `'empty_setting'`
- `account_info()` returns nothing
- Expiration field missing from response
- Timestamp unparseable
- Any exception (per-service `try/except` keeps the loop going)

This is the **only** way `service.py` is legitimately in the Hebrew overlay. The legacy FenLight Hebrew zip used to ship a stale FenLight service.py that would downgrade Gears — that's still forbidden. We always layer the `DebridSubscriptionCheck` patch on top of **clean gears base service.py**, not a stale FenLight copy.

**Adding a new debrid:** if upstream Gears adds a new debrid service later, append a new tuple to `DEBRID_SUBS` with the service's setting keys + account_info field. No other code changes needed.

### `resources/media/icons/*.png` and `resources/media/network_icons/*.png` — bundled icons

The Hebrew overlay bundles **102 in-addon icons** and **76 network logos** so that any Hebrew install (fresh or via "reinstall Hebrew" in the wizard) preserves the original FenLight look. Without this, a fresh Gears install would show Gears's stock icons.

Sourced from the Tikipeter mirror of FenlightAnonyMouse (the original repo is dead):

```
https://github.com/Tikipeter/fenlight.github.io/tree/main/packages/media/icons
https://github.com/Tikipeter/fenlight.github.io/tree/main/packages/media/network_icons
```

The `network_icons/` filenames are hash-based (e.g., `jI5c3bw.png` for Netflix). The mapping is encoded in the SELECTED NETWROKS shortcut folder inside `navigator.db` — see `references/base-build.md`. If a network is added/removed, both navigator.db and the icon need to change.

To refresh icons from upstream:

```python
import os, urllib.request
base = 'https://raw.githubusercontent.com/Tikipeter/fenlight.github.io/main/'
# Get the list via `gh api repos/Tikipeter/fenlight.github.io/git/trees/main?recursive=1`
# then iterate over packages/media/icons/*.png and packages/media/network_icons/*.png
```

These do NOT belong in the workflow's `HEBREW_FILES` watch list — they're bundled resources, not patches against upstream gears.

## Workflow A: Bump for a new Gears upstream release

Triggered when the GitHub Action in `pov-modified-heb` opens an issue, or when the user asks "is there a new Gears version?".

### Step 1 — Read the upstream version from chainsrepo

```bash
curl -fsSL https://raw.githubusercontent.com/unhingedthemes/zips/main/_zips/addons.xml \
  | python -c "import sys, re; m = re.search(r'<addon[^>]*id=\"plugin\.video\.gears\"[^>]*version=\"([^\"]+)\"', sys.stdin.read()); print(m.group(1) if m else 'NOT FOUND')"
```

### Step 2 — Diff the overlay-relevant files between current `compatible_gears` and the new version

The legacy fenlight skill's workflow walked 8 files. For Gears, **only watch the 3 source files + settings_manager.xml**:

```python
files_to_check = [
    'modules/sources.py',
    'windows/sources.py',
    'caches/settings_cache.py',
    # plus settings_manager.xml at resources/skins/Default/1080i/settings_manager.xml
]
```

Download both versions, diff each file. The GitHub Action does this automatically and opens an issue with the diff snippets.

### Step 3 — If diffs are mechanical, just bump

If only whitespace/comments changed in the watched files, bump `compatible_gears` in `gears_version.json` and you're done. The Action does this automatically (`auto-bump`).

### Step 4 — If structural diffs, re-patch

Re-apply the patches from the section above onto the new gears base file. Validate every patch landed by greping for `KODIRDIL` markers in the result.

Always:
1. `python -m py_compile` each patched file.
2. Validate `settings_manager.xml` via `ET.parse()`.
3. Rebuild zip via `scripts/rebuild_hebrew_zip.py`.

## Workflow B: Bumping the Hebrew mod version itself

Triggered when the user wants to ship a new version of the Hebrew overlay (not just compatibility-bump):

1. Edit `gears_version.json`:
   - `"version"` — semver bump (patch for fixes, minor for added features, major for breaking changes)
   - `"compatible_gears"` — leave unless you also tested a new upstream
   - `"min_gears"` — leave unless you intentionally drop support
   - `"changelog"` — short Hebrew or English description
2. Edit `version.txt` inside `gears_hebrew_subtitles.zip` (use `scripts/rebuild_hebrew_zip.py --version <new>`).
3. Commit and push.

## Workflow C: Rebuild the zip from a working tree

When you have a fresh set of overlay files (e.g., after re-patching for upstream):

```bash
python ~/.claude/skills/masterkodi-il-builder/scripts/rebuild_hebrew_zip.py \
    --src <path-to-working-tree> \
    --out pov-modified-heb/gears_hebrew_subtitles.zip \
    --version 2.0.0
```

The script:
- Validates that the 4 critical patches contain Hebrew markers (`kodirdil`, `Gears-HEBSUBS`)
- Excludes `__pycache__/`, `.pyc`, `.DS_Store`
- Writes a fresh `version.txt`
- Confirms by listing the resulting namelist

## The GitHub Action

File: `pov-modified-heb/.github/workflows/gears-auto-update.yml`

What it does (on cron `0 6,18 * * *`):
1. Reads upstream version from `unhingedthemes/zips` addons.xml.
2. Compares against `compatible_gears` in `gears_version.json`.
3. If different, downloads **only the new** upstream zip and diffs the watched files against the stored **`gears_baseline/`** (clean copies of the watched files for the current compatible version).
4. If no watched files changed → auto-commits a bump (and advances `gears_baseline/BASELINE_VERSION.txt`).
5. If watched files changed → opens a labeled issue with the diff. If the new zip can't be downloaded → opens an issue instead of failing silently. Issues are de-duped per upstream version.

**Why the baseline (fixed 2026-06-22):** the workflow used to download the OLD upstream zip to diff against — but upstream prunes old versions, so the old-zip fetch 404'd and the step `exit 0`'d **silently** (success, no bump, no issue). It was blind from when 2.0.7 was pruned until 2.2.2. Now it diffs against `gears_baseline/` (committed in the repo), which never 404s.

When you re-patch the overlay for a new Gears version, **refresh `gears_baseline/`** from that clean version and set `BASELINE_VERSION.txt`. If you rename a watched file or add a patch target, update the `HEBREW_FILES` list in the workflow AND `REQUIRED_FILES` in `rebuild_hebrew_zip.py`.

**Current state (2026-06-22):** compatible_gears **2.2.2**, overlay version **2.1.0**, baseline **2.2.2**. TorBox dropped (upstream native).

## Common pitfalls

- **Setting prefix confusion.** When you read a setting at runtime, it's `gears.X`. When you define it in `settings_cache.py`, no prefix. Don't double-prefix.
- **Empty `version.txt`.** A 0-byte file silently breaks the wizard's "is Hebrew installed?" check. Always write a trailing `\n`.
- **Including stale files.** If you accidentally ship `apis/tmdb_api.py`, `service.py`, `indexers/movies.py`, `indexers/tvshows.py`, or `modules/metadata.py`, you will overwrite Gears with old FenLight code and break new Gears features. Reject overlays that contain those files.
- **Non-ASCII filenames.** The wizard's extract loop has `filename.encode('ascii'); except: continue` — Hebrew filenames are silently dropped. Keep filenames ASCII; put Hebrew inside file contents.
- **Mixed line endings.** Use `newline='\n'` when writing Python source files. CRLF inside a Python file is valid but creates noisy diffs.
