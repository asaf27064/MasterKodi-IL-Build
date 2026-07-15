---
name: zephyr-home-performance
description: Zephyr home freeze on weak boxes = eager widget load; fix = HomeBasic (Home_Simple) lazy view
metadata: 
  node_type: memory
  type: project
  originSessionId: 3dfd12c0-e979-4f10-a359-3f2151554677
---

**FROZEN-FOREGROUND TRUE ROOT CAUSE (solved 2026-07-15, config 24):** fresh Zephyr
installs came up with BOTH `HomeMultiFlixView=true` AND `HomeMultiVertical=true`
(the skin writes its own layout bool on first boot BEFORE config applies;
merge_seed then preserves it) -> TWO home layouts render stacked: keyboard focus
lives in the Flix menu list (id 301 — background reacts to it) while the Vertical
layer draws on top, frozen. Mouse hits the top layer, so hover-focus works.
Diagnosed LIVE via JSON-RPC (TCP 9090, esenabled=true):
`XBMC.GetInfoBooleans Skin.HasSetting(...)` showed both true;
`System.CurrentControlId`=301 proved keyboard focus was fine. FIX: config_policy
zephyr settings `force_ids` on all 6 mutually-exclusive layout bools (flixview
true, rest false). Wizard fixes shipped on the way (2.4.52–54: rebuild marker
runs on the post-install skip boot; marker is skin-targeted so the old skin's
service can't consume it pre-restart; skinviewtypes hashes cleared before
buildviews; wait for script-skinshortcuts-includes.xml before the reload) are
real fresh-install bugs but were NOT this one. LESSON: for "focus/visible" bugs,
introspect the RUNNING Kodi over JSON-RPC instead of guessing from skin XML.

Older episode (2026-07-13, weak-box lag — different issue): on Arctic Zephyr the
home "foreground freezes while the background (fanart/plot) keeps moving" and
it's very laggy — on a Xiaomi MiTV (Mali-G310, ARM 32-bit) box. **NOT hardware**:
AF3 runs the SAME widgets smoothly on the same box (proven). Asaf caught this —
don't blame the device.

