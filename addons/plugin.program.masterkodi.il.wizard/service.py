# -*- coding: utf-8 -*-
"""
MasterKodi IL Wizard - Service

On startup: sweeps stale *_old_<ts> backup dirs, then runs the manifest-driven
update check (modular_update). Every addon -- Gears + its Hebrew overlay, the AI
subs, the skins, and the wizard itself -- is delivered pre-merged from the
MasterKodi-IL-Build manifest, each verified by sha256 before install. There is no
separate "re-apply Hebrew after an update" step anymore: the Hebrew is baked into
what we ship, so the old overlay-reinstall machinery (onNotification reinstalls,
per-addon raw-URL checks, wizard self-update) has been removed.
"""
import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs
import os
import re
import shutil

# Skip service on first run - let firstrun handle the wizard launch
MARKER_FILE = '.masterkodi_il_done'


def _marker_exists():
    home = xbmcvfs.translatePath('special://home/')
    return os.path.exists(os.path.join(home, MARKER_FILE))


if not _marker_exists():
    xbmc.log('[plugin.program.masterkodi.il.wizard] No marker yet, skipping wizard startup service (firstrun will handle launch)', xbmc.LOGINFO)
    raise SystemExit

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo('id')
ADDON_NAME = ADDON.getAddonInfo('name')
ADDONS_PATH = xbmcvfs.translatePath('special://home/addons/')


def log(msg, level=xbmc.LOGINFO):
    xbmc.log(f'[{ADDON_ID}] {msg}', level)


log("Service loading...")


def _strip_pov_settings_comments():
    """Remove XML comments from POV's live settings.xml -- they CRASH Kodi.

    Kodi's addon-settings reader dies natively on a comment node in
    addon_data/<addon>/settings.xml: no traceback, the log just stops mid
    startup. Config 59 shipped a three-line explanatory comment above
    skip_intro.enable in all six POV variants, which crash-looped a box on a
    FRESH INSTALL (Asaf, 2026-08-10 -- he identified the settings as the cause;
    reproduced deterministically by injecting the comment and booting).

    Config 60 ships the corrected files, but a box already holding config 59
    would crash before anything could repair it -- so this runs FIRST, at
    import time, before POV's own service reads the file (the wizard service
    starts ~90ms ahead of POV's). Pure stdlib, no imports beyond what is
    already loaded, wrapped so it can never itself break startup.
    """
    try:
        path = xbmcvfs.translatePath(
            'special://profile/addon_data/plugin.video.pov/settings.xml')
        if not os.path.isfile(path):
            return
        with open(path, encoding='utf-8', errors='replace') as fh:
            txt = fh.read()
        if '<!--' not in txt:
            return
        cleaned = re.sub(r'[^\S\n]*<!--.*?-->[^\S\n]*\n?', '', txt, flags=re.S)
        with open(path, 'w', encoding='utf-8', newline='') as fh:
            fh.write(cleaned)
        log('removed XML comment(s) from POV settings.xml (Kodi crashes on them)')
    except Exception as e:
        log('pov settings comment strip failed: %s' % e, xbmc.LOGWARNING)


_strip_pov_settings_comments()


def _cleanup_old_addon_dirs():
    """Remove stale '<id>_old_<timestamp>' backup folders left by past updates.

    Kodi tries to parse every folder under addons/ as an add-on (log spam +
    clutter). We sweep them on every startup -- safe because the suffix pattern
    is specific (an addon id never ends in _old_<digits>).
    """
    try:
        pat = re.compile(r'_old_\d+$')
        if not os.path.isdir(ADDONS_PATH):
            return
        for name in os.listdir(ADDONS_PATH):
            if not pat.search(name):
                continue
            p = os.path.join(ADDONS_PATH, name)
            if os.path.isdir(p):
                try:
                    shutil.rmtree(p)
                    log(f"Cleaned stale backup dir: {name}")
                except Exception as e:
                    log(f"Could not remove {name}: {e}", xbmc.LOGWARNING)
    except Exception as e:
        log(f"_cleanup_old_addon_dirs error: {e}", xbmc.LOGWARNING)


def get_addon_version(addon_id):
    """Get an addon's version from its addon.xml, or None."""
    try:
        addon_xml = os.path.join(ADDONS_PATH, addon_id, 'addon.xml')
        if os.path.exists(addon_xml):
            with open(addon_xml, 'r', encoding='utf-8') as f:
                match = re.search(r'<addon[^>]*version="([^"]+)"', f.read())
                if match:
                    return match.group(1)
    except Exception:
        pass
    return None


