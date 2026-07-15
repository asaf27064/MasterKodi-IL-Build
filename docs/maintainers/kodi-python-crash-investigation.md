# Kodi 21.3 embedded-Python crash — full investigation (2026-07-15)

## Status: OPEN — root cause proven, mitigation NOT shipped (skin-side attempts reverted)

## Symptom
Kodi dies instantly (no error dialog). `kodi.log` ends ABRUPTLY, no traceback —
almost always with `Loading skin file: Home.xml` as the last line. Reproduced on
**Windows** (repeatedly, ~minutes apart under navigation) and **Android** (MiTV,
after an episode ended → back → home). **Only on Arctic Zephyr** — Estuary, AF3
and Nimbus never crashed on the same box, same gears, same widgets.

Reliable repro (Windows, Zephyr): browse into a gears item, navigate back to the
home screen with widgets, repeat; also rapid view-setting (`set views`) sessions.

## Dump evidence (5 Windows minidumps, 2026-07-15)
All `EXCEPTION_ACCESS_VIOLATION` inside **python3.8.dll** (Kodi's bundled CPython):

| Time | Nearest export + offset | Meaning |
|---|---|---|
| 13:17, 13:28 | `PySys_SetObject+0x14` (read at null+0x10) | `tstate->interp` deref with **NULL thread state** — a script starting against a dead/absent interpreter state |
| 13:33, 13:52 | `_PyDict_SizeOf+0x19d1` | dict/GC internals — corrupted interpreter state |
| 13:37 | `_Py_GetAllocatedBlocks+0x436` | pymalloc allocator — heap corruption |

Analysis method: `pip install minidump pefile`; parse dump exception record
(ignore the non-fatal PEB parse error), map the crash address to module+offset,
then nearest-export symbolication against the *same* `python3.8.dll` Kodi loads
(`C:\MasterKodi IL\python3.8.dll`). Dumps land next to kodi.exe as
`kodi_crashlog-*.dmp`.

## The smoking-gun debug log (loglevel 1)
At `Window Init (Home.xml)` (13:52:35, all within ~1 ms):
1. **13 `CDirectoryProvider[plugin://plugin.video.gears/...]: refreshing..`** —
   every home widget provider refreshes on EVERY Home window init (Kodi refreshes
   directory providers on window init; Home is KEEP_IN_MEMORY but providers still
   refresh).
2. Three `CScriptRunner` gears invocations start simultaneously; one **reuses**
   LanguageInvokerThread 188, three brand-new python engines (invokers 189/190/191)
   initialize on different threads in the same millisecond.
3. `Python interpreter stopped` / invoker-thread terminating (the finished 188)
   **while invoker 189 is "instantiating addon"** → crash.

Conclusion: **CPythonInvoker start/teardown race under concurrent engine
init+shutdown** — a Kodi 21 core bug (python3.8 subinterpreter lifecycle).
Zephyr triggers it because its home fires a widget-provider storm on every entry,
overlapping the teardown of the plugin window's aborted gears call.

## Why only Zephyr
Zephyr's home refreshes ALL widget providers on every Home init (13 providers on
our menu) AND (pre-guard) also ran two `RunScript`s (skinshortcuts buildxml +
skinvariables buildviews) on every Home entry. AF3/Nimbus home widgets don't
re-storm per entry the same way.

## What was tried (chronological) and results
1. **wizard 2.4.52–2.4.56 service fixes** — real bugs fixed on the way (marker on
   skip-boot, skin-targeted marker, hash-clear before buildviews, wait for
   skinshortcuts includes, skin-removal on the deferred boot). SHIPPED and KEPT.
   Did not stop the crash (they weren't its cause).
2. **Once-per-session guard on Zephyr Home.xml onload RunScripts**
   (`MK_OnloadBuilt` window property) — held correctly (log-verified: builds ran
   once) but crash persisted → the RunScript churn was a contributor, not the core.
   REVERTED with the rest.
3. **`reuselanguageinvoker=false` on gears** — crash persisted (13:37 dump), plus
   slows every gears call. REVERTED. The race is engine init/teardown, not reuse.
4. **Debug logging (`<loglevel hide="true">1</loglevel>`)** — did NOT mask the
   race (crashed with it on), which is how we captured the smoking gun.
5. **Staggered widget arming (the big skin surgery)** — gate every spotlight
   provider `<content>` behind `Window(Home).Property(MK_Arm_<group>)`, armed in
   waves from Home.xml onload, cleared on unload. ABANDONED after multiple
   iterations; findings for whoever retries:
   - The single provider `<content>` line lives in
     `1080i/Includes_Object.xml` → `Object_Widget_Spotlight` (line ~437). Gating it
     with `$INFO[Window(Home).Property(MK_Arm_$PARAM[armgroup]),$PARAM[content]&mk_arm=,]`
     is syntactically fine (include params substitute inside $INFO in content).
   - **The Flix home's real widget providers are emitted by the `vertical-submenu`
     (26 calls) and `vertical-main` (2) template blocks — NOT `spotlight-submenu`
     (4 calls, empty content, group '5')**. Flix reuses the vertical templates.
   - skinshortcuts template engine (script.skinshortcuts 2.0.3 + simpleeval):
     `$SKINSHORTCUTS[prop]` substitution works in `<param value>`; `$PYTHON[...]`
     evals with item properties as names — a property missing from the rules
     (e.g. widgetPath in a block that doesn't declare it) breaks emission
     silently/partially. A bare `<property name="X" />` fallback line CLOBBERS the
     lookup rule (order-sensitive). `vertical-submenu` does NOT declare
     `submenuVisibility`; it must be added (`tag="property" attribute="name|group"`
     + `noMenu` fallback) for group substitution there.
   - Even after wiring vertical blocks + rules, widgets did not render on-device;
     reverted per Asaf's call rather than iterate further on the opaque engine.
     (Unverified suspicion: compiled include still not re-read, or the $INFO
     content gate misbehaving inside the vertical emission path — next attempt
     should verify the COMPILED `script-skinshortcuts-includes.xml` after each
     boot: count `armgroup` params and check `<content>` text.)
   - Menu recompile: delete
     `userdata/addon_data/script.skinshortcuts/<skinid>.hash` (template changes
     are NOT covered by the hash) — rebuild happens on next boot's buildxml, and
     the DISPLAY only picks the new includes on the boot after that (2 boots).
   - Hub windows (Custom_1131/32/33/34) share `Object_Widget_Spotlight` — any
     gate must default to an always-armed group (`MK_Arm_always`, set at Home
     onload, never cleared) or hubs break when Home deinit clears properties.

## Current state (after full revert, 2026-07-15)
- Zephyr skin: 100% pristine overlay state (template/Home/Includes_Object all
  reconstruction-verified). Menu hash cleared → pristine rebuild.
- Wizard 2.4.56 + config 25 remain shipped (good fixes regardless).
- Debug logging removed from the live box; gears reuselanguageinvoker=true.
- **The crash remains reproducible on Zephyr.** Known trigger, no shipped fix.

## Untried options (ranked)
1. **Newer Kodi build** — check whether a later 21.x fixed the invoker race
   (Kodi PRs around "python invoker" / "crash on script shutdown"); we bundle our
   own Kodi in the installers, so a base bump is in our control. Most likely REAL fix.
2. Retry the stagger with compiled-file verification at each step (see notes above).
3. Reduce Zephyr widget count / switch its home to a lazy layout (HomeBasic-style).
4. Live with it: Zephyr stays available but flagged "may crash on heavy navigation".
