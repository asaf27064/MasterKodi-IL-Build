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
                if target_path:
                    shutil.copy2(sfile, target_path)
                    stash_applied = True
                    log("applied deferred skin settings from config stash")
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
            xbmc.executebuiltin('RunScript(script.skinvariables,action=buildviews)')
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


def _process_pending_skin_removal():
    """Uninstall the skin the user dropped during a skin switch. Deferred from
    the skins menu to now (the old skin is no longer the running one)."""
    marker = os.path.join(xbmcvfs.translatePath('special://userdata/addon_data/'),
                          ADDON_ID, 'pending_skin_removal')
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
    try:
        os.remove(marker)
    except Exception:
        pass
    try:
        from resources.libs.builds import BuildManager
        if BuildManager().remove_skin(sid):
            log(f"Removed previous skin after switch: {sid}")
    except Exception as e:
        log(f"pending skin removal failed for {sid}: {e}", xbmc.LOGWARNING)


def _prewarm_gears(mon):
    """Warm Gears so the FIRST home-widget/shortcut click is fast. The first
    plugin call pays a cold start (python imports of the whole gears stack +
    TMDB/Trakt list fetch); gears has reuselanguageinvoker so every later call
    reuses the warm interpreter. We pay that cost here silently instead of on
    the user's first click. Headless via JSON-RPC Files.GetDirectory (no
    window opens). Fail-open: any error -> do nothing."""
    try:
        if not xbmc.getCondVisibility('System.HasAddon(plugin.video.gears)'):
            return
        paths = (
            'plugin://plugin.video.gears/?name=Trending&mode=build_movie_list'
            '&action=trakt_movies_trending&random_support=true&iconImage=trending',
            'plugin://plugin.video.gears/?name=Trending&mode=build_tvshow_list'
            '&action=trakt_tv_trending&random_support=true&iconImage=trending',
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
        log("gears pre-warm done")
        # The pre-warm is what CREATES gears' settings.db on a fresh install
        # (gears fills every setting with defaults on first run). The install-
        # time apply_gears_views_for_skin() no-oped back then because the db
        # didn't exist yet -- re-apply now that it does, so a fresh box's first
        # browse already uses the skin's configured views (not gears' Wall).
        try:
            from resources.libs import modular_update as mu
            mu.apply_gears_views_for_skin()
        except Exception as e:
            log("post-prewarm views apply failed: %s" % e, xbmc.LOGDEBUG)
    except Exception as e:
        log("prewarm error: %s" % e, xbmc.LOGDEBUG)


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
            except Exception as e:
                log(f"gears networks seed error: {e}", xbmc.LOGWARNING)
            _process_pending_view_rebuild()

        # Skip the check once right after a build install (the wizard sets this).
        if ADDON.getSetting('skip_update_check') == 'true':
            log("Skipping update check (after build installation)")
            ADDON.setSetting('skip_update_check', 'false')
            # Every install path sets this flag and then restarts, so THIS boot
            # is the deferred-work boot: the dropped previous skin must be
            # removed here (not two boots later). Only the network update
            # check is skipped.
            if not self.waitForAbort(8):
                _process_pending_skin_removal()
            if not self.waitForAbort(12):
                _prewarm_gears(self)
            while not self.abortRequested():
                if self.waitForAbort(300):
                    break
            return

        # Wait for Kodi to settle before touching the network (configurable).
        try:
            delay = int(ADDON.getSetting('update_check_delay') or '8')
        except Exception:
            delay = 8
        delay = max(5, min(delay, 60))
        log("Service started, settling for %ss..." % delay)
        if self.waitForAbort(delay):
            return

        # Sweep stale '<addon>_old_<timestamp>' backup dirs from past updates.
        _cleanup_old_addon_dirs()

        # Remove a previous skin the user chose to drop when switching skins
        # (deferred here so it's not the running skin anymore).
        _process_pending_skin_removal()

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
        while not self.abortRequested():
            if self.waitForAbort(300):
                break
        log("Service stopped")


if __name__ == '__main__':
    POVHebrewService().run()
