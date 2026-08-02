# Skin performance ranking — measured on the Xiaomi

The skin picker has always described Estuary as "הכי מהיר" without a
measurement behind it. This is the measurement (2026-08-02), taken on the
**weakest fleet device** — a Xiaomi MiTV (AFMU0), Kodi 21.3, POV 6.08.01 —
because a ranking only matters where the hardware struggles.

## Result

Cold boot → home rendered (`Home.xml` loaded), 3 runs per skin, **median**:

| # | skin | to HOME | runs |
|---|------|--------:|------|
| 1 | **Estuary** | **1.37s** | 1.73 / 1.36 / 1.37 |
| 2 | **Nimbus** | **1.47s** | 1.57 / 1.43 / 1.47 |
| 3 | **Arctic Zephyr** | **1.66s** | 1.76 / 1.66 / 1.66 |
| 4 | **Arctic Fuse 3** | **3.70s** | 3.86 / 3.34 / 3.70 |

Stage breakdown (medians): GUI init is identical for all (~0.4–0.5s); the whole
difference is skin XML parsing + Home construction.

Conclusions:
- **The picker's "הכי מהיר" claim on Estuary is now verified**, but the real
  story is that Estuary, Nimbus and Zephyr are within **0.3s** of each other —
  effectively one speed class.
- **AF3 is ~2.7× slower to home than everything else** and is the only skin
  where the difference is human-noticeable. On weak hardware that is the
  trade-off for its visuals; the picker's AF3 description should keep saying
  "הכי מפואר" and the numbers say the cost is ~2.3 seconds per boot.

## Method (tools/skin_bench.py)

- Every number comes from **kodi.log's own millisecond timestamps** — no
  stopwatch, no screenshots.
- The skin is selected by rewriting `guisettings.xml` **while Kodi is
  stopped**, so every run is a genuine cold boot straight into the target skin:
  no switch dialog, no ReloadSkin, no warm caches from the previous skin.
- The log is truncated before each run; markers are matched from a clean file.
- **Every run verifies which skin Kodi ACTUALLY loaded** from the logged skin
  path. This guard exists because the first attempt produced a plausible-looking
  1.37s for AF3 — Kodi had silently fallen back to Estuary (the skins were on
  disk but DISABLED in Addons33.db; the wizard's kept-skin neutralisation plus a
  cancelled switch prompt leaves them that way). Never trust the setting; read
  what loaded.

### Android/adb hardening the tool carries (each earned by a real failure)

1. Wi-Fi adb drops mid-run → every call reconnects and retries; empty output is
   never trusted without a live round-trip.
2. The LOCAL adb daemon can wedge ("protocol fault") while the device is fine →
   the last retry tier restarts the adb server itself.
3. `adb shell` returns the REMOTE command's exit code — `pidof` with no match
   exits 1. Judging connectivity by exit code made a correctly-stopped Kodi look
   like a dead link; only adb's own transport errors on stderr count.

Raw data: `docs/skin-performance-xiaomi.json`.

## Re-running

```bash
python tools/skin_bench.py --device <ip>:5555 --runs 3 --out docs/skin-performance-xiaomi.json
```

The box must have the skins installed AND enabled (Addons.SetAddonEnabled via
JSON-RPC if needed). The tool disables nothing and restores nothing — wake/
screensaver handling and post-run restoration are the operator's job.