def _process_pending_view_rebuild():
    """First boot after a skin (re)install: Zephyr/AF3 build their skinvariables
    home views on Home load with `no_reload`, so on a fresh switch the views build
    but the DISPLAY never refreshes -- the foreground stays showing the pre-build
    state (looks frozen) while the background updates, until the user manually
    switches a view. Do that clean rebuild ONCE ourselves (buildviews without
    no_reload reloads the skin), so a fresh install comes up right."""
    marker = os.path.join(xbmcvfs.translatePath('special://userdata/addon_data/'),
                          ADDON_ID, 'pending_view_rebuild')
    if not os.path.isfile(marker):
        return
    try:
        target = open(marker, encoding='utf-8').read().strip()
    except Exception:
        target = ''
    cur_skin = xbmc.getSkinDir() or ''
    # The marker names the skin it was written FOR. During a skin install the
    # service of the STILL-RUNNING old skin can reach this point before the
    # restart -- consuming the marker on the wrong skin left the new skin's
    # first boot without its rebuild (the Zephyr frozen-home regression).
    if target and target.startswith('skin.') and target != cur_skin:
        # If the marker's skin is GONE from disk (user removed/switched away
        # for good), waiting is pointless and a months-later switch-back would
        # apply a stale stash over newer settings -- drop marker + stash now.
        if not os.path.isfile(os.path.join(ADDONS_PATH, target, 'addon.xml')):
            log("post-install rebuild dropped: marker skin %s no longer installed" % target)
            base_ = xbmcvfs.translatePath('special://userdata/addon_data/')
            shutil.rmtree(os.path.join(base_, ADDON_ID, 'pending_skin_config'),
                          ignore_errors=True)
            for f in ('pending_view_rebuild', 'pending_view_rebuild_force'):
                try:
                    os.remove(os.path.join(base_, ADDON_ID, f))
                except Exception:
                    pass
            return
        log("post-install rebuild deferred: marker is for %s, active skin is %s"
            % (target, cur_skin))
        return
    # NOTE: the marker is removed at the END (or on timeout) -- consuming it
    # up front meant an abort mid-wait (user quits during first boot) lost
    # the rebuild forever and the widgets stayed dead on every later boot.
    try:
        skin = cur_skin
        # Deferred skin-visual config (armed by _maybe_apply_config): re-apply
        # the stashed active-skin settings BEFORE the rebuild+reload below.
        # Mid-session apply gets clobbered by Kodi's exit-save; boot-time apply
        # + the single reload below is the safe path.
        stash_applied = False
        force_hash_clear = False
        base = xbmcvfs.translatePath('special://userdata/addon_data/')
        try:
            fflag = os.path.join(base, ADDON_ID, 'pending_view_rebuild_force')
            if os.path.isfile(fflag):
                force_hash_clear = True
                os.remove(fflag)
            sdir = os.path.join(base, ADDON_ID, 'pending_skin_config')
            sfile = os.path.join(sdir, 'settings.xml')
            tfile = os.path.join(sdir, 'target.txt')
            if os.path.isfile(sfile) and os.path.isfile(tfile):
                target_path = open(tfile, encoding='utf-8').read().strip()
                # the stash was captured from the skin ACTIVE at defer time --
                # after a skin switch that's NOT the running skin. Pushing it
                # into the live skin injected 175 Zephyr ids into AF3's
                # settings (harmless junk, but junk). Apply in-memory ONLY
                # when the stash belongs to the CURRENT skin; the file copy
                # below still lands it for the skin it belongs to.
                stash_is_for_cur = ('/%s/' % cur_skin) in target_path.replace('\\', '/')
                if target_path:
                    shutil.copy2(sfile, target_path)
                    # CRITICAL (learned from the Xiaomi, 2026-07-17): the file
                    # copy alone is NOT enough for the ACTIVE skin. Kodi saves
                    # the running skin's in-memory settings back to settings.xml
                    # during skin UNLOAD -- so the very ReloadSkin/buildviews
                    # reload below overwrites the stash with the old values a
                    # moment before re-reading them, silently reverting the
                    # config. Push the stashed values into the LIVE skin via
                    # Skin.* builtins: then unload-save writes OUR values and
                    # the reload compiles against them.
                    applied_n = 0
                    try:
                        import xml.etree.ElementTree as ET
                        for s in (ET.parse(sfile).getroot().findall('setting')
                                  if stash_is_for_cur else []):
                            sid = s.get('id')
                            if not sid:
                                continue
                            val = (s.text or '').strip()
                            if val == 'true':
                                xbmc.executebuiltin('Skin.SetBool(%s)' % sid)
                            elif val == 'false':
                                xbmc.executebuiltin('Skin.Reset(%s)' % sid)
                            else:
                                # quoted so commas in values don't split params
                                xbmc.executebuiltin('Skin.SetString(%s,"%s")'
                                                    % (sid, val.replace('"', '')))
                            applied_n += 1
                    except Exception as e:
                        log(f"in-memory skin settings apply failed: {e}", xbmc.LOGWARNING)
                    stash_applied = True
                    log("applied deferred skin settings from config stash "
                        "(%d pushed into live skin)" % applied_n)
                shutil.rmtree(sdir, ignore_errors=True)
        except Exception as e:
            log(f"config stash apply failed: {e}", xbmc.LOGWARNING)
        # Only skins that actually DEPEND on script.skinshortcuts (Zephyr)
        # compile their menu into script-skinshortcuts-includes.xml on first
        # Home load. A folder named shortcuts/ is NOT the signal -- AF3 has one
        # too (skinvariables templates) and would stall the full timeout here.
        uses_ss = False
        try:
            with open(os.path.join(ADDONS_PATH, skin, 'addon.xml'), encoding='utf-8') as fh:
                uses_ss = 'script.skinshortcuts' in fh.read()
        except Exception:
            pass
        ss_inc = os.path.join(ADDONS_PATH, skin, '1080i', 'script-skinshortcuts-includes.xml')
        if uses_ss:
            # Until that file exists AND a reload happens, the foreground
            # (hero/menu) is dead while the background moves. Wait for the
            # build so OUR reload below brings everything up at once.
            # CRITICAL: existence is NOT enough -- skinshortcuts writes the
            # file in place over several seconds (ElementTree tree.write, no
            # temp+rename), and reloading mid-write makes Kodi parse a
            # truncated include table (menu shows, widgets dead) with nothing
            # left to reload again. ElementTree emits the root closing tag
            # LAST, so `</includes>` on disk == document complete -- that one
            # check is both sufficient and the fastest possible signal.
            mon = xbmc.Monitor()
            waited = 0
            done = False
            while not done and waited < 90 and not mon.abortRequested():
                try:
                    with open(ss_inc, 'rb') as fh:
                        fh.seek(max(0, os.path.getsize(ss_inc) - 64))
                        done = b'</includes>' in fh.read()
                except Exception:
                    done = False
                if not done:
                    # abort (user quitting) keeps the marker -> retried next boot
                    if mon.waitForAbort(1):
                        return
                    waited += 1
            log("post-install: skinshortcuts includes %s after %ss"
                % ('complete' if done else 'STILL MISSING', waited))
            if not done:
                # something is genuinely wrong with the skin's menu build;
                # reloading a truncated/missing include table IS the bug we
                # are here to prevent. Give up (marker removed below) rather
                # than stall every future boot for 90s. BUT: if we already
                # copied deferred settings onto disk, they only survive Kodi's
                # exit-save if the skin re-reads them -- without this reload
                # the change is silently lost forever (__config__ is already
                # bumped, so it would never retry).
                if stash_applied:
                    xbmc.sleep(1000)
                    xbmc.executebuiltin('ReloadSkin()')
                try:
                    os.remove(marker)
                except Exception:
                    pass
                return
        inc = os.path.join(ADDONS_PATH, skin, '1080i', 'script-skinvariables-includes.xml')
        if skin and os.path.isfile(inc):
            # buildtemplate (force) recompiles the menu/shortcut includes from the
            # skinvariables nodes we deliver via config (e.g. custom home categories);
            # without it, edited node JSONs never reach the skin. buildviews rebuilds
            # the view-type includes. Both needed for a config-driven menu change.
            gen = os.path.join(ADDONS_PATH, skin, '1080i',
                               'script-skinvariables-generator-includes-.xml')
            if os.path.isfile(gen):
                # NOT forced: the generator hashes the node contents, so this
                # no-ops (no reload, no splash) when the skin's own first-boot
                # build already compiled everything -- AF3 self-builds on a
                # fresh install -- and only really rebuilds when a delivered
                # node change wasn't compiled yet. no_reload keeps it silent;
                # the buildviews after it does the single visible reload only
                # when views actually changed.
                log("post-install: rebuilding skin menu templates (buildtemplate,no_reload)")
                xbmc.executebuiltin('RunScript(script.skinvariables,action=buildtemplate,no_reload=true)')
                xbmc.Monitor().waitForAbort(3)   # let the template write finish before buildviews
            # buildviews hash-skips (silently, no reload) unless the stored
            # skinviewtypes hashes are cleared. Clear them for skinshortcuts-
            # driven skins (Zephyr) whose display needs the forced rebuild +
            # reload -- and ALSO whenever the marker came from a config-driven
            # viewtypes change (force flag): the hash covers only the SKIN's
            # json, so a new config-delivered userdata viewtypes.json never
            # triggers a rebuild on its own (the 'views not applied via update'
            # bug on the Xiaomi).
            if uses_ss or force_hash_clear:
                xbmc.executebuiltin('Skin.SetString(script-skinviewtypes-hash,)')
                xbmc.executebuiltin('Skin.SetString(script-skinviewtypes-checksum,)')
            log("post-install: rebuilding skin views (buildviews)")
            # no_reload=true + explicit reload BELOW. Relying on buildviews'
            # own reload raced with the skin's first-boot self-build (fresh
            # install, Windows 2026-07-18): the skin re-set the hashes between
            # our clear and buildviews' check, buildviews early-returned
            # WITHOUT reloading, and the home stayed frozen (menu loaded from
            # a pre-viewtypes state, widgets dead). One reload, always ours.
            xbmc.executebuiltin('RunScript(script.skinvariables,action=buildviews,no_reload=true)')
            # wait for the viewtypes include write to settle (spawn + compile
            # take a few seconds; file may also already be complete)
            vt_inc = os.path.join(ADDONS_PATH, skin, '1080i',
                                  'script-skinviewtypes-includes.xml')
            mon2 = xbmc.Monitor()
            if not mon2.waitForAbort(3):
                last = -1
                stable = 0
                waited2 = 0
                while waited2 < 20 and not mon2.abortRequested():
                    try:
                        cur = os.path.getsize(vt_inc)
                    except Exception:
                        cur = -1
                    if cur > 0 and cur == last:
                        stable += 1
                        if stable >= 2:      # unchanged for 2s -> write done
                            break
                    else:
                        stable = 0
                    last = cur
                    if mon2.waitForAbort(1):
                        return
                    waited2 += 1
            log("post-install: views compiled, reloading skin")
            xbmc.executebuiltin('ReloadSkin()')
        elif stash_applied:
            # non-skinvariables skin (Estuary/Nimbus): nothing to rebuild, but
            # the deferred settings need ONE boot-time reload to take effect
            xbmc.sleep(1000)
            xbmc.executebuiltin('ReloadSkin()')
    except Exception as e:
        log(f"post-install view rebuild failed: {e}", xbmc.LOGWARNING)
        # same rescue as the timeout path: applied-but-never-reloaded settings
        # get reverted by Kodi's exit-save -- reload so they stick
        try:
            if stash_applied:
                xbmc.sleep(1000)
                xbmc.executebuiltin('ReloadSkin()')
        except Exception:
            pass
    try:
        os.remove(marker)
    except Exception:
        pass