**Root cause:** Zephyr's default home view `Home_Multi_Vertical` instantiates
**7 widget list controls** (Includes_Home.xml), so it EAGER-loads all ~7 of the
focused category's Gears scraper widgets at once (13+ across categories as you
scroll), and re-queries them. Debug log tell: a burst of `CScriptRunner: running
add-on script The Gears('plugin://plugin.video.gears/'...)` — ~25 widget loads,
same widget 2–3×. AF3 stays smooth because its `script.skinvariables` home
lazy-loads only the focused widget. AF3 and Zephyr ship the IDENTICAL widget set
(compare `config/.../script.skinvariables/nodes/skin.arctic.fuse.3/*widgets.json`
vs Zephyr's skinshortcuts submenu DATA) — so content was never the issue, only load.

**HomeBasic was the WRONG fix (reverted, config 15).** Asaf wants **Flix**
(`homemultiflixview=true`) — and the custom home title header we built lives in the
Flix view, so switching away from Flix loses it. DO NOT change Zephyr's home view
away from Flix. Home view options live in `1080i/Includes_Home.xml`; selection in
`Home.xml` ~line 72-77 (Flix / Netflix / Vertical / Horizontal / Simple).

**Actual root of the extra load was a DIRTY menu, not Flix.** The menu bundle we
shipped was captured from Asaf's messy local install — 8 orphan empty-stub
`.DATA.xml` groups (disney/hbomax/netflix/hprkymhbaym/*bthlykh/*lpyzanr) + `.bak`
files. Cleaned it (wizard 2.4.40, bundle VERSION=2): removed orphans, let buildxml
regenerate includes (verified zero refs to removed groups). NOTE `13012` in
mainmenu is NOT junk — it's the **Power** item (label is a string-id). To re-lay a
cleaned menu over an older dirty one already on a box, `repair_skin_menu` re-lays on
bundle VERSION change (not just when broken) and first deletes the box's stale
`.DATA/.bak/.hash/.properties` so orphans don't linger. Bump VERSION when the menu
changes. Whether clean Flix is fast enough on the Mali-G310 is still TBD on-device.

**Skin settings revert on reinstall (fixed wizard 2.4.41, config 16).** Reinstalling/
switching to Zephyr RESET every custom skin setting to the skin's own defaults (Flix
lost, match% shown, profile shown, monochrome ratings, simple notifications). Cause:
Kodi resets a reinstalled skin's settings.xml, and config-apply only ran on a
config-VERSION bump — the skin-switch path (`_install_from_manifest`/`install_skin`/
`install_skin_only` in builds.py) never applied config. Fix: `BuildManager.
_apply_build_config()` force-applies the config policy after every skin install.
Our full Zephyr defaults live in `config/userdata/addon_data/skin.arctic.zephyr.
2.resurrection.mod/settings.xml`: homemultiflixview=true, Infoline.DisableMatch=true
(hide match%), DisabledProfileInfo=true, ExtendedNotification=true, Icons=colorful,
ShowClearlogo, ShowEpisodeRatings, SeriesIndicators, EnableLanguages, TMDBhCrop,
EnableShowItemCount, customrating.movies/tvshows.item01-06. Reminder: a config
settings change reaches an existing box only with a config_version bump (re-applied
by wizard-version change too, but ReloadSkin is gated on a real version bump).

**User skin-setting changes must NOT be clobbered (wizard 2.4.42, config 17).**
All skin settings.xml used `update: merge_id` ("build value wins"), and config
re-applies on every wizard update -> a user's own change got overwritten on the
next launch. Fix: new `merge_seed` mode (`_seed_settings_xml`): per `<setting id>`,
ADD only ids the user lacks, NEVER overwrite an existing value. All 4 skins
(estuary/nimbus/af3/zephyr) switched `merge_id -> merge_seed`. So routine updates
make our values the DEFAULT for anyone missing them but leave user preferences
intact. An explicit (re)install still resets to our defaults: `_apply_build_config
(skin_id)` deletes that skin's settings.xml first so the seed writes our full
defaults fresh. Modes now: replace / seed_if_absent (whole-file) / merge_id
(overwrite per id) / merge_seed (add-if-absent per id) / merge_name.

ALL `update: merge_id` files were switched to `merge_seed` (config 18): the 4 skins
PLUS guisettings.xml, tmdbhelper, magneto. Key catch: guisettings shipped
`lookandfeel.skin=skin.estuary` under merge_id — a config re-apply could RESET the
user's skin to Estuary + revert locale/timezone/screensaver. Now merge_seed (fresh
stays merge_id so a clean install still gets the Hebrew/skin/regional baseline).
Critical Gears values still enforced via the separate `gears_settings` block.

**To push a changed default to EVERYONE (wizard 2.4.44):** add the setting id to
that file's `"force_ids"` list in config_policy.json + bump config_version -> it
overwrites even user-set values (exclude_ids still win, for credentials). Remove the
id afterwards if you want user changes to stick again. So merge_seed = don't clobber
by default, force_ids = deliberate override when you actually want to push.

Also: startup update-check settle cut 15s->8s (service.py, setting `update_check_delay`,
clamped 5-60). Part of the "20s wait" was our own rapid wizard updates reloading each
boot; that stops once the version is stable.

**Frozen-foreground on a FRESH install is a view-build issue, NOT Flix load (wizard
2.4.45).** After a fresh (re)install, Zephyr/AF3 came up with the foreground frozen
(background fanart/info still updating). Cause: `Home.xml` builds the skinvariables
home views via `RunScript(script.skinvariables,action=buildviews,no_reload)` -- the
views build but the display never refreshes. Asaf's own cure was switching a home
view and back (that runs buildviews WITHOUT no_reload -> reloads). Log proof: it
cleared exactly when `action=buildviews` ran (no no_reload). It is NOT Flix being too
heavy -- switching back to Flix stayed clean. Automated: `builds._apply_build_config`
drops a `pending_view_rebuild` marker (addon_data/<wizard>/); `service.
_process_pending_view_rebuild` runs `RunScript(script.skinvariables,action=buildviews)`
once on the next boot if the active skin ships skinvariables includes, then clears
it. So a fresh install self-heals -- no manual view-switch needed. This was the SAME
"foreground frozen / background moves" symptom chased earlier as eager widget load;
the real recurring cause on fresh installs is the no_reload view build.

**Config→skin delivery gotcha:** merging settings.xml while Kodi runs is clobbered
by Kodi's exit-write from stale memory. So the wizard now `ReloadSkin()`s after a
config-version bump that changed active-skin settings (gated on real version change
via `_config_version_changed`, not every wizard update). Related:
[[wizard-dep-self-heal]] (the Zephyr menu bundle it ships is still cosmetically
dirty — stray `13012` main item + empty stub .DATA files + `-1` dup groups — worth
a cleanup pass).
