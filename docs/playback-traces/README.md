# Playback traces — Gears vs POV

Like-for-like traces of a single **click a title → source list** flow, so the two
content engines can be compared on evidence instead of impression.

Captured with `tools/trace_playback.py`, which marks the size of `kodi.log`
before the click and reads only what was appended after. Baselines are committed
**because kodi.log rotates**: a restart moves it to `kodi.old.log`, and the first
POV trace we wanted to compare against was gone by the next morning.

## How to capture

Same title, same skin, same box — only the engine differs. TMDb-widget entry
point (that is how Asaf actually plays).

```bash
python tools/trace_playback.py mark        # BEFORE clicking the title
#   ... click it, wait for the source list ...
python tools/trace_playback.py show
python tools/trace_playback.py save pov    # -> docs/playback-traces/pov.json
python tools/trace_playback.py diff gears pov
```

## Baselines (Supergirl tt8814476, Zephyr, TMDb widget, 2026-08-02)

| file | engine | tmdbhelper dummy |
|---|---|---|
| `gears-dummy1.0.json` | Gears 2.3.8 | 1.0 (old default) |
| `gears.json` | Gears 2.3.8 | **0.1** (shipped, config 56) |
| `pov-dummy1.0.json` | POV 6.08.01 | 1.0 |
| `pov.json` | POV 6.08.01 | **0.1** |

### The engines behave the same

Every stage lands within ~1s of the other, and the totals match. The dummy, the
post-dummy gap, the prefetch trigger and the Hebrew-subs thread are **shared
code plus tmdbhelper**, not the engine.

The ONE real difference: **POV briefly shows the HOME screen between the dummy
ending and its scraping window opening; Gears never does** (its own sources
window stays up across the gap). Confirmed both in the trace (`ui: HOME on
screen`, POV only) and on screen by Asaf.

### What the dummy tuning bought (config 56)

| stage | 1.0 | 0.1 | saved |
|---|---|---|---|
| Gears: scrape starts | +6.4s | +4.5s | −1.9s |
| Gears: source list | +12.8s | **+9.3s** | −3.5s |
| POV: scrape starts | +7.3s | +4.2s | −3.1s |
| POV: source list | +13.2s | **+9.5s** | −3.7s |
| POV: home flash | ~2.9s | **~1s** | −1.9s |

Quote **~2s** as the deterministic saving (the dummy pair); the rest is scraper
variance. The dummy itself is still necessary — it satisfies Kodi's
`setResolvedUrl` handle while the engine opens an interactive dialog.

## Open, deliberately not fixed

The remaining **~1s home flash on POV** is POV's own plugin startup: the dummy
ends at +0.8s but POV draws nothing until its scraping window at +4.3s, so Home
is simply what is underneath. Fixing it means showing the busy/progress window
at the START of POV's play action — which is `plugin.video.pov` code, i.e.
another overlay file with a re-merge tax on every POV update.

**Decision (Asaf, 2026-08-02): leave it.** A 1s flash on a click that now
completes in 9.5s is not worth a permanent overlay. Revisit later, or raise it
upstream with kodifitzwell (their window lifecycle, and they are responsive).

Caveat for whoever re-measures: `Home.xml` is `KEEP_IN_MEMORY`, so the ABSENCE
of a "Loading skin file: Home.xml" line does not prove home was not shown — it
is only logged when actually re-loaded. Trust the screen over the log here.