def _pending_removal_marker():
    """Path of the marker the skins menu drops when the user asks for the
    previous skin to be removed."""
    return os.path.join(xbmcvfs.translatePath('special://userdata/addon_data/'),
                        ADDON_ID, 'pending_skin_removal')


def _pending_setup_marker():
    """Path of the marker install_skin drops when a newly-installed skin has its
    own setup wizard to run."""
    return os.path.join(xbmcvfs.translatePath('special://userdata/addon_data/'),
                        ADDON_ID, 'pending_skin_setup')


def _process_pending_skin_setup():
    """Run a newly-installed skin's OWN setup wizard.

    The skin only starts it from its startup window, and only while both of its
    gates are empty -- and those get stamped by the first Home load, so a
    reinstall (Kodi keeps addon_data) or an Android live switch never shows it
    again. We drive the skin's own documented entry point instead."""
    marker = _pending_setup_marker()
    if not os.path.isfile(marker):
        return
    try:
        sid = open(marker, encoding='utf-8').read().strip()
    except Exception:
        sid = ''
    if not sid:
        try:
            os.remove(marker)
        except Exception:
            pass
        return
    if xbmc.getSkinDir() != sid:
        return                      # not the active skin yet -- try again later
    if not xbmc.getCondVisibility('Window.IsVisible(home)'):
        return                      # the properties it sets live on Home
    try:
        from resources.libs.builds import _launch_skin_setup, SKIN_SETUP_WIZARD
    except Exception as e:
        log(f"skin setup import failed: {e}", xbmc.LOGWARNING)
        return
    if sid not in SKIN_SETUP_WIZARD:
        try:
            os.remove(marker)
        except Exception:
            pass
        return
    if not _launch_skin_setup(sid):
        return                      # already on screen -- keep the marker, retry
    try:
        os.remove(marker)
    except Exception:
        pass


