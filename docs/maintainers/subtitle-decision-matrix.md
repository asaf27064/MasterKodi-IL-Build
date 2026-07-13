# GearsAI subtitle decision matrix

What happens for every subtitle situation when playback starts, as of
gearsai **1.0.34** (2026-07-13). Modes:

- **Automatic** — happens on its own at play (the autosub service).
- **Cancelable** — gated by a setting and/or overridable in Kodi's subtitle menu.
- **Manual choice** — the user picks it from the subtitle-search dialog.

## No Hebrew subtitle exists

| # | Situation | What happens | Mode |
|---|---|---|---|
| 1 | No subs at all + no embedded track in another language | Autosub searches every source (Ktuvit/Wizdom/OpenSubtitles/SubDL/…) and, if English is found, translates. Here there is nothing → no subtitle. Nothing can help — no source to translate from or sync against. | — |
| 2 | No subs + embedded track in another language (e.g. English) | Autosub finds no Hebrew. The embedded English is NOT auto-translated (that's the rejected full-download case — subtitle packets are interleaved through the whole ~30 GB file). It stays available manually in Kodi's subtitle menu. If an EXTERNAL English also exists → the AI-translate path can run. | Embedded = manual only |
| 2b | No Hebrew but an external English exists | The heart of the system: AI translation produces Hebrew — community pool first (free, instant), otherwise Gemini. Automatic when `auto_translate` is on; also available as the "AI translate" row. | Automatic (setting) / manual row |
| 3 | No external subs, pool Hebrew synced to this release | Via the translation path (when English was found) the pool is checked and the matching Hebrew is placed automatically. If no English was found at all, the pool is not consulted automatically — the "AI translate" row pulls from it. | Automatic via translate path / else manual |
| 4 | Pool Hebrew exists but timed for a DIFFERENT release | `_pool_retime` runs automatically: (a) text alignment vs a matching external English (`resync.retime`) → (b) timestamp shift vs that English (`sync_align.align`) → (c) if `mkv_sync_oracle` is ON: the playing file's embedded-track anchor (`mkv_probe` + `align_to_anchors`). Success → placed synced. All fail → placed as-is; the manual 🔄 sync row can still fix it. | Automatic + manual fallback |

## Hebrew subtitle exists

| # | Situation | What happens | Mode |
|---|---|---|---|
| 5 | Hebrew synced and working | Autosub placed it (results sorted by release-match %). Done. | Automatic, replaceable |
| 6 | Embedded Hebrew track in the file | `auto_place_hebrew_embedded_subs` (default on) places it automatically — perfectly synced by definition. Badged `[HEB\|LOC] מוטמע` in the gears sources window. | Automatic, cancelable (setting) |
| 7 | Hebrew placed but out of sync; NO embedded track in another language | Direct downloads (e.g. Ktuvit) are never auto-retimed. The user presses the 🔄 "סנכרן את הכתובית שמוצגת כעת" row (top of the subtitle dialog, shown only while a Hebrew sub is active) → path (2): align to a release-matched external English. No English → left unchanged (fail-open). | Manual (sync row) |
| 8 | Hebrew out of sync; embedded track in another language EXISTS | The 🔄 sync row → path (1): the embedded English inside the playing file itself = exact ground-truth timing for this release → snapped on perfectly. This is the MKV-oracle case. | Manual (sync row) |

## The 🔄 manual sync row (1.0.34)

- Appears at the top of the GearsAI subtitle results dialog only while a Hebrew
  sub is active (Home-window property `gearsai.current_heb_sub`, recorded at both
  placement sites in autosub.py).
- Keeps the Hebrew TEXT; only re-times. Reference priority: embedded English via
  the MKV oracle (bypasses the `mkv_sync_oracle` opt-in — pressing the button IS
  the consent), then release-matched external English.
- Bandwidth (~40–64 MB probe on 4K, less on 1080p) is spent only on explicit press.
- Everything in-memory; the only artifact is the re-timed `.srt` in the normal
  temp path.

## Relevant settings (all cancelable)

| Setting | Default | Controls |
|---|---|---|
| `autosub` | on | the whole automatic flow |
| `auto_translate` | on | AI translation when only English exists |
| `pool_enabled` / `pool_contribute` | on | community pool lookup / sharing |
| `auto_place_hebrew_embedded_subs` | on | case 6 |
| `prefetch` | **off** | search during gears scrape (speed only) |
| `mkv_sync_oracle` | **off** | automatic embedded-anchor sync in case 4 |

Nothing is locked: Kodi's subtitle menu can always disable the current sub or
pick another row (list is sorted by match %).
