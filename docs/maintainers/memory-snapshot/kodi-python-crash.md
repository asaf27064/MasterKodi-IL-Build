---
name: kodi-python-crash
description: Task #10 root cause — Kodi 21.3 embedded python3.8.dll ACCESS_VIOLATION under concurrent script load (dump-proven); mitigation not yet built
metadata:
  type: project
  originSessionId: 3dfd12c0-e979-4f10-a359-3f2151554677
---

**Task #10 (Kodi hangs/crashes) has a dump-proven root cause (2026-07-15):**
`EXCEPTION_ACCESS_VIOLATION` (null+16 read) inside **python3.8.dll** at offset
`0x1c6744` — Kodi 21.3's embedded Python engine, NOT the skin and NOT our code.
Windows minidump: `C:\MasterKodi IL\portable_data\kodi_crashlog-21.3
Git_20251031-a3a448d26b-20260715-131746.dmp` (parse with `py -m pip install
minidump`; the PEB parse error is non-fatal, exception record still reads).

**Two confirmed occurrences, same signature:** kodi.log ends ABRUPTLY at
"Loading skin file: Home.xml" with no error/traceback.
1. Android (MiTV, 2026-07-15 01:17): finished a Tracker episode -> gears
   auto-next opened sources window -> back/mark-watched -> home load -> dead.
2. Windows (2026-07-15 13:17): mid rapid set-views on Zephyr (repeated
   skinvariables invocations) -> home load -> dead.

**Why home load:** that's when several Python scripts fire at once
(script.skinshortcuts buildxml + script.skinvariables + gears widgets with
reuselanguageinvoker=true). The crash is a race in Kodi's Python invoker layer
under concurrent/rapid invocations — a known-fragile area of Kodi 21 embedded
python3.8.

**Rule out:** our filecache change (was never active), skin XML (loads fine),
the frozen-home layout bug (separate, fixed in config 24).

**FULL INVESTIGATION 2026-07-15 (5 dumps + debug-log smoking gun) — see the
COMPLETE writeup committed at `docs/maintainers/kodi-python-crash-investigation.md`
in MasterKodi-IL-Build (commit cd29cf3).** Key facts: crash sites =
PySys_SetObject+0x14 (NULL tstate), _PyDict_SizeOf area, _Py_GetAllocatedBlocks
(pymalloc) — CPythonInvoker engine init/teardown race. Trigger: ZEPHYR ONLY —
its home refreshes ALL ~13 gears widget CDirectoryProviders on EVERY Home window
init, overlapping the teardown of the aborted plugin-window invoker.
TRIED & FAILED: reuselanguageinvoker=false (crash persisted, reverted);
once-per-session guard on Home onload RunScripts (held, insufficient, reverted);
staggered provider arming via MK_Arm_<group> window properties (multiple
iterations, widgets stopped rendering, ABANDONED + fully reverted to pristine
overlay per Asaf). Template-engine gotchas (vertical-submenu emits the real 28
providers, NOT spotlight-submenu; bare <property/> fallback clobbers lookups;
$PYTHON breaks on undeclared names; menu .hash doesn't cover template changes;
2 boots needed per iteration) — all in the repo doc.
UNTRIED (ranked): (1) newer Kodi build with invoker fixes — likely the real fix
(we bundle Kodi in the installers); (2) retry stagger WITH per-boot compiled-file
verification; (3) reduce/lazy Zephyr widgets. STATUS: crash remains reproducible
on Zephyr; wizard 2.4.52-56 fixes shipped and kept; Asaf chose to park it and
continue the Zephyr SETTINGS review.