def _run_pending_skin_setup_when_home(monitor, timeout=45):
    """Wait (briefly) for Home, then run a pending skin setup.

    Called early on every boot path. The setup wizard has to land within a
    few seconds of Home appearing: reaching it from the idle loop instead
    took 108s on the live box, which drops the setup on a user who has
    already started navigating. Returns at once when nothing is pending, so
    a normal boot pays one os.path.isfile."""
    if not os.path.isfile(_pending_setup_marker()):
        return
    waited = 0
    while waited < timeout and not monitor.abortRequested():
        if xbmc.getCondVisibility('Window.IsVisible(home)'):
            _process_pending_skin_setup()
            return
        if monitor.waitForAbort(1):
            return
        waited += 1
    log('skin setup: Home never appeared in %ss; leaving it pending' % timeout,
        xbmc.LOGWARNING)


def _process_pending_skin_removal():
    """Uninstall the skin the user dropped during a skin switch. Deferred from
    the skins menu to now (the old skin is no longer the running one)."""
    marker = _pending_removal_marker()
    if not os.path.isfile(marker):
        return
    try:
        sid = open(marker, encoding='utf-8').read().strip()
    except Exception:
        sid = ''
    if not sid:
        try:
            os.remove(marker)
        except Exception:
            pass
        return
    if xbmc.getSkinDir() == sid:
        # Somehow still the active skin -> KEEP the marker so the removal is
        # retried on a later boot instead of being silently lost.
        log(f"pending skin removal deferred: {sid} is still the active skin")
        return
    # Attempt the removal BEFORE dropping the marker. The old order deleted the
    # marker first, so a removal that failed (or returned False) was never
    # retried on a later boot and nothing was logged -- the skin simply stayed
    # on disk with no trace of why (Asaf, 2026-08-29).
    try:
        from resources.libs.builds import BuildManager
        ok = BuildManager().remove_skin(sid)
    except Exception as e:
        log(f"pending skin removal failed for {sid}: {e}", xbmc.LOGWARNING)
        ok = False
    if ok:
        log(f"Removed previous skin after switch: {sid}")
        try:
            os.remove(marker)
        except Exception:
            pass
    else:
        # keep the marker so the next boot tries again
        log(f"pending skin removal for {sid} did not succeed; will retry next boot",
            xbmc.LOGWARNING)


