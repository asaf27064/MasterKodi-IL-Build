# Playback traces — Gears vs POV

Like-for-like traces of a single **click a title → source list → play** flow, so
the two content engines can be compared on evidence instead of impression.

Captured with `tools/trace_playback.py`, which marks the size of `kodi.log`
before the click and reads only what was appended after — so nothing older
pollutes the trace. Baselines are committed here **because kodi.log rotates**: a
restart moves it to `kodi.old.log` and the 2026-08-01 POV trace we wanted to
compare against was already gone by the next morning.

## How to capture

Same title, same skin, same box — only the engine differs. TMDb-widget entry
point (that is how Asaf actually plays).

```bash
python tools/trace_playback.py mark        # BEFORE clicking the title
#   ... click it, wait for the source list ...
python tools/trace_playback.py show        # look at it
python tools/trace_playback.py save pov    # write docs/playback-traces/pov.json
python tools/trace_playback.py diff gears pov
```

## Baseline: `gears.json`

Supergirl (tt8814476), Zephyr skin, Gears 2.3.8, TMDb-widget click,
2026-08-02 11:28. Times are relative to the first event.

| +secs | stage |
|---|---|
| +0.0 | tmdbhelper resolves its `dummy.mp4` |
| +0.6 | GearsAI **skips the placeholder** (the 1.0.45 guard) |
| +3.2 | dummy finished — **~3.2s of pure placeholder** |
| +7.2 | Gears scrape starts · GearsAI prefetch notified · Hebrew-subs thread |
| +7.5 | Wizdom 0 / OpenSubtitles 0 subtitles |
| **+11.2** | **source list shown** · `Sources with matched subtitles: 0` |
| +13.1 | prefetch ready — 23 subs |

Notes for whoever compares:

- The **dummy is tmdbhelper's, not the engine's** — it appears on BOTH engines.
  The ~4s gap between "dummy finished" and "scrape starts" is tmdbhelper's
  `dummy_duration` + `dummy_delay`, both still shipped at the slow `1.0`
  defaults (0.1 is available; tuning deferred by Asaf — see the memory note).
- `Sources with matched subtitles: 0` here is **correct**, not a fault: all
  three Hebrew sites returned 0 for this title. The engines share that
  `kodirdil` code, so POV should show the same.
- The one real engine difference in this path: **Gears publishes
  `subs.player_filename` itself** (`modules/player.py`), POV only through our
  overlay 0.1.5+. Everything else — dummy, prefetch trigger, Hebrew-subs thread
  — is shared.

## Still to capture

`pov.json` — the same click on the same title with POV installed, then
`diff gears pov`.
