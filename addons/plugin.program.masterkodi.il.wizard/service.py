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
    try:
        os.remove(marker)
    except Exception:
        pass
    if not sid:
        return
    try:
        if xbmc.getSkinDir() == sid:      # somehow still active -> leave it
            return
        from resources.libs.builds import BuildManager
        if BuildManager().remove_skin(sid):
            log(f"Removed previous skin after switch: {sid}")
    except Exception as e:
        log(f"pending skin removal failed for {sid}: {e}", xbmc.LOGWARNING)


class POVHebrewService(xbmc.Monitor):
    def __init__(self):
        super().__init__()
        self.gears_version = get_addon_version('plugin.video.gears')
        self.skin_version = get_addon_version('skin.arctic.fuse.3')
        log(f"Service initialized - Gears: {self.gears_version}, Skin: {self.skin_version}")

    def run(self):
        """Main service loop: sweep, run one manifest update pass, then idle."""
        # Skip the check once right after a build install (the wizard sets this).
        if ADDON.getSetting('skip_update_check') == 'true':
            log("Skipping update check (after build installation)")
            ADDON.setSetting('skip_update_check', 'false')
            while not self.abortRequested():
                if self.waitForAbort(300):
                    break
            return

        # Wait for Kodi to settle before touching the network (configurable).
        try:
            delay = int(ADDON.getSetting('update_check_delay') or '15')
        except Exception:
            delay = 15
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

        # Keep the service alive until Kodi shuts down.
        while not self.abortRequested():
            if self.waitForAbort(300):
                break
        log("Service stopped")


if __name__ == '__main__':
    POVHebrewService().run()