def _prewarm_gears(mon):
    """Warm Gears so the FIRST home-widget/shortcut click is fast. The first
    plugin call pays a cold start (python imports of the whole gears stack +
    TMDB/Trakt list fetch); gears has reuselanguageinvoker so every later call
    reuses the warm interpreter. We pay that cost here silently instead of on
    the user's first click. Headless via JSON-RPC Files.GetDirectory (no
    window opens). Fail-open: any error -> do nothing."""
    try:
        # Content-aware: POV is a Gears fork with the SAME modes/actions and the
        # same reuselanguageinvoker, so it pays the identical cold start. Warming
        # whichever engine is actually installed means a POV box gets the fast
        # first click too (it used to get none -- this returned immediately).
        if xbmc.getCondVisibility('System.HasAddon(plugin.video.gears)'):
            engine = 'plugin.video.gears'
        elif xbmc.getCondVisibility('System.HasAddon(plugin.video.pov)'):
            engine = 'plugin.video.pov'
        else:
            return
        # Apply the view map BEFORE the prewarm too: gears caches its settings
        # in the warm interpreter on first touch, so writing the db only AFTER
        # priming left the whole session on the OLD views (config-delivered
        # view change looked like a no-op until the next restart). The post-
        # prewarm apply below still covers the fresh-install case where the
        # db is only created by the prewarm itself.
        try:
            if engine == 'plugin.video.gears':
                from resources.libs import modular_update as _mu
                _mu.apply_gears_views_for_skin()
        except Exception:
            pass
        paths = (
            'plugin://%s/?name=Trending&mode=build_movie_list'
            '&action=trakt_movies_trending&random_support=true&iconImage=trending' % engine,
            'plugin://%s/?name=Trending&mode=build_tvshow_list'
            '&action=trakt_tv_trending&random_support=true&iconImage=trending' % engine,
        )
        for p in paths:
            if mon.abortRequested():
                return
            try:
                xbmc.executeJSONRPC(
                    '{"jsonrpc":"2.0","id":1,"method":"Files.GetDirectory",'
                    '"params":{"directory":"%s","media":"video",'
                    '"properties":["title"],"limits":{"start":0,"end":3}}}' % p)
            except Exception:
                pass
        log("%s pre-warm done" % engine)
        # The pre-warm is what CREATES gears' settings.db on a fresh install
        # (gears fills every setting with defaults on first run). The install-
        # time apply_gears_views_for_skin() no-oped back then because the db
        # didn't exist yet -- re-apply now that it does, so a fresh box's first
        # browse already uses the skin's configured views (not gears' Wall).
        try:
            # These three all write into GEARS' own settings.db / scraper stack.
            # A POV box has neither, so they are gears-only by definition
            # (POV's views + settings are seeded by content_source at install).
            if engine == 'plugin.video.gears':
                from resources.libs import modular_update as mu
                # fresh-install catch-up: land any gears settings the config
                # enforcement stashed while the db didn't exist yet (e.g. the
                # magneto default selection) -- BEFORE the scraper sync reads it
                mu.apply_pending_gears_settings()
                mu.apply_gears_views_for_skin()
                # scraper lifecycle: keep only the SELECTED external scraper
                # enabled; neutralize the unused standby (its settings-monitor
                # service is dead weight). gearsscrapers is never touched.
                mu.sync_scraper_stack()
        except Exception as e:
            log("post-prewarm views apply failed: %s" % e, xbmc.LOGDEBUG)
    except Exception as e:
        log("prewarm error: %s" % e, xbmc.LOGDEBUG)


