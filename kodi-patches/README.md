# Kodi source patches (custom kodi.exe / APK builds)

## pr27320-python-crash-fix.diff
Backport of upstream https://github.com/xbmc/xbmc/pull/27320
("[python] Fix random segfaults on startup / script execution / finalization",
merged to master Oct 2025 — Kodi 22 only; the Omega branch never received it).

Fixes the EXCEPTION_ACCESS_VIOLATION crashes in python3.8.dll
(PySys_SetObject+0x14 NULL thread-state / pymalloc corruption) triggered by
concurrent CPythonInvoker init/teardown — e.g. the Zephyr home widget storm
firing ~15 gears plugin calls while another invoker stops. 9 dumps analyzed,
Windows x64 + Android ARM32, one signature. Full investigation:
docs/maintainers/kodi-python-crash-investigation.md

Applies 100% clean to tag 21.3-Omega (verified with git apply --check).
Built by .github/workflows/build-kodi-win.yml; the resulting kodi.exe is
overlaid onto the OFFICIAL 21.3 package in build-inputs (only kodi.exe
changes; python3.8.dll and all other binaries stay official).
