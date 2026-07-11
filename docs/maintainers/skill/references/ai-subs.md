# MasterKodi AI Subs (`service.subtitles.gearsai`) + community pool + wizard self-updater

Our own AI Hebrew subtitle addon: when a video has no Hebrew subs, it fetches the best-matching English from OpenSubtitles and translates it to Hebrew with Google Gemini (gender-aware, RTL-correct), with a shared community pool + free cross-release re-sync.

## Addon layout (`FenLight_Estuary/addons/service.subtitles.gearsai/`)
Canonical source = the working tree above. Shipped 3 ways: baked into `FenLight_Estuary.zip`, as `gearsai_subtitles.zip` in `pov-modified-heb`, and self-updates on-device.

| Module | Role |
|---|---|
| `default.py` | subtitle-module entry: `search`/`download` (+ test_connection, pair_key, show_usage, update_now, test_pool) |
| `service.py` | `GearsAIPlayer` — onAVStarted → supersede-able worker; waits for DarkSubs; pool→human→translate; rating prompt |
| `resources/lib/gemini.py` | Gemini client; model chain + fallback; 429 day/minute classifier; `MODEL_QUALITY`/`label`/`is_best`. DEFAULT_MODEL=`gemini-2.5-flash` |
| `translate.py` | parallel chunked translate (ThreadPoolExecutor, `parallel_chunks`), thread-safe model fallback, `abort_cb` (TranslationAborted) |
| `prompt.py` | gender/RTL/continuity prompt + cast block |
| `srt.py`,`rtl.py`,`match.py` | SRT parse/serialize; DarkSubs-style RTL reorder; DarkSubs-identical release match (`match.player_release()` reads window prop `subs.player_filename` for debrid UUIDs) |
| `pool.py` | community pool client (lookup/fetch/contribute/vote/flag/best_anchor); USER_AGENT set (Cloudflare 403s python-urllib); baked DEFAULT_POOL_URL/TOKEN |
| `resync.py` | reference-based re-time: align our English anchor ↔ this release's English (difflib), stamp Hebrew onto its timecodes; confidence gate (REPLACE_MIN_RATIO 0.55, min 0.85) |
| `opensubtitles.py`,`tmdb.py`,`quota.py`,`cache.py`,`kodirdil.py`,`selfupdate.py`,`pair.py`,`progress_overlay.py` | OS search; TMDb cast/gender; daily quota counter; atomic SRT cache (+English anchor sidecar); reads gears' Hebrew verdict; self-update; QR key pairing; top-center overlay |

## Community pool (Cloudflare, `cloudflare/` in the addon = maintainer-only, should NOT ship on-device)
- Worker + **D1** (no R2/no card). `subs` table (metadata + `has_anchor`) + `blobs` (srt + `eng` anchor). Live: `https://masterkodi-subpool.asaf27064.workers.dev`, token `mk-76ed711408c449eda0c5a2d868720b0438e36309`, deployed on Asaf's CF (acct 482ed527…, db id 855163c0…). Quality gate (valid SRT + actually-Hebrew + cue count), sha256 dedup, anon contributor hash, vote/flag hide. Deploy guide in `cloudflare/DEPLOY.md`.
- Flow: pool hit → instant; same movie diff release → free re-sync from a stored anchor; else translate fresh + contribute (with English anchor).

## Distribution + updates
- **New installs**: gearsai is baked into `FenLight_Estuary.zip` (v1.0 release) + the EXE/APK bundle the wizard that fetches it.
- **Existing installs**: the **wizard** (`plugin.program.masterkodi.il.wizard` ≥2.3.1) — `service.py check_gears_gearsai_updates()` polls `gearsai_version.json` (and `gears_version.json`), prompts, and installs via `GearsaiInstaller`/`GearsHebrewInstaller`, reusing `perform_addon_updates`/`perform_hebrew_updates`. Gated: Gears only moves when `compatible_gears == upstream-latest`.
- **gearsai also self-updates** (`selfupdate.py`) — ⚠️ this OVERLAPS the wizard's gearsai handling (two mechanisms). Pick one authority (see Known issues).

## Publish an update (maintainer)
- **AI Subs change**: edit the addon → bump `addon.xml` version → rebuild `gearsai_subtitles.zip` + bump `gearsai_version.json "version"` → push to `pov-modified-heb`; rebuild `FenLight_Estuary.zip` (re-inject) + re-upload to `v1.0`.
- **Pool change**: `cd cloudflare && wrangler deploy` (+ `wrangler d1 execute … --file schema.sql` for schema).