def _has_debrid():
    """True when a debrid credential actually exists for the installed engine.

    Asked of the DATA, not of the user's keep choice: that also covers the case
    where debrid WAS kept but there was never a token to keep. 'empty_setting'
    is Gears' "never configured" placeholder and must not count as a login --
    carrying it across once made every unused service look authorised.
    Unknown/unreadable -> True, so a failure here never nags the user.
    """
    import re as _re
    import sqlite3 as _sq
    try:
        from resources.libs import keep as keep_mod
    except Exception:
        return True
    TOKENS = ('tb.token', 'rd.token', 'rd.refresh', 'pm.token', 'ad.token',
              'oc.token', 'premiumize.token', 'easynews_user')
    try:
        if os.path.exists(keep_mod.POV_SETTINGS):
            with open(keep_mod.POV_SETTINGS, encoding='utf-8', errors='replace') as fh:
                txt = fh.read()
            for sid in TOKENS:
                m = _re.search(r'<setting id="%s"[^>]*>([^<]*)</setting>' % _re.escape(sid), txt)
                if m and m.group(1).strip() not in ('', 'empty_setting'):
                    return True
    except Exception as e:
        log('services: POV token check failed: %s' % e, xbmc.LOGWARNING)
        return True
    try:
        if os.path.exists(keep_mod.GEARS_SETTINGS_DB):
            con = _sq.connect(keep_mod.GEARS_SETTINGS_DB)
            try:
                rows = con.execute(
                    'SELECT setting_id, setting_value FROM settings').fetchall()
            finally:
                con.close()
            for sid, val in rows:
                if sid in TOKENS and str(val or '').strip() not in ('', 'empty_setting'):
                    return True
    except Exception as e:
        log('services: Gears token check failed: %s' % e, xbmc.LOGWARNING)
        return True
    return False


