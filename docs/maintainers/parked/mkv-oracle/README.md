# MKV subtitle sync-oracle (validated prototype, 2026-07-13)

Pure-Python Matroska probe that samples an embedded TEXT subtitle track's cue
timestamps via HTTP Range reads — no ffmpeg, no full download. The sampled cues
are a perfect timing reference ("oracle") for the exact video being streamed, used
to time-align an external Hebrew subtitle of uncertain timing.

## Feasibility: PROVEN
Tested live against a TorBox 4K stream (Project Hail Mary, 29.6 GB, 25 Mbps).

- `mkv_probe.parse(url)` correctly parsed EBML/Matroska, found 35 text subtitle
  tracks (SubRip / S_TEXT/UTF8), read the Info/Tracks/Cues index + a few clusters.
- Extracted clean, correctly-timed cues at the requested sample points (e.g. 25 /
  50 / 75 % of duration → 00:39:07, 01:18, 01:57). Hebrew and English SDH both
  parsed fine.
- Cost: **~8 MB per cue on this 25 Mbps 4K file** (each Range window spans only a
  few seconds of a high-bitrate stream). Hebrew 57.6 MB→7 cues; Eng-SDH 39.6 MB→5.

## Design conclusion (what to build for production)
Do NOT collect the 12+ dense events `sync_align.estimate_offset` wants (~120 MB).
Instead sample **5–8 well-spread anchors** (they span the whole film) and fit a
linear map `t_true = a*t_sub + b` against the external Hebrew sub's nearest cues —
2 anchors suffice for offset+scale, 5–8 give robustness. Budget ~40 MB on 4K,
~15–20 MB on 1080p. Hard-cap the byte budget; over budget → return None
(fail-open to the existing text-based `resync.retime`, then no-sync).

Production TODO when we pick this up:
1. Incremental reader with per-window early-stop (stop once K cues collected;
   don't always pull the full `per_window_bytes`).
2. Prefer the DENSEST/English-SDH track as the oracle (more anchors per MB); it's
   only timings we need, not the text.
3. New matcher: pair oracle anchors ↔ external-Hebrew cues by proximity, least-
   squares fit (a,b), accept only if residuals small + a≈1.0±small.
4. Wire as a fail-open oracle in `ai_bridge._pool_retime` AFTER text `resync` and
   the existing `sync_align.align`, behind an OPT-IN setting (off by default).
   Needs the playing file URL (`xbmc.Player().getPlayingFile()`).
5. Cross-platform: verify Range reads + the CERT_NONE SSL context work on Android
   (TorBox CDN cert read as expired to desktop Python — hence CERT_NONE; confirm
   Kodi/Android behavior). Test a 1080p x265 release and a release with English
   SDH but NO embedded Hebrew (the real target case).

## Files
- `mkv_probe.py` — the validated probe (no secrets). Drop into
  `service.subtitles.gearsai/resources/aisubs/` when productionizing.

Related existing pieces: `sync_align.py` (timestamp offset aligner),
`resync.py` (text-based English-A↔English-B aligner), `match.py` (release scoring).
See [[moran-wizard-reference]] — Moran's `mkv_probe.py` is the prior art.