## Translation quality pipeline (since 0.4.0)
The cold-translate path: `default.py do_download` / `service.py` → `translate.translate_srt`.
- **Chunking**: 80 entries/chunk (`DEFAULT_CHUNK_SIZE`), 4–6 parallel (`parallel_chunks`), each chunk carries `CONTEXT_TAIL=4` prev English lines. Model chain = chosen → fallbacks (`gemini.model_chain`); per-DAY quota advances the shared model index.
- **Pass-0 gender analysis** (`analysis.py character_map`, setting `gender_analysis` default ON): ONE call over the full dialogue → a character/gender guide → injected into EVERY chunk via `prompt.build(gender_map=...)`. **Fail-open**: error/short/INSUFFICIENT → `''` → prompt byte-identical to pre-0.4.0. Strictly additive (cast list + line evidence still take precedence). This is the main lever left for gender — tune the Pass-0 prompt if gender still slips.
- **Fast mode** (setting `fast_mode` default OFF): `gemini.generate(thinking_budget=0)` (sets `thinkingConfig.thinkingBudget`, gated by `_supports_thinking` = 2.5/3.x only) + chunk size 110. OFF sends no thinkingConfig = unchanged behavior. The thinking-off quality risk is covered by Pass-0 (gender precomputed).
- **Truncation split** (`_translate_subchunk`): now rebuilds the FULL prompt (cast+guide+context via threaded `pmeta`), not a context-less minimal one.
- INVARIANT: any change here must keep "both new settings off → identical output to before". Verify with the offline `prompt.build` with/without `gender_map` test.

## English sources (multi-provider, since 0.3.0)
`resources/lib/sources.py` aggregates every free, keyless English provider and
is the single entry point (`search_english` / `download`). The downloader
auto-detects gzip/zip/raw, so adding a source = add one function to `PROVIDERS`.
Current providers: `opensubtitles.py` (imdb match, falls through to a fuzzy
title query only when sparse) + `podnapisi.py` (keyless JSON search, fail-open).
Hebrew lookups stay OpenSubtitles-only.

## Pool Worker extras (since 0.3.0)
- `GET /stats` (public HTML dashboard) + `GET /v1/stats` (JSON, consumed by the
  build status page). `json()` sends `Access-Control-Allow-Origin: *`.
- Opt-in failure telemetry: addon setting `report_failures` (default OFF) →
  `POST /v1/telemetry/fail` → `failures` table (re-run `schema.sql` to migrate).
- Moderation behind `ADMIN_TOKEN` secret: `GET /v1/admin/flagged`,
  `POST /v1/admin/delete {id}`.

## Build status page
`MasterKodi_Build/status.html` — static, client-side fetch of all version JSONs
+ upstream Gears + wizard + pool `/v1/stats`. Deploy as gh-pages `status/index.html`
(new path; does not touch the live site).

## Publishing (one command)
`scripts/publish.py` rebuilds `gearsai_subtitles.zip` into the pov repo
(cloudflare/ + .pyc excluded, asserted), bumps `gearsai_version.json`, and with
`--reinject` rebuilds `FenLight_Estuary.zip`. Deploys are opt-in (`--push`,
`--upload`); without them it prints the exact git/gh commands.

## Known issues / TODO
1. **[HIGH, untested] gearsai first-install enable** — `GearsaiInstaller` enables a brand-new addon via `UpdateLocalAddons` + JSON-RPC `SetAddonEnabled`; Kodi may leave it disabled or the 2s wait may be short. (User tested 2026-06-26: works while addon is in addons33 — OK in practice.)
2. ✅ **RESOLVED (0.3.0)** Redundant updaters — gearsai in-addon self-update is now a **no-op** (`service.py run()`); the wizard is the sole updater. `selfupdate.py` kept only for the manual settings button.
3. ✅ **RESOLVED (0.3.0)** cloudflare/ no longer ships — excluded by `publish.py` + `build_release_zips.py` (asserted) and by the CI `validate-releases.yml` gate.
4. **Pool token is public** (baked in the addon) — soft gate only; add Worker per-IP rate-limiting if abused.
5. Wizard `_old_<ts>` backup dirs are now swept on startup (`_cleanup_old_addon_dirs`, wizard 2.3.2); overlay copy-over is still additive (doesn't prune orphans) — fine in practice.