def _offer_services_connect(mon):
    """Once per install, offer to connect a debrid service on the home screen.

    A freshly installed build has no debrid credentials, so it cannot play
    anything until the user finds the 'חיבור שירותים' shortcut themselves. Ask
    once, right after the build comes up (Asaf, 2026-08-15).

    The 'asked' flag lives in the WIZARD's addon_data, which the wipe
    deliberately preserves -- so a flag written on the old build would survive a
    reinstall and silence the question exactly when it matters most (the same
    trap that made the seeds marker-gated bug). install_build() therefore clears
    it, which re-arms this for each install and keeps it to ONE ask.

    POV has no per-service deep link -- its only entry is `myservices`, a select
    list of all nine services -- so POV lands on that list and the user picks
    TorBox. Gears has a direct torbox.authenticate mode.
    """
    try:
        if ADDON.getSetting('services_prompt_done') == 'true':
            return
        if _has_debrid():
            return
        if get_addon_version('plugin.video.gears'):
            url, engine = 'plugin://plugin.video.gears/?mode=torbox.authenticate', 'gears'
        elif get_addon_version('plugin.video.pov'):
            url, engine = 'plugin://plugin.video.pov/?mode=myservices', 'pov'
        else:
            return                              # no engine -> nothing to connect
        # wait for the home screen: the dialog must not open behind the skin's
        # own startup work, and a build install reloads the skin on this boot
        for _ in range(40):
            if mon.abortRequested():
                return
            if xbmc.getCondVisibility('Window.IsVisible(home)'):
                break
            if mon.waitForAbort(1):
                return
        # mark BEFORE asking: a force-close mid-dialog must not re-ask forever
        ADDON.setSetting('services_prompt_done', 'true')
        log('services: no debrid credential found, offering %s connect' % engine)
        if xbmcgui.Dialog().yesno(
                ADDON_NAME,
                'הבילד מוכן.\n\nכדי לצפות צריך חשבון [COLOR cyan]TorBox[/COLOR].\n'
                'להתחבר עכשיו?',
                yeslabel='התחבר', nolabel='אחר כך'):
            xbmc.executebuiltin('RunPlugin(%s)' % url)
    except Exception as e:
        log('services connect offer failed: %s' % e, xbmc.LOGWARNING)


class POVHebrewService(xbmc.Monitor):
    def __init__(self):
        super().__init__()
        self.gears_version = get_addon_version('plugin.video.gears')
        # Log the ACTIVE skin -- a hardcoded AF3 version here once sent a
        # debugging session down the wrong path.
        active = xbmc.getSkinDir() or '?'
        log(f"Service initialized - Gears: {self.gears_version}, "
            f"Skin: {active} {get_addon_version(active) or ''}")

    def run(self):
        """Main service loop: sweep, run one manifest update pass, then idle."""
        # First boot after a skin (re)install: the skin compiles its menu on
        # Home load but the loaded skin still holds the pre-build include stubs,
        # so WIDGETS don't render until a reload. Run the marker rebuild FIRST,
        # before any settle/update wait -- the handler itself waits for the
        # compiled includes to appear, so the one visible reload lands seconds
        # after boot, before the user starts navigating (running it after the
        # 15s settle yanked mid-navigation users back to home).
        if not self.waitForAbort(2):
            # Make sure the Gears shortcut folder the default networks widget
            # points at exists BEFORE the rebuild's reload populates widgets
            # (no-op after the first successful seed).
            try:
                from resources.libs import modular_update
                modular_update.seed_gears_shortcut_folder()
                # cpath_cache.db is `update: skip` in the config policy, so a row
                # ADDED to the shipped config never reaches an existing box --
                # add the missing ones (key-absent only) here.
                modular_update.seed_nimbus_missing_cpaths()
                # the shipped skinshortcuts dir is `update: skip`, so a menu
                # correction only reaches existing boxes through a migration
                modular_update.fix_invalid_tmdb_widgets()
                # a KEEP reinstall planted Gears' 'empty_setting' sentinel as
                # POV debrid tokens -> fake "authorized" services; keep.py is
                # fixed for future reinstalls, existing boxes need the scrub
                modular_update.fix_pov_placeholder_tokens()
            except Exception as e:
                log(f"gears networks seed error: {e}", xbmc.LOGWARNING)
            _process_pending_view_rebuild()

        # A freshly installed skin gets its own setup wizard now -- before the
        # branch below, because the post-install boot returns into its own idle
        # loop and would otherwise never reach it. This is the boot the user is
        # looking at right after installing a skin.
        _run_pending_skin_setup_when_home(self)

        # Skip the check once right after a build install (the wizard sets this).
        if ADDON.getSetting('skip_update_check') == 'true':
            log("Skipping update check (after build installation)")
            ADDON.setSetting('skip_update_check', 'false')
            # Every install path sets this flag and then restarts, so THIS boot
            # is the deferred-work boot: the dropped previous skin must be
            # removed here (not two boots later). Only the network update
            # check is skipped. Removal runs IMMEDIATELY -- it's local disk
            # work, and a user who quits early must not carry it another boot.
            _process_pending_skin_removal()
            if not self.waitForAbort(12):
                _prewarm_gears(self)
            # this IS the boot right after an install -- the moment the build
            # comes up with no credentials, which is what the offer is for
            _offer_services_connect(self)
            while not self.abortRequested():
                if self.waitForAbort(300):
                    break
                _process_pending_skin_setup()
            return

        # Remove a previous skin the user chose to drop when switching skins
        # (deferred to this boot so it's not the running skin anymore).
        # BEFORE the settle wait: it's local disk work with no network, and a
        # fast quit (the 2026-07-18 test: exit 1s before the settle ended)
        # must not postpone it yet another boot.
        _process_pending_skin_removal()

        # Wait for Kodi to settle before touching the network (configurable).
        try:
            delay = int(ADDON.getSetting('update_check_delay') or '8')
        except Exception:
            delay = 8
        delay = max(5, min(delay, 60))
        log("Service started, settling for %ss..." % delay)
        if self.waitForAbort(delay):
            return

        # Also offered on a normal boot, not just the post-install one: an
        # EXE/APK first run reaches the home screen WITHOUT going through
        # install_build, so it would otherwise never be asked. The flag makes
        # this a no-op on every later boot.
        _offer_services_connect(self)

        # Sweep stale '<addon>_old_<timestamp>' backup dirs from past updates.
        _cleanup_old_addon_dirs()

        # Manifest-driven update: ONE pass updates every addon (Gears + overlay,
        # AI subs, skins, and the wizard itself) from the MasterKodi-IL-Build
        # manifest, verifying each sha256 before installing.
        if ADDON.getSettingBool('auto_update_check'):
            log("Running manifest update check...")
            try:
                from resources.libs import modular_update
                modular_update.silent_check()
            except Exception as e:
                log(f"manifest update error: {e}", xbmc.LOGERROR)
        else:
            log("Auto update check disabled")

        # Warm gears now that boot + update check are done (nothing else to do).
        _prewarm_gears(self)

        # Keep the service alive until Kodi shuts down.
        #
        # The pending skin removal is also processed HERE, not only on the boot
        # paths above. On Android there is no working Kodi restart, so a skin
        # switch is applied live (_apply_skin_live) and the boot that was meant
        # to carry out the deferred removal never comes -- the dropped skin sat
        # on disk forever and the user was told nothing (Asaf, Xiaomi,
        # 2026-08-29). Doing it from the idle loop needs no restart anywhere.
        #
        # Safe to call at any moment: _process_pending_skin_removal removes
        # nothing while the skin named in the marker is still the running skin,
        # so it cannot fire while Kodi's "keep this skin?" prompt could still
        # revert the switch, and it cannot pull the skin out from under the user.
        removal_ticks = 0
        while not self.abortRequested():
            pending = (os.path.isfile(_pending_removal_marker())
                       or os.path.isfile(_pending_setup_marker()))
            if not pending:
                removal_ticks = 0
            # Poll quickly for the first minute after a marker appears (the
            # Android case: the user is looking at the result now), then fall
            # back to the idle cadence so a marker we can never act on -- the
            # user reverted, so the old skin is the running skin again -- is
            # retried cheaply rather than spun on.
            if self.waitForAbort(5 if (pending and removal_ticks < 12) else 300):
                break
            if pending:
                removal_ticks += 1
                if os.path.isfile(_pending_removal_marker()):
                    _process_pending_skin_removal()
            _process_pending_skin_setup()
        log("Service stopped")


if __name__ == '__main__':
    POVHebrewService().run()
