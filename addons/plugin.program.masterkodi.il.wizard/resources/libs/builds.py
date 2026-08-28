# -*- coding: utf-8 -*-
"""
MasterKodi IL Wizard - Build Installation
Flow: Select Build (from build.txt) -> Select Skin (Estuary/Arctic Fuse) -> Install
      OR: Add Arctic Fuse to existing build
"""
import os
import shutil
import time
import xbmc
import xbmcvfs
import xbmcgui
import xbmcaddon

try:
    import zipfile
except ImportError:
    from resources.libs import zipfile

try:
    import requests
except ImportError:
    requests = None

from resources.libs.config import (
    ADDON_ID, ADDON_NAME, HOME, ADDONS, USERDATA, ADDON_DATA_PATH,
    BUILD_TXT_URL, TEMP_FOLDER, COLOR_ERROR, COLOR_WARNING
)
# Branded custom-window menu (same look as the wizard's main menu)
from resources.libs.ui import menu_item, wizard_select


USER_AGENT = 'Mozilla/5.0 (Windows NT 6.1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/35.0.1916.153 Safari/537.36 SE 2.X MetaSr 1.0'
ADDON = xbmcaddon.Addon()


def log(msg, level=xbmc.LOGINFO):
    xbmc.log(f'[{ADDON_ID}] Builds: {msg}', level)


def _is_android():
    try:
        return bool(xbmc.getCondVisibility('System.Platform.Android'))
    except Exception:
        return False


# Why Android never restarts, measured on the Xiaomi 2026-07-30 -- all three
# candidate mechanisms were tried on real hardware and all three failed:
#   * detached child (`sh -c 'sleep N; am start'`): the child is killed the
#     moment our process dies -- Android reaps the app's whole process group.
#     Its own log stopped after the first line, before `am` ever ran.
#   * `RestartApp`: tears the activity down and leaves the process as a zombie
#     with no window. It never comes back.
#   * graceful Quit: Kodi re-saves guisettings from MEMORY, reverting whatever
#     the install wrote to disk (measured: skin.estuary -> skin.nimbus).
# So on Android a skin switch must NOT restart at all -- it is applied in-process
# by _apply_skin_live below. A full build install still has to hard-exit (the
# whole addon tree changed under Kodi), and there the user is told plainly that
# they need to reopen Kodi.


def _enable_addons_live(addon_ids):
    """Tell the RUNNING Kodi about addons we just enabled in Addons33.db.

    sync_skin_stacks enables a skin's dependency stack by writing straight into
    the DB, which Kodi does not re-read while running. An in-process skin switch
    therefore asks for a skin whose dependencies Kodi still believes are
    disabled: the skin fails to load and Kodi silently falls back to Estuary
    (measured on the Xiaomi -- asked for Zephyr, memory came back skin.estuary).
    Pushing the same enables through the API makes the live switch possible.

    Only ENABLES are mirrored. The matching disables stay DB-only on purpose:
    they are housekeeping for the next start, and turning addons off underneath
    the skin that is still rendering is a good way to destabilise it.
    """
    import json as _json
    done = 0
    for aid in addon_ids:
        req = {'jsonrpc': '2.0', 'id': 1, 'method': 'Addons.SetAddonEnabled',
               'params': {'addonid': aid, 'enabled': True}}
        try:
            resp = _json.loads(xbmc.executeJSONRPC(_json.dumps(req)))
            if not resp.get('error'):
                done += 1
        except Exception:
            pass
    log('live enable: %d/%d addons enabled in the running Kodi'
        % (done, len(addon_ids)))
    return done


def _apply_skin_live(skin_id, fontset=None, timeout=25):
    """Switch Kodi to `skin_id` in-process, without a restart.

    Writing the skin into guisettings.xml is not enough on its own: Kodi saves
    that file from MEMORY on the next graceful exit and would revert us. Pushing
    the value through the settings API instead keeps memory and disk in
    agreement AND loads the skin immediately.

    Kodi asks "Keep this skin?" after a settings-driven skin change and reverts
    if nobody answers, so a watcher thread confirms it for us.

    Returns True once Kodi reports the new skin as active.
    """
    import json as _json

    getter = {'jsonrpc': '2.0', 'id': 1, 'method': 'Settings.GetSettingValue',
              'params': {'setting': 'lookandfeel.skin'}}

    def _current():
        try:
            return _json.loads(xbmc.executeJSONRPC(
                _json.dumps(getter))).get('result', {}).get('value')
        except Exception:
            return None

    def _prompt_open():
        try:
            return bool(xbmc.getCondVisibility('Window.IsVisible(yesnodialog)'))
        except Exception:
            return False

    def _dismiss_prompt():
        """Answer "Keep this skin?" with YES, and make sure it actually closed.

        A single click is not enough: the click can land while the dialog is
        still initialising and is then dropped, leaving the prompt open behind
        whatever the wizard shows next. If nobody answers it, Kodi reverts to
        the previous skin -- which is what happened on the first attempt (the
        prompt sat there for two minutes and a stray Back press cancelled it).
        """
        for _ in range(10):
            if not _prompt_open():
                return True
            xbmc.executebuiltin('SendClick(10100,11)')   # 11 = yes, 10 = no
            xbmc.sleep(400)
        return not _prompt_open()

    req = {'jsonrpc': '2.0', 'id': 1, 'method': 'Settings.SetSettingValue',
           'params': {'setting': 'lookandfeel.skin', 'value': skin_id}}
    try:
        resp = _json.loads(xbmc.executeJSONRPC(_json.dumps(req)))
    except Exception as e:
        log('live skin switch failed to call settings API: %s' % e, xbmc.LOGERROR)
        return False
    if resp.get('error'):
        log('live skin switch rejected: %s' % resp['error'], xbmc.LOGERROR)
        return False

    # 1) wait for the skin to load, answering the keep-skin prompt on the way
    deadline = time.time() + timeout
    active = False
    while time.time() < deadline:
        if _prompt_open():
            _dismiss_prompt()
            continue
        if _current() == skin_id:
            active = True
            break
        xbmc.sleep(300)
    if not active:
        log('live skin switch: %s did not become active within %ss'
            % (skin_id, timeout), xbmc.LOGWARNING)
        return False

    def _set_font():
        # The font is GLOBAL (lookandfeel.font) and names a fontset that belongs
        # to the skin. Changing the skin makes Kodi RESET the font to 'Default'
        # (the font list is skin-specific), which renders Hebrew as boxes/tofu.
        # We re-apply the skin's Hebrew fontset. Must run AFTER the skin loads
        # (the fontset only exists then), and as EARLY as possible after that --
        # doing it here rather than after the settle loop shrinks the tofu flash
        # from up to ~8s down to the skin-load time.
        if not fontset:
            return
        freq = {'jsonrpc': '2.0', 'id': 1, 'method': 'Settings.SetSettingValue',
                'params': {'setting': 'lookandfeel.font', 'value': fontset}}
        try:
            fresp = _json.loads(xbmc.executeJSONRPC(_json.dumps(freq)))
            if fresp.get('error'):
                log('live skin switch: could not set fontset %s: %s'
                    % (fontset, fresp['error']), xbmc.LOGWARNING)
            else:
                log('live skin switch: fontset set to %s' % fontset)
        except Exception as e:
            log('live skin switch: fontset call failed: %s' % e, xbmc.LOGWARNING)

    # 2) fix the font IMMEDIATELY (kills the tofu as soon as possible)
    _set_font()

    # 3) the keep-skin prompt can still appear just AFTER the skin loads --
    #    settle until it has been gone for a couple of consecutive seconds
    settle = time.time() + 8
    while time.time() < settle:
        if _prompt_open():
            if not _dismiss_prompt():
                log('live skin switch: could not dismiss the keep-skin prompt',
                    xbmc.LOGWARNING)
                return False
            settle = time.time() + 4     # restart the quiet period
        xbmc.sleep(400)

    # 4) the real acceptance test: an unanswered prompt reverts the skin, so
    #    only report success if it is STILL the one we asked for
    final = _current()
    if final != skin_id:
        log('live skin switch: reverted to %s after the prompt' % final,
            xbmc.LOGWARNING)
        return False

    # if the revert-check or the keep-prompt bounced the font back to Default,
    # re-apply it once more so the box lands with Hebrew intact
    if fontset and _json.loads(xbmc.executeJSONRPC(_json.dumps(
            {'jsonrpc': '2.0', 'id': 1, 'method': 'Settings.GetSettingValue',
             'params': {'setting': 'lookandfeel.font'}}))).get(
                 'result', {}).get('value') != fontset:
        _set_font()

    log('live skin switch: %s is active and confirmed (no restart needed)' % skin_id)
    return True


def _allow_insecure_ssl():
    """Opt-in (default OFF) escape hatch for ancient embedded OpenSSL that cannot
    verify modern certificate chains. Kept OFF by default: an AUTOMATIC fallback
    to an unverified TLS context let an active network attacker force the first
    (verified) attempt to fail and then MITM the unverified retry -- on the very
    path that downloads and installs a build over the wiped device. A user who
    genuinely needs it can set `allow_insecure_ssl=true`."""
    try:
        return ADDON.getSetting('allow_insecure_ssl') == 'true'
    except Exception:
        return False


class SkinPickerDialog(xbmcgui.WindowXMLDialog):
    """Skin picker with a LARGE live preview (skin-picker.xml).

    dialog.select's useDetails thumbnails are tiny; this shows a ~1120x630
    preview of the focused skin beside the list. Returns the selected index
    via .selection (-1 = cancelled)."""

    def __init__(self, *args, **kwargs):
        self.items = kwargs.pop('items', [])       # [(name, desc, image_path)]
        self.heading = kwargs.pop('heading', '')
        self.selection = -1
        super().__init__(*args)

    @staticmethod
    def pick(heading, items):
        """items: [(name, desc, image_path)] -> selected index or -1."""
        d = SkinPickerDialog('skin-picker.xml',
                             xbmcvfs.translatePath(ADDON.getAddonInfo('path')),
                             'Default', '1080i',
                             items=items, heading=heading)
        d.doModal()
        sel = d.selection
        del d
        return sel

    def onInit(self):
        self.setProperty('heading', self.heading)
        lst = self.getControl(100)
        lst.reset()
        for item in self.items:
            # (name, desc, image) or (name, desc, image, preview_dir). With a
            # dir the picker runs a full-size SLIDESHOW over every screenshot
            # in it (multiimage) instead of one composite image that squeezed
            # two screenshots into half the height each.
            name, desc, img = item[0], item[1], item[2]
            li = xbmcgui.ListItem(name, desc)
            li.setArt({'icon': img, 'thumb': img})
            if len(item) > 3 and item[3] and os.path.isdir(item[3]):
                li.setProperty('previewdir', item[3])
            lst.addItem(li)
        self.setFocusId(100)

    def onClick(self, control_id):
        if control_id == 100:
            self.selection = self.getControl(100).getSelectedPosition()
            self.close()

    def onAction(self, action):
        if action.getId() in (9, 10, 92):  # BACK / PREVIOUS_MENU / NAV_BACK
            self.selection = -1
            self.close()


class BuildManager:
    def __init__(self):
        self.dialog = xbmcgui.Dialog()
        self.builds = []
        
    def _fetch_build_txt(self):
        """Fetch build.txt text. Tries requests, then falls back to plain urllib.
        Kodi's embedded Python + urllib3 2.x can fail SSL where urllib works, so
        the urllib path (same one the manifest updater uses reliably) is the
        safety net -- a network hiccup should never leave the build list empty."""
        # 1) requests (fast path)
        if requests is not None:
            try:
                log(f"Fetching builds (requests) from: {BUILD_TXT_URL}")
                r = requests.get(BUILD_TXT_URL, headers={'user-agent': USER_AGENT}, timeout=10)
                r.raise_for_status()
                if r.text.strip():
                    return r.text
                log("requests returned empty build.txt", xbmc.LOGWARNING)
            except Exception as e:
                log(f"requests fetch failed ({e}), trying urllib", xbmc.LOGWARNING)
        # 2) urllib fallback (proven to work in this Kodi runtime)
        try:
            import ssl
            try:
                from urllib.request import urlopen, Request
            except ImportError:
                from urllib2 import urlopen, Request
            log(f"Fetching builds (urllib) from: {BUILD_TXT_URL}")
            req = Request(BUILD_TXT_URL, headers={'User-Agent': USER_AGENT})
            try:
                data = urlopen(req, timeout=15).read()
            except Exception:
                # SECURITY: only retry WITHOUT cert verification if the user has
                # explicitly opted in (default OFF -- see _allow_insecure_ssl).
                if not _allow_insecure_ssl():
                    raise
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                data = urlopen(req, timeout=15, context=ctx).read()
            return data.decode('utf-8', 'replace')
        except Exception as e:
            log(f"urllib fetch failed too: {e}", xbmc.LOGERROR)
            return ''

    def fetch_builds_list(self):
        """Fetch list of available builds from build.txt (requests -> urllib)."""
        text = self._fetch_build_txt()
        builds = []
        for line in (text or '').strip().split('\n'):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            build_info = {}
            for part in line.split('" '):
                if '="' in part:
                    key, value = part.split('="', 1)
                    build_info[key.strip()] = value.rstrip('"').strip()
            if 'name' in build_info and 'url' in build_info:
                builds.append(build_info)
        self.builds = builds
        log(f"Fetched {len(builds)} builds")
        return builds

    def _urllib_download(self, url, dest, progress_dialog, title):
        """urllib download with progress (fallback when requests SSL fails)."""
        import ssl
        try:
            from urllib.request import urlopen, Request
        except ImportError:
            from urllib2 import urlopen, Request
        req = Request(url, headers={'User-Agent': USER_AGENT})
        try:
            resp = urlopen(req, timeout=30)
        except Exception:
            # SECURITY: unverified retry is opt-in only (default OFF). This is the
            # build-ZIP download that feeds the device wipe -- an automatic
            # downgrade here is a direct MITM-to-arbitrary-install hole.
            if not _allow_insecure_ssl():
                raise
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            resp = urlopen(req, timeout=30, context=ctx)
        total = int(resp.headers.get('content-length') or 0)
        mb = 1024 * 1024
        downloaded = 0
        start_time = time.time()
        with open(dest, 'wb') as f:
            while True:
                chunk = resp.read(max(mb, (total // 512) if total else mb))
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    done = int(100 * downloaded / total)
                    try:
                        spd = downloaded / (time.time() - start_time) / 1024
                    except Exception:
                        spd = 0
                    unit = 'KB'
                    if spd >= 1024:
                        spd /= 1024; unit = 'MB'
                    msg = (f'{title}\n[COLOR yellow][B]גודל:[/B] [COLOR lime]{downloaded/mb:.2f}[/COLOR] MB '
                           f'מתוך [COLOR lime]{total/mb:.2f}[/COLOR] MB[/COLOR]\n'
                           f'[COLOR yellow][B]מהירות:[/B] [COLOR cyan]{spd:.2f}[/COLOR] {unit}/s[/COLOR]')
                    progress_dialog.update(done, msg)
        # same short-read guard as the requests path
        if total and downloaded < total:
            log(f"Truncated download (urllib): {downloaded}/{total} bytes for {url}",
                xbmc.LOGERROR)
            return False
        log(f"Downloaded (urllib): {dest}")
        return True

    def download_file(self, url, dest, progress_dialog, title="מוריד..."):
        """Download file - requests with a urllib fallback for Kodi SSL quirks."""
        try:
            path = os.path.split(dest)[0]
            if not os.path.exists(path):
                os.makedirs(path)

            log(f"Downloading: {url}")
            if requests is None:
                return self._urllib_download(url, dest, progress_dialog, title)
            try:
                response = requests.get(url, headers={'user-agent': USER_AGENT}, timeout=10, stream=True)
            except Exception as e:
                log(f"requests download failed ({e}), using urllib", xbmc.LOGWARNING)
                return self._urllib_download(url, dest, progress_dialog, title)

            with open(dest, 'wb') as f:
                if not response:
                    return False
                
                total = response.headers.get('content-length')
                
                if total is None:
                    f.write(response.content)
                else:
                    downloaded = 0
                    total = int(total)
                    start_time = time.time()
                    mb = 1024 * 1024
                    
                    for chunk in response.iter_content(chunk_size=max(int(total / 512), mb)):
                        downloaded += len(chunk)
                        f.write(chunk)
                        
                        done = int(100 * downloaded / total)
                        
                        try:
                            kbps_speed = downloaded / (time.time() - start_time)
                        except Exception:
                            kbps_speed = 0
                        
                        if kbps_speed > 0 and done < 100:
                            eta = (total - downloaded) / kbps_speed
                        else:
                            eta = 0
                        
                        kbps_speed = kbps_speed / 1024
                        type_speed = 'KB'
                        
                        if kbps_speed >= 1024:
                            kbps_speed = kbps_speed / 1024
                            type_speed = 'MB'
                        
                        currently_downloaded = f'[COLOR yellow][B]גודל:[/B] [COLOR lime]{downloaded/mb:.2f}[/COLOR] MB מתוך [COLOR lime]{total/mb:.2f}[/COLOR] MB[/COLOR]'
                        div = divmod(int(eta), 60)
                        speed = f'[COLOR yellow][B]מהירות:[/B] [COLOR cyan]{kbps_speed:.2f}[/COLOR] {type_speed}/s | [B]זמן:[/B] [COLOR orange]{div[0]:02d}:{div[1]:02d}[/COLOR][/COLOR]'
                        
                        progress_dialog.update(done, f'{title}\n' + currently_downloaded + '\n' + speed)

                    # A connection dropped mid-stream ends iter_content WITHOUT
                    # raising -- we used to return success on a truncated file.
                    # The install then wiped the box and only failed later at
                    # extract, leaving the user with an empty Kodi. Treat a short
                    # read as a failed download (the caller aborts BEFORE the wipe).
                    if downloaded < total:
                        log(f"Truncated download: {downloaded}/{total} bytes for {url}",
                            xbmc.LOGERROR)
                        return False

            log(f"Downloaded: {dest}")
            return True
            
        except Exception as e:
            log(f"Download error: {e}", xbmc.LOGERROR)
            return False

    def wipe(self, progress_dialog):
        """Wipe Kodi - delete everything except wizard, downloads, and My_Builds.

        Returns the count of files that could NOT be deleted (e.g. locked on
        Windows). >0 means the fresh build is about to be extracted OVER a
        partially-retained old install, so the caller must not report a clean
        success -- it warns instead."""
        log("Starting wipe...")

        exclude_dirs = [ADDON_ID, 'packages', 'My_Builds', 'temp', 'cache']
        # The Gears/POV default download dirs (Movies/TV Show/Premium/Image
        # Downloads) live inside addon_data and hold the user's own downloaded
        # media -- preserve them across a reinstall. Scope the match by PARENT to
        # those two addon dirs, so an unrelated folder that merely shares one of
        # these names elsewhere under Kodi home is still wiped as normal.
        dl_names = {'Movies Downloads', 'TV Show Downloads',
                    'Premium Downloads', 'Image Downloads'}
        dl_parents = {'plugin.video.gears', 'plugin.video.pov'}

        # Textures13.db and Thumbnails/ are ONE cache in two places: the db holds
        # a row per cached image, the folder holds the files. Kodi keeps that db
        # open, so on Windows it survives the wipe -- and wiping the folder anyway
        # left 854 rows pointing at files that were gone, so Kodi logged
        # "Direct texture file loading failed" for every one of them and crawled
        # through the first paint re-caching (Asaf's reinstall, 2026-08-24).
        #
        # Try the db FIRST and let the answer decide: if it goes, the thumbs go
        # with it; if it is locked, the thumbs stay. Either way the two agree.
        textures_db = os.path.join(USERDATA, 'Database', 'Textures13.db')
        if os.path.isfile(textures_db):
            try:
                os.remove(textures_db)
                log('wipe: Textures13.db removed -- thumbnails wiped with it')
            except Exception as e:
                exclude_dirs.append('Thumbnails')
                log('wipe: Textures13.db is locked (%s) -- KEEPING Thumbnails so the '
                    'texture cache stays consistent' % e, xbmc.LOGWARNING)

        def _prune(dirs, root):
            base = os.path.basename(root)
            return [d for d in dirs
                    if d not in exclude_dirs
                    and not (d in dl_names and base in dl_parents)]

        total_files = 0
        for root, dirs, files in os.walk(HOME):
            dirs[:] = _prune(dirs, root)
            total_files += len(files)

        del_file = 0
        del_fail = 0
        progress_dialog.update(0, "[COLOR yellow]מנקה קבצים ותיקיות...[/COLOR]")

        for root, dirs, files in os.walk(HOME, topdown=True):
            dirs[:] = _prune(dirs, root)

            for name in files:
                del_file += 1
                filepath = os.path.join(root, name)

                if name.endswith('.log') or name.endswith('.old.log'):
                    continue

                # NEVER unlink the LIVE addon registry (Addons33.db + its WAL/SHM).
                # Kodi (and this wizard) hold it OPEN; deleting it mid-session makes
                # every later write fail with SQLITE_READONLY_DBMOVED -- enable_
                # addons_in_db then silently no-ops and the reinstalled build boots
                # with EVERYTHING unregistered/disabled (the 2026-07-30 Xiaomi/
                # Shield breakage; Android enforces DBMOVED strictly). The bundle's
                # Addons DB is MERGED into this live file at extract time instead,
                # and stale rows are reconciled after extraction.
                if (os.path.basename(root) == 'Database'
                        and name.startswith('Addons')
                        and '.db' in name):
                    continue

                try:
                    os.remove(filepath)
                except Exception as e:
                    del_fail += 1
                    log(f"wipe: could not delete {filepath}: {e}", xbmc.LOGWARNING)

                if del_file % 100 == 0:
                    pct = min(int(del_file * 100 / max(total_files, 1)), 100)
                    progress_dialog.update(pct, f"[COLOR yellow]מוחק קבצים...[/COLOR]\n{del_file}/{total_files}")

        progress_dialog.update(95, "[COLOR yellow]מנקה תיקיות ריקות...[/COLOR]")
        for root, dirs, files in os.walk(HOME, topdown=False):
            dirs[:] = _prune(dirs, root)
            for name in dirs:
                dirpath = os.path.join(root, name)
                if name not in ["Database", "userdata", "temp", "addons", "addon_data"]:
                    try:
                        if not os.listdir(dirpath):
                            os.rmdir(dirpath)
                    except Exception:
                        pass

        if del_fail:
            log(f"Wipe complete with {del_fail} undeletable file(s)", xbmc.LOGWARNING)
        else:
            log("Wipe complete")
        return del_fail

    def validate_build_zip(self, zip_path, expected_addon_id=None):
        """Is this a complete, readable build zip? Returns (ok, reason).

        When expected_addon_id is given (optional-skin installs), the archive must
        actually contain that addon -- a mistaken catalog URL pointing at another
        VALID zip would otherwise pass CRC + structure and be set as the wrong skin.

        MUST be called BEFORE the wipe. A corrupt/truncated download used to slip
        through (only size>0 was checked, and grab_addons_from_zip swallowed the
        error), so the install wiped the box first and only discovered the bad
        zip at extract time -- leaving the user with an empty Kodi and no build.
        Now a bad download aborts while the existing build is still intact."""
        try:
            with zipfile.ZipFile(zip_path, 'r', allowZip64=True) as z:
                names = z.namelist()
                if not names:
                    return False, 'הקובץ ריק'
                if not any(n.startswith('addons/') for n in names):
                    return False, 'לא נמצאו אדונים בקובץ'
                # identity: the archive must contain the REQUESTED addon, not just
                # SOME addon (guards against a wrong-but-valid zip being installed
                # and then selected as a skin that was never delivered).
                if expected_addon_id:
                    want = 'addons/%s/addon.xml' % expected_addon_id
                    if not any(n.replace('\\', '/') == want for n in names):
                        return False, 'הקובץ אינו מכיל את הסקין המבוקש (%s)' % expected_addon_id
                # FULL CRC verification BEFORE the wipe. This decompresses the
                # whole archive (tens of seconds on a weak box), but a corrupt
                # NON-critical member (a font/module) would otherwise pass a
                # structural/critical-only check, the wipe would run, and the file
                # would be silently dropped on extract -> a broken install on a
                # gutted box. Not bricking the device is worth the one-time cost.
                bad = z.testzip()
                if bad is not None:
                    return False, 'קובץ פגום בהורדה: %s' % bad
                # (the per-critical-member CRC below is now redundant with testzip
                # but kept for its specific, actionable message.)
                critical = [n for n in names
                            if n.replace('\\', '/').endswith('userdata/guisettings.xml')
                            or n.replace('\\', '/').endswith('/addon.xml')]
                for n in critical[:300]:
                    try:
                        with z.open(n) as fh:          # read forces CRC verification
                            while fh.read(65536):
                                pass
                    except Exception as e:
                        return False, 'קובץ קריטי פגום בהורדה: %s (%s)' % (n, e)
                # Zip-bomb / too-big-for-device guard, BEFORE the wipe: sum the
                # uncompressed sizes (cheap, from the central directory) and make
                # sure the box has room + a margin. A build that can't fit must
                # NOT wipe the existing one first.
                total = sum(getattr(i, 'file_size', 0) for i in z.infolist())
                if total > 12 * 1024 ** 3:
                    return False, 'הבילד גדול מדי (%d MB)' % (total // 1024 ** 2)
                try:
                    import shutil as _sh
                    free = _sh.disk_usage(xbmcvfs.translatePath('special://home/')).free
                    if free < total + 300 * 1024 ** 2:
                        return False, ('אין מספיק מקום פנוי: צריך ~%d MB, פנוי %d MB'
                                       % (total // 1024 ** 2, free // 1024 ** 2))
                except Exception:
                    pass
            return True, None
        except Exception as e:
            return False, str(e)

    def grab_addons_from_zip(self, zip_path):
        """Get list of addon IDs from the build ZIP"""
        addons = []
        try:
            zf = zipfile.ZipFile(zip_path, 'r')
            for item in zf.namelist():
                if item.startswith('addons/') and item.count('/') == 2:
                    addon_id = item.split('/')[1]
                    if addon_id and addon_id not in addons:
                        addons.append(addon_id)
            zf.close()
            log(f"Found {len(addons)} addons in build")
        except Exception as e:
            log(f"Error reading addons from zip: {e}", xbmc.LOGWARNING)
        return addons

    def enable_addons_in_db(self, addon_list):
        """Enable all addons from the build in the database (INSERT or IGNORE + UPDATE)"""
        try:
            import sqlite3
            from datetime import datetime
            
            db_path = xbmcvfs.translatePath('special://database/')
            addon_db = None
            
            # Find latest Addons database -- pick the HIGHEST schema number,
            # not whatever os.listdir happens to yield last (a box migrated
            # across Kodi majors can have several Addons*.db side by side)
            best = -1
            for f in os.listdir(db_path):
                if f.startswith('Addons') and f.endswith('.db'):
                    try:
                        num = int(f[len('Addons'):-len('.db')])
                    except ValueError:
                        continue
                    if num > best:
                        best = num
                        addon_db = os.path.join(db_path, f)
            
            if not addon_db or not os.path.exists(addon_db):
                log("Addons database not found", xbmc.LOGWARNING)
                return
            
            log(f"Updating database: {addon_db}")
            
            conn = sqlite3.connect(addon_db)
            cursor = conn.cursor()
            
            installed_time = str(datetime.now())[:-7]
            
            for addon_id in addon_list:
                try:
                    # INSERT if not exists, then UPDATE to enable
                    cursor.execute(
                        'INSERT or IGNORE into installed (addonID, enabled, installDate) VALUES (?,?,?)',
                        (addon_id, 1, installed_time)
                    )
                    cursor.execute(
                        'UPDATE installed SET enabled = 1 WHERE addonID = ?',
                        (addon_id,)
                    )
                except Exception as e:
                    log(f"Error enabling {addon_id}: {e}", xbmc.LOGWARNING)
            
            conn.commit()
            conn.close()
            log(f"Enabled {len(addon_list)} addons in database")
            
        except Exception as e:
            log(f"Error updating addon database: {e}", xbmc.LOGWARNING)

    def setup_wizard_repo_in_db(self):
        """Ensure wizard is properly linked to repo for auto-updates.
        
        Kodi requires the repo's origin to reference itself in the installed table.
        Without this, the repo appears as 'not installed' in the UI and auto-updates
        don't trigger - only manual 'Check for updates' works.
        """
        try:
            import sqlite3
            
            db_path = xbmcvfs.translatePath('special://database/')
            addon_db = None
            
            for f in os.listdir(db_path):
                if f.startswith('Addons') and f.endswith('.db'):
                    addon_db = os.path.join(db_path, f)
            
            if not addon_db or not os.path.exists(addon_db):
                log("Addons database not found for repo setup", xbmc.LOGWARNING)
                return
            
            WIZARD_ID = 'plugin.program.masterkodi.il.wizard'
            REPO_ID = 'repository.masterkodi.il'
            
            conn = sqlite3.connect(addon_db)
            cursor = conn.cursor()
            
            # 1. Wizard origin must point to repo
            cursor.execute(
                'UPDATE installed SET origin = ? WHERE addonID = ?',
                (REPO_ID, WIZARD_ID)
            )
            
            # 2. Repo origin must reference itself (this is what Kodi sets on UI install)
            cursor.execute(
                'UPDATE installed SET origin = ? WHERE addonID = ?',
                (REPO_ID, REPO_ID)
            )
            
            # 3. Ensure repo is in repo table
            cursor.execute('SELECT id FROM repo WHERE addonID = ?', (REPO_ID,))
            row = cursor.fetchone()
            if not row:
                cursor.execute('''
                    INSERT INTO repo (addonID, checksum, lastcheck, version, nextcheck)
                    VALUES (?, '', '2000-01-01 00:00:00', '1.0.0', '2000-01-01 00:00:00')
                ''', (REPO_ID,))
                repo_id = cursor.lastrowid
            else:
                repo_id = row[0]
            
            # 4. Ensure wizard is in addons table
            cursor.execute('SELECT id FROM addons WHERE addonID = ?', (WIZARD_ID,))
            row = cursor.fetchone()
            if not row:
                cursor.execute('''
                    INSERT INTO addons (addonID, version, name, summary, news, description, metadata)
                    VALUES (?, '2.0.0', 'MasterKodi IL Wizard', 'MasterKodi IL Wizard', '', '', '')
                ''', (WIZARD_ID,))
                addon_id = cursor.lastrowid
            else:
                addon_id = row[0]
            
            # 5. Ensure repo-addon link exists
            cursor.execute(
                'INSERT OR IGNORE INTO addonlinkrepo (idRepo, idAddon) VALUES (?, ?)',
                (repo_id, addon_id)
            )
            
            conn.commit()
            conn.close()
            log("Wizard repo setup complete - origin set for auto-updates")
            
        except Exception as e:
            log(f"Error setting up wizard repo: {e}", xbmc.LOGWARNING)

    def _reconcile_addons_db(self):
        """Delete registry rows for addons that no longer exist on disk.

        After a wipe+reinstall the LIVE Addons33.db (deliberately preserved --
        replacing the open file causes SQLITE_READONLY_DBMOVED) still holds the
        previous build's rows. An ENABLED row whose addon files are gone trips
        Kodi's dependency resolver at boot: it cascades disables onto real,
        healthy addons (observed un-fixing the 2026-07-30 Xiaomi twice). Rows are
        kept only for addons present in home/addons or Kodi's own system addons."""
        try:
            import sqlite3
            db_dir = xbmcvfs.translatePath('special://database/')
            target = None
            best = -1
            for f in os.listdir(db_dir):
                if f.startswith('Addons') and f.endswith('.db'):
                    try:
                        num = int(f[len('Addons'):-len('.db')])
                    except ValueError:
                        continue
                    if num > best:
                        best, target = num, os.path.join(db_dir, f)
            if not target:
                return
            present = set()
            for base in (os.path.join(HOME, 'addons'),
                         xbmcvfs.translatePath('special://xbmc/addons')):
                try:
                    present.update(d for d in os.listdir(base)
                                   if os.path.isdir(os.path.join(base, d)))
                except Exception:
                    pass
            if not present:
                return                       # can't list -> don't guess-delete
            conn = sqlite3.connect(target)
            removed = 0
            for table in ('installed', 'update_rules'):
                try:
                    rows = [r[0] for r in conn.execute(
                        f"SELECT DISTINCT addonID FROM {table}")]
                except Exception:
                    continue
                for aid in rows:
                    # xbmc.* / kodi.* virtual deps have no folder -- keep them
                    if aid in present or aid.startswith(('xbmc.', 'kodi.')):
                        continue
                    conn.execute(f"DELETE FROM {table} WHERE addonID=?", (aid,))
                    removed += 1
            conn.commit()
            conn.close()
            log(f"addons-db reconcile: removed {removed} stale row(s)")
        except Exception as e:
            log(f"addons-db reconcile failed: {e}", xbmc.LOGWARNING)

    def merge_addon_databases(self, source_db_path):
        """Merge addon entries from source database into existing Kodi database"""
        try:
            import sqlite3
            
            db_path = xbmcvfs.translatePath('special://database/')
            target_db = None
            
            # Find latest Addons database
            for f in os.listdir(db_path):
                if f.startswith('Addons') and f.endswith('.db'):
                    target_db = os.path.join(db_path, f)
            
            if not target_db or not os.path.exists(target_db):
                log("Target Addons database not found", xbmc.LOGWARNING)
                return False
            
            if not os.path.exists(source_db_path):
                log(f"Source database not found: {source_db_path}", xbmc.LOGWARNING)
                return False
            
            log(f"Merging databases: {source_db_path} -> {target_db}")
            
            # Connect to both databases
            source_conn = sqlite3.connect(source_db_path)
            target_conn = sqlite3.connect(target_db)
            
            source_cursor = source_conn.cursor()
            target_cursor = target_conn.cursor()
            
            # ONLY the addonID-keyed tables, matched BY addonID -- never by the
            # integer primary key. The old whole-table INSERT OR REPLACE keyed
            # on the bundle's OWN row ids overwrote UNRELATED rows in the
            # device db (the bundle's rows 108-119 wiped gears/gearsai/
            # skipintro/... on the 2026-07-18 AF3 switch: Kodi then re-found
            # them as new addons, DISABLED -> dead widgets, dead services).
            merged_count = 0
            for table in ('installed', 'update_rules'):
                try:
                    source_cursor.execute(f"PRAGMA table_info({table})")
                    columns = [col[1] for col in source_cursor.fetchall()]
                    if 'addonID' not in columns:
                        continue
                    cols = [c for c in columns if c.lower() != 'id']
                    cols_str = ','.join(cols)
                    source_cursor.execute(f"SELECT {cols_str} FROM {table}")
                    rows = source_cursor.fetchall()
                    aid_idx = cols.index('addonID')
                    placeholders = ','.join('?' for _ in cols)
                    for row in rows:
                        try:
                            target_cursor.execute(
                                f"DELETE FROM {table} WHERE addonID=?", (row[aid_idx],))
                            target_cursor.execute(
                                f"INSERT INTO {table} ({cols_str}) VALUES ({placeholders})", row)
                            merged_count += 1
                        except Exception:
                            pass
                except Exception as e:
                    log(f"Error merging table {table}: {e}", xbmc.LOGWARNING)
            
            target_conn.commit()
            source_conn.close()
            target_conn.close()
            
            log(f"Merged {merged_count} entries from source database")
            return True
            
        except Exception as e:
            log(f"Error merging databases: {e}", xbmc.LOGERROR)
            return False

    def merge_viewmodes_database(self, source_db_path):
        """Merge ViewModes entries from source database into existing Kodi database"""
        try:
            import sqlite3
            
            db_path = xbmcvfs.translatePath('special://database/')
            target_db = None
            
            # Find latest ViewModes database
            for f in os.listdir(db_path):
                if f.startswith('ViewModes') and f.endswith('.db'):
                    target_db = os.path.join(db_path, f)
            
            if not target_db or not os.path.exists(target_db):
                log("Target ViewModes database not found", xbmc.LOGWARNING)
                return False
            
            if not os.path.exists(source_db_path):
                log(f"Source ViewModes database not found: {source_db_path}", xbmc.LOGWARNING)
                return False
            
            log(f"Merging ViewModes databases: {source_db_path} -> {target_db}")
            
            # Connect to both databases
            source_conn = sqlite3.connect(source_db_path)
            target_conn = sqlite3.connect(target_db)
            
            source_cursor = source_conn.cursor()
            target_cursor = target_conn.cursor()
            
            # Get all tables from source
            source_cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in source_cursor.fetchall()]
            
            merged_count = 0
            
            for table in tables:
                if table.startswith('sqlite_'):
                    continue
                
                try:
                    # Get all rows from source table
                    source_cursor.execute(f"SELECT * FROM {table}")
                    rows = source_cursor.fetchall()
                    
                    if not rows:
                        continue
                    
                    # Get column names
                    source_cursor.execute(f"PRAGMA table_info({table})")
                    columns = [col[1] for col in source_cursor.fetchall()]
                    
                    if not columns:
                        continue
                    
                    # Insert or replace into target
                    placeholders = ','.join(['?' for _ in columns])
                    columns_str = ','.join(columns)
                    
                    for row in rows:
                        try:
                            target_cursor.execute(
                                f"INSERT OR REPLACE INTO {table} ({columns_str}) VALUES ({placeholders})",
                                row
                            )
                            merged_count += 1
                        except Exception as e:
                            pass
                            
                except Exception as e:
                    log(f"Error merging ViewModes table {table}: {e}", xbmc.LOGWARNING)
            
            target_conn.commit()
            source_conn.close()
            target_conn.close()
            
            log(f"Merged {merged_count} ViewModes entries from source database")
            return True
            
        except Exception as e:
            log(f"Error merging ViewModes databases: {e}", xbmc.LOGERROR)
            return False

    def extract_and_merge_skin(self, zip_path, progress_dialog, title="מתקין סקין..."):
        """Extract skin ZIP and merge its database with existing one"""
        log(f"Extracting skin with database merge: {zip_path}")
        
        try:
            zin = zipfile.ZipFile(zip_path, 'r', allowZip64=True)
        except Exception as e:
            log(f"Error opening zip: {e}", xbmc.LOGERROR)
            return False, 0
        
        files = zin.namelist()
        total = len(files)
        errors = 0
        extracted = 0
        
        # Check if ZIP contains database files
        addons_db_in_zip = None
        viewmodes_db_in_zip = None
        for f in files:
            if 'Database' in f and f.endswith('.db'):
                if 'Addons' in f:
                    addons_db_in_zip = f
                    log(f"Found Addons database in skin ZIP: {addons_db_in_zip}")
                elif 'ViewModes' in f:
                    viewmodes_db_in_zip = f
                    log(f"Found ViewModes database in skin ZIP: {viewmodes_db_in_zip}")
        
        progress_dialog.update(0, f"[COLOR yellow]{title}[/COLOR]")
        
        # Merge Addons database
        if addons_db_in_zip:
            try:
                temp_db = os.path.join(TEMP_FOLDER, 'skin_addons.db')
                try:
                    os.remove(temp_db)
                except Exception:
                    pass
                
                # Extract just the database file
                with zin.open(addons_db_in_zip) as source:
                    with open(temp_db, 'wb') as target:
                        target.write(source.read())
                
                # Merge it
                progress_dialog.update(10, "[COLOR yellow]ממזג מסד נתונים...[/COLOR]")
                self.merge_addon_databases(temp_db)
                
                try:
                    os.remove(temp_db)
                except Exception:
                    pass
                    
            except Exception as e:
                log(f"Error extracting/merging database: {e}", xbmc.LOGWARNING)
        
        # Merge ViewModes database
        if viewmodes_db_in_zip:
            try:
                temp_db = os.path.join(TEMP_FOLDER, 'skin_viewmodes.db')
                try:
                    os.remove(temp_db)
                except Exception:
                    pass
                
                # Extract just the database file
                with zin.open(viewmodes_db_in_zip) as source:
                    with open(temp_db, 'wb') as target:
                        target.write(source.read())
                
                # Merge it
                progress_dialog.update(15, "[COLOR yellow]ממזג הגדרות תצוגה...[/COLOR]")
                self.merge_viewmodes_database(temp_db)
                
                try:
                    os.remove(temp_db)
                except Exception:
                    pass
                    
            except Exception as e:
                log(f"Error extracting/merging ViewModes database: {e}", xbmc.LOGWARNING)
        
        # Now extract all other files (skip database). ALSO skip the bundle's
        # guisettings snapshot: on a no-wipe skin install it would overwrite
        # the LIVE install's global Kodi settings (colours, prefs, everything)
        # with the state of whatever machine the bundle was packed on -- the
        # config policy owns guisettings, not skin bundles.
        _SKIP_MERGE_FILES = ('userdata/guisettings.xml', 'userdata\\guisettings.xml')
        for i, item in enumerate(zin.infolist()):
            filename = item.filename
            
            # Skip database files (we already merged)
            if 'Database' in filename and filename.endswith('.db'):
                continue

            if filename in _SKIP_MERGE_FILES:
                continue

            # The bundle's harvested SKIN settings snapshot (whatever state the
            # packing machine was in -- e.g. the January blue accent) must not
            # override the build's curated defaults: the CONFIG owns skin
            # settings. Everything else in addon_data (skinshortcuts menus
            # etc.) still extracts.
            _norm = filename.replace('\\', '/')
            if _norm.startswith('userdata/addon_data/skin.') and _norm.endswith('/settings.xml'):
                continue

            if ADDON_ID in filename:
                continue
            
            if '__pycache__' in filename or filename.endswith('.pyc') or filename.endswith('.pyo'):
                continue
            
            if filename.endswith('.csv'):
                continue
            
            try:
                filename.encode('ascii')
            except Exception:
                continue
            
            try:
                zin.extract(item, HOME)
                extracted += 1
            except Exception as e:
                errors += 1
            
            if i % 50 == 0:
                pct = 10 + int(i * 90 / total)
                progress_dialog.update(pct, f"[COLOR yellow]{title}[/COLOR]\n{extracted}/{total} קבצים")
        
        zin.close()

        # Report FAILURE on ANY extraction error. A partially-extracted skin must
        # not be set as the default -- a single missing critical file (addon.xml,
        # the primary skin XML, a required module) breaks the skin, and there's no
        # cheap way here to tell critical from optional. Since Estuary is a safe
        # fallback, zero tolerance is the correct trade (a stray failure just means
        # the user keeps Estuary, not a broken skin).
        ok = (errors == 0)
        log(f"Skin extraction complete. Extracted: {extracted}, Errors: {errors}, ok={ok}")
        return ok, errors

    def extract_zip(self, zip_path, dest, progress_dialog, title="מחלץ..."):
        """Extract ZIP to destination"""
        log(f"Extracting: {zip_path} to {dest}")
        
        try:
            zin = zipfile.ZipFile(zip_path, 'r', allowZip64=True)
        except Exception as e:
            log(f"Error opening zip: {e}", xbmc.LOGERROR)
            return False, 0
        
        files = zin.namelist()
        total = len(files)
        errors = 0
        extracted = 0
        critical_failed = []

        progress_dialog.update(0, f"[COLOR yellow]{title}[/COLOR]")
        
        # skip the wizard's own ADDON CODE (addons/<id>/) so the running wizard
        # isn't overwritten mid-install, and its stale harvested settings.xml --
        # but DO extract userdata/addon_data/<id>/applied_manifest.json: that's
        # the state SEED that lets the post-install completion skip addons the
        # base zip already carries (the old blanket `ADDON_ID in filename` also
        # matched the seed -> discarded -> completion re-downloaded the WHOLE
        # build every install).
        _skip_code = ('addons/%s/' % ADDON_ID, 'addons\\%s\\' % ADDON_ID)
        bundle_addons_db = None       # bundle's Addons DB -> MERGED, never extracted raw
        for i, item in enumerate(zin.infolist()):
            filename = item.filename

            if filename.startswith(_skip_code):
                continue

            if ADDON_ID in filename and not filename.endswith('applied_manifest.json'):
                continue

            # The bundle's Addons registry must NOT be extracted over the LIVE
            # Addons33.db: Kodi holds that file open, and replacing it makes every
            # later write fail with SQLITE_READONLY_DBMOVED (nothing gets enabled,
            # the box boots broken -- the 2026-07-30 Android reinstall bug). The
            # wipe now preserves the live file; the bundle's rows are merged into
            # it (by addonID) after extraction, exactly like the skin path does.
            _norm_db = filename.replace('\\', '/')
            if ('/Database/' in _norm_db and _norm_db.endswith('.db')
                    and os.path.basename(_norm_db).startswith('Addons')):
                bundle_addons_db = filename
                continue

            if '__pycache__' in filename or filename.endswith('.pyc') or filename.endswith('.pyo'):
                continue

            if filename.endswith('.csv'):
                continue

            try:
                zin.extract(item, dest)
                extracted += 1
            except Exception as e:
                errors += 1
                _n = filename.replace('\\', '/').lower()
                if _n.endswith('userdata/guisettings.xml') or _n.endswith('/addon.xml'):
                    critical_failed.append(filename)
                    log(f"CRITICAL extract failure: {filename}: {e}", xbmc.LOGERROR)

            if i % 50 == 0:
                pct = int(i * 100 / total)
                progress_dialog.update(pct, f"[COLOR yellow]{title}[/COLOR]\n{extracted}/{total} קבצים")
        
        # Deliver the bundle's Addons registry into the LIVE db (see skip above):
        # merge by addonID -- keeps Kodi's open handle valid (no DBMOVED), brings
        # in the bundle's enabled/origin/update_rules pinning rows. Raw-extract
        # only if no live db exists at all (first-ever run before Kodi created one).
        if bundle_addons_db:
            try:
                _dbdir = xbmcvfs.translatePath('special://database/')
                _has_live = any(f.startswith('Addons') and f.endswith('.db')
                                for f in os.listdir(_dbdir)) if os.path.isdir(_dbdir) else False
                if _has_live:
                    _tmp = os.path.join(TEMP_FOLDER, 'bundle_addons.db')
                    try:
                        os.remove(_tmp)
                    except Exception:
                        pass
                    with zin.open(bundle_addons_db) as _src, open(_tmp, 'wb') as _dst:
                        _dst.write(_src.read())
                    self.merge_addon_databases(_tmp)
                    try:
                        os.remove(_tmp)
                    except Exception:
                        pass
                    log("bundle Addons db MERGED into live registry (not extracted raw)")
                else:
                    zin.extract(bundle_addons_db, dest)
                    log("no live Addons db -- bundle registry extracted as-is")
            except Exception as e:
                errors += 1
                log(f"bundle Addons db merge failed: {e}", xbmc.LOGERROR)

        zin.close()

        # Drop STALE registry rows: the live db still carries the PREVIOUS build's
        # addons (their files were just wiped). An enabled row whose addon no
        # longer exists trips Kodi's dependency cascade at boot (it re-disables
        # dependents) -- that ghost-cascade is what kept un-breaking fixes on the
        # 2026-07-30 Xiaomi. Remove every row with no matching addon on disk
        # (home/addons) and not a system addon (special://xbmc/addons).
        if bundle_addons_db:
            self._reconcile_addons_db()

        log(f"Extraction complete. Extracted: {extracted}, Errors: {errors}")
        # A handful of per-file errors (locked thumbnail etc.) is survivable;
        # a burst means disk-full/permissions AFTER the wipe already ran --
        # reporting success there ends with a "complete" install on a gutted
        # box. Fail loudly instead so the caller shows the error path.
        # A CRITICAL file (guisettings.xml / an addon.xml) failing aborts
        # REGARDLESS of the count -- one corrupt guisettings.xml is a broken
        # install even if it's the only error, and the count threshold used to
        # wave it through as success.
        if critical_failed:
            log(f"Extraction FAILED: {len(critical_failed)} critical file(s) "
                f"corrupt e.g. {critical_failed[:3]}", xbmc.LOGERROR)
            return False, errors
        if errors and (extracted == 0 or errors >= max(10, total // 50)):
            log(f"Extraction FAILED: {errors} errors out of {total} entries", xbmc.LOGERROR)
            return False, errors
        return True, errors

    def set_default_skin(self, skin_id):
        """Set the default skin in guisettings.xml.

        CREATES a minimal guisettings.xml when it doesn't exist yet. It used to
        just log "not found" and return False -- which silently LOST the user's
        skin choice on every POV install: the Gears bundle ships a guisettings.xml
        but the POV bundle does not, so on POV + any non-default skin (AF3 /
        Nimbus / Zephyr) nothing was written here, Kodi later recreated the file
        with its own default, and the box booted ESTUARY. That is exactly the
        "בחרתי Zephyr וקיבלתי Estuary" symptom from the Shield/Xiaomi incident,
        reproduced on-device 2026-07-30 (POV+AF3). The config's guisettings entry
        is merge_id/merge_seed, so the value written here is preserved, not
        clobbered, when the config applies later in the install."""
        try:
            guisettings = os.path.join(USERDATA, 'guisettings.xml')
            if not os.path.exists(guisettings):
                log("guisettings.xml missing -- creating it so the skin choice sticks",
                    xbmc.LOGWARNING)
                try:
                    os.makedirs(USERDATA, exist_ok=True)
                    with open(guisettings, 'w', encoding='utf-8') as f:
                        f.write('<settings version="2">\n'
                                '    <setting id="lookandfeel.skin">%s</setting>\n'
                                '</settings>\n' % skin_id)
                    log(f"created guisettings.xml with skin {skin_id}")
                    self.set_skin_font(skin_id)
                    return True
                except Exception as e:
                    log(f"could not create guisettings.xml: {e}", xbmc.LOGERROR)
                    return False

            with open(guisettings, 'r', encoding='utf-8') as f:
                content = f.read()
            
            import re
            content = re.sub(
                r'(<setting id="lookandfeel.skin"[^>]*>)[^<]*(</setting>)',
                rf'\g<1>{skin_id}\g<2>',
                content
            )
            
            content = re.sub(
                r'(<setting id="lookandfeel.skin"[^>]*) default="[^"]*"',
                r'\1',
                content
            )
            
            with open(guisettings, 'w', encoding='utf-8') as f:
                f.write(content)

            log(f"Set default skin to: {skin_id}")
            # Also set the correct Hebrew fontset for the target skin. lookandfeel.font
            # is GLOBAL, so switching skins keeps the previous value -- and AF3's
            # "Default" fontset is Latin-only (Hebrew renders as tofu). Each skin needs
            # its Hebrew-capable fontset here.
            self.set_skin_font(skin_id)
            return True

        except Exception as e:
            log(f"Error setting default skin: {e}", xbmc.LOGERROR)
            return False

    # Hebrew-capable fontset per skin. Estuary/Nimbus "Default" already map to a
    # Hebrew font; AF3's "Default" is Latin-only so it must use "Hebrew (Rubik)".
    SKIN_FONTSET = {
        # All build skins default to the Rubik Hebrew fontset (nicest Hebrew UI
        # face). Each skin also ships Hebrew (Noto)/(Assistant)/(Heebo) fontsets
        # the user can pick from Skin settings -> Fonts. (Zephyr's plot boxes use
        # font_plotbox=Noto in Defaults.xml regardless, so plots always render.)
        'skin.arctic.fuse.3': 'Hebrew (Rubik)',
        'skin.arctic.zephyr.2.resurrection.mod': 'Hebrew (Rubik)',
        'skin.estuary': 'Hebrew (Rubik)',
        'skin.nimbus': 'Hebrew (Rubik)',
    }

    def set_skin_font(self, skin_id):
        """Force the target skin's Hebrew fontset into guisettings (lookandfeel.font
        is global, so a skin switch would otherwise keep a font with no Hebrew)."""
        fontset = self.SKIN_FONTSET.get(skin_id, 'Default')
        try:
            guisettings = os.path.join(USERDATA, 'guisettings.xml')
            if not os.path.exists(guisettings):
                return False
            import re
            with open(guisettings, 'r', encoding='utf-8') as f:
                content = f.read()
            if re.search(r'<setting id="lookandfeel.font"', content):
                content = re.sub(r'(<setting id="lookandfeel.font"[^>]*>)[^<]*(</setting>)',
                                 lambda m: m.group(1) + fontset + m.group(2), content, count=1)
                content = re.sub(r'(<setting id="lookandfeel.font"[^>]*) default="[^"]*"', r'\1', content)
            else:
                content = content.replace('</settings>',
                                          '    <setting id="lookandfeel.font">%s</setting>\n</settings>' % fontset, 1)
            with open(guisettings, 'w', encoding='utf-8') as f:
                f.write(content)
            log(f"Set skin font to '{fontset}' for {skin_id}")
            return True
        except Exception as e:
            log(f"set_skin_font error: {e}", xbmc.LOGERROR)
            return False

    def is_build_installed(self):
        """Check if a build is already installed"""
        build_name = ADDON.getSetting('buildname')
        return build_name and build_name != ''
    
    def get_installed_build_name(self):
        """Get the name of the installed build"""
        return ADDON.getSetting('buildname') or ''
    
    def get_installed_skin(self):
        """Get the installed skin"""
        return ADDON.getSetting('installed_skin') or 'Estuary'

    # Optional skins the build can switch to. Estuary is the baked-in default.
    # url_key = the field name in build_info (from build.txt) holding the zip URL.
    OPTIONAL_SKINS = {
        # AF3/Nimbus: Omega installs the one-zip CI bundle (url_key); on Piers
        # the bundle carries gui-5.17 skins + skinshortcuts 2.0.3 which CANNOT
        # load on Kodi 22 -- manifest_install routes Piers to manifest-piers
        # (gui-5.18 overlays), same gate Zephyr uses. deps = the skin's full
        # import closure inside the manifest (verified against manifest-piers).
        'arctic': {'id': 'skin.arctic.fuse.3', 'name': 'Arctic Fuse',
                   'url_key': 'skin_url', 'zip': 'arctic_fuse.zip',
                   'manifest_install': True,
                   'deps': ['script.skinvariables', 'script.texturemaker',
                            'plugin.video.themoviedb.helper',
                            'script.module.jurialmunkey', 'script.module.infotagger',
                            'script.module.addon.signals', 'script.module.qrcode',
                            'script.module.requests', 'script.module.urllib3',
                            'script.module.certifi', 'script.module.chardet',
                            'script.module.idna', 'script.module.six',
                            'resource.images.weathericons.white',
                            'resource.images.studios.coloured',
                            'resource.font.robotocjksc']},
        'nimbus': {'id': 'skin.nimbus', 'name': 'Nimbus',
                   'url_key': 'nimbus_skin_url', 'zip': 'nimbus.zip',
                   'manifest_install': True,
                   'deps': ['script.nimbus.helper', 'script.module.requests',
                            'script.module.urllib3', 'script.module.certifi',
                            'script.module.chardet', 'script.module.idna',
                            'script.module.six']},
        # Zephyr's deps aren't bundled in a single build.txt zip like AF3/Nimbus,
        # so it installs the skin + its deps straight from the manifest.
        'zephyr': {'id': 'skin.arctic.zephyr.2.resurrection.mod', 'name': 'Arctic Zephyr',
                   # Omega: fast one-zip install (CI-built bundle, never stale).
                   # Piers: manifest_install fallback (bundle carries OMEGA
                   # gui-5.17 skins + skinshortcuts 2.0.3 - wrong for Kodi 22).
                   'url_key': 'zephyr_skin_url', 'zip': 'zephyr_skin.zip',
                   'manifest_install': True,
                   'deps': ['script.skinshortcuts', 'script.skinhelper',
                            'script.module.simplejson', 'script.module.unidecode',
                            'script.module.simpleeval',
                            'script.skinvariables', 'plugin.video.themoviedb.helper',
                            # TMDbHelper's own module deps -- WITHOUT these its
                            # service crashes ("No module named 'jurialmunkey'").
                            'script.module.jurialmunkey', 'script.module.infotagger',
                            'script.module.addon.signals', 'script.module.qrcode',
                            # qrcode hard-requires six -- without it a fresh
                            # Zephyr install crashes until the next update pass
                            'script.module.six',
                            'resource.images.studios.white',
                            'resource.images.moviegenreicons.transparent',
                            'resource.images.moviecountryicons.maps',
                            'resource.images.weathericons.white']},
        # Added 2026-08-28. No CI bundle exists for Rounded, so there is no
        # url_key -- skin_zip_url stays None and the manifest_install path is
        # taken on both fleets (the lookup at the call site is guarded with
        # skin.get('url_key'), so omitting it is supported).
        # Kodi refuses to ENABLE an addon whose <import> is unmet, so deps is
        # the skin's full import closure plus the transitive module deps of
        # skinshortcuts and TMDb Helper -- every one verified present in
        # addons/. TMDbHelper without jurialmunkey/infotagger/addon.signals/
        # qrcode/six crashes its service on a fresh install.
        # Piers is deliberately absent: Rounded's Kodi-22 build lives in a
        # different repo on skinshortcuts 3.x and its author warns 2.x menus
        # cannot migrate, so it needs its own Hebrew menu set first.
        'rounded': {'id': 'skin.arctic.zephyr.rounded', 'name': 'Arctic Zephyr Rounded',
                    'manifest_install': True,
                    'deps': ['script.skinshortcuts', 'script.skin.info.service',
                             'script.skinvariables', 'plugin.video.themoviedb.helper',
                             'script.wikipedia', 'script.artistslideshow',
                             'script.globalsearch', 'script.image.resource.select',
                             'script.module.simplejson', 'script.module.unidecode',
                             'script.module.simpleeval',
                             'script.module.jurialmunkey', 'script.module.infotagger',
                             'script.module.addon.signals', 'script.module.qrcode',
                             'script.module.six',
                             'resource.images.recordlabels.white',
                             'resource.images.studios.coloured',
                             'resource.images.moviecountryicons.flags',
                             'resource.images.weathericons.white',
                             'resource.images.weatherfanart.single']},
        # skin.bingie is NOT listed yet on purpose: its plugin.video.tmdb.bingie
        # .helper hard-imports script.module.pil (not optional="true"), which we
        # do not vendor -- Kodi would refuse to enable the helper and the skin
        # would install broken. The Python only uses PIL lazily for optional
        # image effects, so the fix is either to vendor PIL or to drop that
        # import in an overlay (as was done for the PIL-free skinhelper), which
        # makes the addon a modified dep and takes it off auto-update.
    }

    def install_build(self, build_info, skin_choice='estuary', with_arctic_fuse=None,
                      keep_keys=None, keep_extras=None, content_choice='gears'):
        """Full build installation. skin_choice: 'estuary' | 'arctic' | 'nimbus'.
        keep_keys: list of 'keep' group keys to carry across the wipe (see keep.py).
        keep_extras: user-installed addon ids to preserve if 'extras' is kept."""
        # Back-compat: older callers pass with_arctic_fuse=True/False.
        if with_arctic_fuse is not None:
            skin_choice = 'arctic' if with_arctic_fuse else 'estuary'
        progress = xbmcgui.DialogProgress()
        progress.create(ADDON_NAME, "[COLOR cyan]מתחיל התקנה...[/COLOR]")

        build_name = build_info.get('name', 'Unknown')
        skin = self.OPTIONAL_SKINS.get(skin_choice)
        skin_name = skin['name'] if skin else "Estuary"
        
        try:
            # Set skip flag for service (don't show update dialog during install)
            ADDON.setSetting('skip_update_check', 'true')
            # Re-arm the "connect a service?" offer for the boot after this
            # install. The wipe deliberately PRESERVES the wizard's addon_data,
            # so a flag written on the old build would survive into the new one
            # and swallow the question exactly when the box has no credentials
            # (the same trap that made the seeds marker-gated bug). Clearing it
            # here keeps the offer to ONE ask per install.
            ADDON.setSetting('services_prompt_done', 'false')

            # Record the content source NOW, before any config apply, so the
            # whole install is content-aware (the config engine skips Gears-
            # specific entries when POV is chosen). BUT remember the previous
            # value: if the install fails after this point, we revert it, so a
            # failed/cancelled install can't leave the box flagged for a content
            # source it isn't actually running (the final value is re-committed
            # at the end on success).
            try:
                _prev_content_source = ADDON.getSetting('content_source') or 'gears'
            except Exception:
                _prev_content_source = 'gears'
            try:
                ADDON.setSetting('content_source', content_choice)
            except Exception:
                pass

            # Prepare destination
            if not os.path.exists(TEMP_FOLDER):
                os.makedirs(TEMP_FOLDER)
            
            filename = build_info['url'].split('/')[-1]
            if not filename.endswith('.zip'):
                filename = 'build.zip'
            zip_path = os.path.join(TEMP_FOLDER, filename)
            
            try:
                os.remove(zip_path)
            except Exception:
                pass
            
            # Step 1: Download base build. POV chosen -> download the CLEAN POV
            # base bundle (POV closure, no Gears/scrapers), NOT the Gears bundle.
            # The POV bundle URL is the pov_url from build.txt, else derived from
            # the Gears url (FenLight_Estuary.zip -> POV_Estuary.zip), same
            # base-builds release CI uploads it to.
            base_url = build_info['url']
            if content_choice == 'pov':
                base_url = (build_info.get('pov_url')
                            or base_url.replace('FenLight_Estuary.zip', 'POV_Estuary.zip'))
            progress.update(0, f"[COLOR yellow]מוריד בילד {build_name}...[/COLOR]")
            success = self.download_file(base_url, zip_path, progress, f"[COLOR yellow]מוריד בילד {build_name}...[/COLOR]")
            
            if not success or not os.path.exists(zip_path) or os.path.getsize(zip_path) == 0:
                progress.close()
                try:
                    os.remove(zip_path)
                except Exception:
                    pass
                self.dialog.ok(ADDON_NAME, f"[COLOR {COLOR_ERROR}]ההורדה נכשלה![/COLOR]")
                try: ADDON.setSetting('content_source', _prev_content_source)   # box untouched -> revert
                except Exception: pass
                return False
            
            # Step 2: Validate the archive BEFORE touching the user's build, then
            # get the addon list. Order matters: everything below this point is
            # destructive, so a bad download must abort HERE.
            progress.update(0, "[COLOR yellow]בודק תקינות הקובץ...[/COLOR]")
            zip_ok, zip_err = self.validate_build_zip(zip_path)
            if not zip_ok:
                progress.close()
                try:
                    os.remove(zip_path)
                except Exception:
                    pass
                log(f"Build zip validation FAILED: {zip_err}", xbmc.LOGERROR)
                self.dialog.ok(ADDON_NAME,
                               f"[COLOR {COLOR_ERROR}]ההורדה נכשלה או שהקובץ פגום.[/COLOR]\n"
                               f"{zip_err}\n\n"
                               "הבילד הקיים לא נפגע. נסו שוב.")
                try: ADDON.setSetting('content_source', _prev_content_source)   # box untouched -> revert
                except Exception: pass
                return False

            progress.update(0, "[COLOR yellow]סורק אדונים בבילד...[/COLOR]")
            addon_list = self.grab_addons_from_zip(zip_path)
            
            # Step 2.5: snapshot the user's 'keep' selections BEFORE wiping.
            # A HARD failure here (no stage dir / disk full) means the very data
            # the user ticked to keep gets destroyed by the wipe below with no
            # way back -- make that an explicit decision instead of a silent
            # loss. staged==0 is NOT an error: a box with no logins configured
            # legitimately has nothing to carry over.
            if keep_keys:
                keep_ok, keep_n = True, 0
                try:
                    from resources.libs import keep as keep_mod
                    progress.update(0, "[COLOR yellow]שומר נתונים נבחרים...[/COLOR]")
                    keep_ok, keep_n = keep_mod.backup(keep_keys, extras=keep_extras,
                                                      target_content=content_choice,
                                                      source_content=_prev_content_source)
                except Exception as e:
                    keep_ok = False
                    log(f"keep backup failed: {e}", xbmc.LOGWARNING)
                log(f"keep backup: ok={keep_ok} staged={keep_n}")
                if not keep_ok:
                    progress.close()
                    if not self.dialog.yesno(
                            ADDON_NAME,
                            f"[COLOR {COLOR_WARNING}]גיבוי הנתונים שבחרתם נכשל.[/COLOR]\n\n"
                            "אם תמשיכו, ההתחברויות והנתונים שסימנתם יימחקו "
                            "ולא ניתן יהיה לשחזר אותם.\n\n"
                            "להמשיך בכל זאת?",
                            yeslabel="המשך", nolabel="[B]בטל[/B]"):
                        log("install aborted by user after keep-backup failure")
                        try: ADDON.setSetting('content_source', _prev_content_source)   # box untouched -> revert
                        except Exception: pass
                        return False
                    progress.create(ADDON_NAME, "[COLOR cyan]ממשיך בהתקנה...[/COLOR]")

            # Step 3: Wipe
            progress.update(0, "[COLOR yellow]מכין להתקנה...[/COLOR]")
            wipe_failed = self.wipe(progress)
            if wipe_failed:
                # locked files survived the wipe (Windows) -> the build is about to
                # extract over a partially-retained old install. Don't silently call
                # it a clean success; log loudly so a mixed install is diagnosable.
                log(f"wipe left {wipe_failed} undeletable file(s); build will extract "
                    f"over them -- install may be partially mixed", xbmc.LOGWARNING)

            # Step 4: Extract base build
            progress.update(0, f"[COLOR yellow]מתקין {build_name}...[/COLOR]")
            success, errors = self.extract_zip(zip_path, HOME, progress, f"מתקין {build_name}...")

            if not success:
                progress.close()
                self.dialog.ok(ADDON_NAME, f"[COLOR {COLOR_ERROR}]ההתקנה נכשלה![/COLOR]")
                return False

            # Cleanup base zip
            try:
                os.remove(zip_path)
            except Exception:
                pass

            # Step 4.5: Ask about OLED and apply settings
            self._ask_and_apply_oled(progress)
            progress.create(ADDON_NAME, "[COLOR cyan]ממשיך בהתקנה...[/COLOR]")

            # Step 4.6: restore the 'keep' selections NOW -- after the base build is
            # extracted but BEFORE any addon is registered/enabled/started. The skin
            # install below can be a MANIFEST install (Zephyr/Piers) that itself
            # enables addons and runs UpdateLocalAddons(); doing the restore only
            # before Step 6 (as 2.4.135 did) still left the kept sqlite files being
            # overwritten while a service started by that earlier path could hold
            # them open (WAL corruption). Restoring here precedes EVERY registration
            # path. Restored user addons are folded into addon_list for the single
            # enable at Step 6.
            if keep_keys:
                try:
                    from resources.libs import keep as keep_mod
                    progress.update(0, "[COLOR yellow]משחזר נתונים שנשמרו...[/COLOR]")
                    restored_extras, restore_failed = keep_mod.restore()
                    if restored_extras:
                        addon_list.extend(a for a in restored_extras if a not in addon_list)
                    if restore_failed:
                        try:
                            progress.close()
                        except Exception:
                            pass
                        self.dialog.ok(ADDON_NAME,
                            f"[COLOR {COLOR_WARNING}]חלק מהנתונים ששמרת לא שוחזרו "
                            f"({restore_failed} פריטים).[/COLOR]\n\n"
                            "עותק הגיבוי לא נמחק וניתן לשחזר ממנו ידנית:\n"
                            f"{keep_mod.STAGE}")
                        progress.create(ADDON_NAME, "[COLOR cyan]ממשיך בהתקנה...[/COLOR]")
                except Exception as e:
                    log(f"keep restore failed: {e}", xbmc.LOGWARNING)
            
            # Step 5: Install the chosen optional skin (Arctic Fuse / Nimbus / Zephyr)
            skin_zip_url = build_info.get(skin['url_key']) if (skin and skin.get('url_key')) else None
            if skin and skin.get('manifest_install') and (_kodi_major() >= 22 or not skin_zip_url):
                # Zephyr installs from the manifest (skin + its own deps), not from a
                # bundled build.txt zip like AF3/Nimbus. _install_from_manifest also
                # enables the addons and applies our config (skin defaults + view rebuild).
                progress.update(0, f"[COLOR yellow]מתקין {skin['name']}...[/COLOR]")
                if self._install_from_manifest(skin['id'], skin.get('deps', []), skin['name']):
                    self.set_default_skin(skin['id'])
                    ADDON.setSetting('installed_skin', skin['name'])
                else:
                    # authoritative fallback (same as the ZIP branch): reset skin +
                    # skin_name so stack-sync, the POV variant target and the restart
                    # label all use Estuary, not the manifest skin that never
                    # installed. Kodi 22 always takes THIS branch, so without the
                    # reset a failed Zephyr/Piers install kept driving later steps.
                    log(f"manifest skin {skin['name']} install failed; using Estuary",
                        xbmc.LOGWARNING)
                    skin = None
                    skin_name = "Estuary"
                    ADDON.setSetting('installed_skin', 'Estuary')
            elif skin and skin_zip_url:
                # Small delay between downloads to avoid GitHub rate limiting
                xbmc.sleep(2000)

                dl_msg = f"[COLOR yellow]מוריד סקין {skin['name']}...[/COLOR]"
                progress.update(0, dl_msg)

                skin_zip = os.path.join(TEMP_FOLDER, skin['zip'])
                try:
                    os.remove(skin_zip)
                except Exception:
                    pass

                success = self.download_file(skin_zip_url, skin_zip, progress, dl_msg)

                # verify the downloaded skin zip is INTACT before extracting. A
                # corrupt/truncated skin download used to extract PARTIALLY and
                # still be set as the default skin, leaving a broken skin on the
                # box. validate_build_zip runs a full testzip() (+ critical-file
                # CRC); on any failure we fall back to Estuary and never set the
                # broken skin as default.
                if success and os.path.exists(skin_zip) and os.path.getsize(skin_zip) > 0:
                    skin_ok, skin_why = self.validate_build_zip(skin_zip, expected_addon_id=skin['id'])
                else:
                    skin_ok, skin_why = False, 'download failed'

                if skin_ok:
                    progress.update(0, f"[COLOR yellow]מתקין {skin['name']}...[/COLOR]")
                    skin_addons = self.grab_addons_from_zip(skin_zip)
                    # Use special extraction that merges database
                    ok_extract, _ = self.extract_and_merge_skin(skin_zip, progress, f"מתקין {skin['name']}...")
                    if ok_extract:
                        addon_list.extend(skin_addons)
                        self.set_default_skin(skin['id'])
                        ADDON.setSetting('installed_skin', skin['name'])
                        # don't re-download what the bundle just delivered
                        self._seed_state_from_manifest(skin_addons)
                        # Same post-skin-install config the switch-flow does:
                        # applies skin defaults, seeds gears views/shortcuts,
                        # and ARMS pending_view_rebuild so the first boot does
                        # the one clean includes-rebuild (without it the new
                        # skin self-builds with no_reload -> frozen home).
                        # Also records __config__ so step 8 won't re-apply the
                        # config in fresh mode over the choices made here.
                        self._apply_build_config(skin['id'])
                    else:
                        skin_ok, skin_why = False, 'extraction failed'
                    try:
                        os.remove(skin_zip)
                    except Exception:
                        pass

                if not skin_ok:
                    # ANY optional-skin failure (download / CRC / extraction) must
                    # fall back AUTHORITATIVELY to Estuary: reset skin + skin_name
                    # too, or later steps (stack sync, POV variant target, restart
                    # label) keep using the skin that never actually installed.
                    log(f"optional skin unavailable/invalid ({skin_why}); using Estuary",
                        xbmc.LOGWARNING)
                    try:
                        os.remove(skin_zip)
                    except Exception:
                        pass
                    skin = None
                    skin_name = "Estuary"
                    ADDON.setSetting('installed_skin', 'Estuary')
            else:
                ADDON.setSetting('installed_skin', 'Estuary')

            # (keep-restore now runs at Step 4.6, before any addon registration.)

            # Step 6: Enable addons in database
            progress.update(90, "[COLOR yellow]מפעיל אדונים...[/COLOR]")
            self.enable_addons_in_db(addon_list)
            self.setup_wizard_repo_in_db()

            # Step 7: Update
            progress.update(95, "[COLOR yellow]מעדכן...[/COLOR]")
            xbmc.executebuiltin('UpdateAddonRepos()')
            xbmc.executebuiltin('UpdateLocalAddons()')

            # Step 8: Complete the build from the manifest BEFORE we exit, so the
            # first re-launch already shows our full defaults. The base zip ships
            # STOCK skins (e.g. vanilla Estuary); our MODIFIED skins -- the power
            # menu, home arrangement, skin-switch button -- and the config live in
            # the manifest. Applying it now (while the user is already waiting on
            # the install) means re-entry is complete, with no extra restart.
            completion_incomplete = False
            try:
                progress.update(97, "[COLOR yellow]מחיל את ברירות המחדל של הבילד...[/COLOR]")
                from resources.libs import modular_update as mu
                # _run_update_impl, not run_update: we're inside the install (the
                # background service is already held off by skip_update_check) and
                # this completion MUST run -- routing through the lock-guarded
                # run_update could see the lock briefly held and skip it.
                # Most failures are RETURNED as {'ok': False}, not raised, so the
                # except below wouldn't catch them -- inspect the summary too, or
                # a manifest server going down after the wipe would be reported as
                # a clean install of an INCOMPLETE build.
                _summary = mu._run_update_impl(silent=True, no_reload=True)
                if isinstance(_summary, dict) and _summary.get('ok') is False:
                    completion_incomplete = True
                    log(f"post-install manifest completion incomplete: {_summary}", xbmc.LOGWARNING)
            except Exception as e:
                completion_incomplete = True
                log(f"post-install manifest completion failed: {e}", xbmc.LOGWARNING)

            # Save build info
            # step 8 installed the FULL manifest (incl. other skins' stacks,
            # enabled) -- align enablement to the chosen skin's stack
            try:
                self.sync_skin_stacks(skin['id'] if skin else 'skin.estuary')
            except Exception as e:
                log(f"post-install stack sync failed: {e}", xbmc.LOGWARNING)
            ADDON.setSetting('buildname', build_name)
            ADDON.setSetting('buildversion', build_info.get('version', '1.0'))

            # Content source: a POV install downloaded the CLEAN POV bundle, so
            # the box IS POV regardless of whether the variant menus applied. Use
            # _apply_pov_core (which never flips the source) and then record POV
            # explicitly -- install_apply would have DOWNGRADED to 'gears' on a
            # variant failure, leaving a POV-closure box wrongly flagged Gears
            # (with no Gears installed). A failed variant just needs a later
            # re-apply of the menus, not a source change.
            if content_choice == 'pov':
                try:
                    from resources.libs import content_source
                    target_skin = skin['id'] if skin else 'skin.estuary'
                    progress.update(98, "[COLOR yellow]מחיל מקור תוכן POV...[/COLOR]")
                    ok, err = content_source._apply_pov_core(target_skin)
                    if not ok:
                        log(f"install POV variant apply failed (box stays POV): {err}",
                            xbmc.LOGWARNING)
                except Exception as e:
                    log(f"install POV apply failed: {e}", xbmc.LOGWARNING)
                try:
                    import xbmcaddon as _xa
                    _xa.Addon().setSetting('content_source', 'pov')
                except Exception:
                    pass
            else:
                try:
                    import xbmcaddon as _xa
                    _xa.Addon().setSetting('content_source', 'gears')
                except Exception:
                    pass

            # Reconcile the registry ONE MORE TIME at the very end: extract-time
            # reconcile can't see what Step 8 changed (manifest completion removes
            # addons dropped from the build, and the bundle's seed DB carries rows
            # for addons this device doesn't have). Observed on-device: 22 ghost
            # rows survived to the end of a clean install. Harmless on this boot,
            # but an enabled row for a missing addon is exactly what feeds Kodi's
            # dependency cascade later.
            try:
                self._reconcile_addons_db()
            except Exception as e:
                log(f"final addons-db reconcile failed: {e}", xbmc.LOGWARNING)

            # Create first-run marker (so wizard won't auto-launch again)
            try:
                home_path = xbmcvfs.translatePath('special://home/')
                marker_path = os.path.join(home_path, '.masterkodi_il_done')
                with open(marker_path, 'w') as f:
                    f.write(f'{build_name}')
                log(f"Created first-run marker: {marker_path}")
            except Exception as e:
                log(f"Could not create marker: {e}")
            
            progress.update(100, "[COLOR lime]ההתקנה הושלמה![/COLOR]")
            xbmc.sleep(500)
            progress.close()

            # The base build extracted, but Step 8 (manifest completion) didn't
            # finish -- tell the user rather than showing an unqualified success.
            # It's not fatal: the next update pass completes the config/defaults.
            if completion_incomplete:
                # The service SKIPS the update check on the first boot after an
                # install (skip_update_check). That's fine when completion
                # succeeded, but here it DIDN'T -- so clear the flag, or the retry
                # wouldn't run until the SECOND boot. With it cleared the very next
                # boot runs the update and completes the config.
                try:
                    ADDON.setSetting('skip_update_check', 'false')
                except Exception:
                    pass
                self.dialog.ok(ADDON_NAME,
                    f"[COLOR {COLOR_WARNING}]הבילד הותקן, אך חלק מברירות המחדל לא הושלמו "
                    f"(ייתכן שהשרת לא היה זמין).[/COLOR]\n\n"
                    "ההגדרות יושלמו אוטומטית בהפעלה הבאה של Kodi, או שניתן להריץ עדכון ידני מהאשף.")

            # Step 8.5: the display question. Deliberately AFTER Step 6/8 (the
            # addons are registered and enabled by now) -- it writes into POV's
            # settings through Kodi's addon API and into Gears' settings.db, and
            # neither exists for an addon Kodi hasn't registered yet.
            self._ask_and_apply_sdr()

            # Countdown and restart
            self._countdown_restart(build_name, skin_name)

            return True
            
        except Exception as e:
            progress.close()
            log(f"Install error: {e}", xbmc.LOGERROR)
            self.dialog.ok(ADDON_NAME, f"[COLOR {COLOR_ERROR}]שגיאה:[/COLOR] {str(e)}")
            return False

    # ------------------------------------------------------------------ #
    # Skin-stack lifecycle (Asaf's policy, 2026-07-18): the ACTIVE skin's
    # UI stack is enabled; every other skin's EXCLUSIVE stack is disabled
    # (neutralized, not removed -- switching back re-enables instantly).
    # Core stays on everywhere: gears+scrapers+magneto, the subtitle/skip
    # services, wizard/firstrun/repos, requests/urllib3/certifi/chardet/
    # idna/six, languages -- none of those may EVER appear in these sets.
    # skin.estuary is never disabled (Kodi's fallback skin).
    # ------------------------------------------------------------------ #
    SKIN_STACKS = {
        'skin.estuary': set(),
        'skin.arctic.zephyr.2.resurrection.mod': {
            'script.skinshortcuts', 'script.skinhelper',
            'script.module.simplejson', 'script.module.unidecode',
            'script.module.simpleeval', 'script.skinvariables',
            'plugin.video.themoviedb.helper', 'script.module.jurialmunkey',
            'script.module.infotagger', 'script.module.addon.signals',
            'script.module.qrcode', 'resource.images.studios.white',
            'resource.images.moviegenreicons.transparent',
            'resource.images.moviecountryicons.maps',
            'resource.images.weathericons.white'},
        'skin.arctic.fuse.3': {
            'script.skinvariables', 'script.texturemaker',
            'plugin.video.themoviedb.helper', 'script.module.jurialmunkey',
            'script.module.infotagger', 'script.module.addon.signals',
            'script.module.qrcode', 'resource.images.weathericons.white',
            'resource.images.studios.coloured', 'resource.font.robotocjksc'},
        'skin.nimbus': {'script.nimbus.helper'},
    }

    def _disable_addons_in_db(self, addon_ids):
        if not addon_ids:
            return
        try:
            import sqlite3
            db_path = xbmcvfs.translatePath('special://database/')
            best, addon_db = -1, None
            for f in os.listdir(db_path):
                if f.startswith('Addons') and f.endswith('.db'):
                    try:
                        num = int(f[len('Addons'):-len('.db')])
                    except ValueError:
                        continue
                    if num > best:
                        best, addon_db = num, os.path.join(db_path, f)
            if not addon_db:
                return
            conn = sqlite3.connect(addon_db)
            for aid in addon_ids:
                conn.execute('UPDATE installed SET enabled=0 WHERE addonID=?', (aid,))
            conn.commit(); conn.close()
        except Exception as e:
            log(f"disable_addons_in_db failed: {e}", xbmc.LOGWARNING)

    def sync_skin_stacks(self, active_skin_id):
        """Enable the active skin + its stack; disable every OTHER skin's
        exclusive stack (and the inactive optional skins themselves). Records
        the intentionally-disabled set in the wizard state so the update
        repair pass doesn't re-enable them behind our back."""
        try:
            stacks = self.SKIN_STACKS
            keep = stacks.get(active_skin_id, set()) | {active_skin_id}
            everything = set().union(*stacks.values())
            disable = (everything - keep)
            for sid in stacks:
                if sid != active_skin_id and sid != 'skin.estuary':
                    disable.add(sid)
            # only touch what's actually installed
            disable = sorted(d for d in disable
                             if os.path.isfile(os.path.join(ADDONS, d, 'addon.xml')))
            enable = sorted(k for k in keep
                            if os.path.isfile(os.path.join(ADDONS, k, 'addon.xml')))
            self.enable_addons_in_db(enable)
            self._disable_addons_in_db(disable)
            from resources.libs import modular_update as mu
            state = mu._load_state()
            state['__skin_disabled__'] = disable
            mu._save_state(state)
            log(f"skin stacks synced for {active_skin_id}: +{len(enable)} on, -{len(disable)} off")
        except Exception as e:
            log(f"sync_skin_stacks failed: {e}", xbmc.LOGWARNING)

    def _seed_state_from_manifest(self, addon_ids):
        """After a bundle-zip skin install: record the manifest shas for the
        addons the bundle just delivered, so the post-install completion pass
        doesn't re-download the identical zips (the Windows fresh-install
        re-fetched all 18 Zephyr addons it had just extracted). Only ids whose
        INSTALLED version matches the manifest version are recorded -- a stale
        bundle still gets refreshed by the update pass as before."""
        try:
            from resources.libs import modular_update as mu
            manifest = mu.fetch_manifest()
            by_id = {a['id']: a for a in manifest.get('addons', [])}
            state = mu._load_state()
            n = 0
            for aid in addon_ids:
                entry = by_id.get(aid)
                if entry and mu._installed_version(aid) == entry.get('version'):
                    state[aid] = entry['sha256']
                    n += 1
            mu._save_state(state)
            log(f"seeded manifest state for {n}/{len(addon_ids)} bundle-installed addons")
        except Exception as e:
            log(f"state seed after bundle install failed: {e}", xbmc.LOGWARNING)

    # ------------------------------------------------------------------ #
    # Skin manager helpers (used by skins_menu)
    # ------------------------------------------------------------------ #
    def get_optional_skin_url(self, url_key):
        """The zip URL for an optional skin (from build.txt), or None."""
        try:
            for b in (self.fetch_builds_list() or []):
                if b.get(url_key):
                    return b[url_key]
        except Exception as e:
            log(f"get_optional_skin_url failed: {e}", xbmc.LOGWARNING)
        return None

    def _install_from_manifest(self, addon_id, deps, name='סקין'):
        """Install a skin + its deps straight from the build manifest (for skins
        whose deps aren't bundled in a single build.txt zip). Returns True."""
        try:
            from resources.libs import modular_update as mu
            manifest = mu.fetch_manifest()
            by_id = {a['id']: a for a in manifest.get('addons', [])}
            ids = list(dict.fromkeys(list(deps) + [addon_id]))   # deps first, skin last, unique
            progress = xbmcgui.DialogProgress()
            progress.create(ADDON_NAME, f"[COLOR cyan]מתקין {name}...[/COLOR]")
            state = mu._load_state()
            # A swallowed failure here used to still return True -- the caller
            # would then switch lookandfeel.skin to a skin that never landed on
            # disk and restart into an unloadable skin. Track failures: the skin
            # itself (or a missing manifest entry for it) is fatal; a failed dep
            # is fatal too -- a skin whose dep is missing can't load either.
            failed = []
            for i, aid in enumerate(ids):
                entry = by_id.get(aid)
                if not entry:
                    # A missing manifest entry -- for the skin OR a dependency --
                    # is a real problem (a skin whose dep is absent won't load).
                    # Log it loudly and track it instead of silently skipping.
                    log(f"manifest install: '{aid}' absent from manifest"
                        f"{'' if aid == addon_id else f' (dependency of {addon_id})'}",
                        xbmc.LOGERROR)
                    failed.append(aid)
                    continue
                progress.update(int(i / max(len(ids), 1) * 100), f"[COLOR yellow]מתקין: {aid}[/COLOR]")
                try:
                    mu._apply_one(entry)          # sha-verified download + extract to addons/
                    # record the sha so the post-install completion skips it
                    # instead of re-downloading what we just installed
                    state[aid] = entry['sha256']
                except Exception as e:
                    log(f"manifest install {aid} failed: {e}", xbmc.LOGWARNING)
                    # already-installed addons are fine even if the re-download
                    # failed -- only count it when nothing usable is on disk
                    if not os.path.isfile(os.path.join(ADDONS, aid, 'addon.xml')):
                        failed.append(aid)
            mu._save_state(state)
            if failed:
                progress.close()
                log(f"manifest install of {addon_id} FAILED, missing: {failed}", xbmc.LOGERROR)
                self.dialog.ok(ADDON_NAME,
                               f"[COLOR {COLOR_ERROR}]התקנת {name} נכשלה![/COLOR]\n"
                               "לא בוצע שינוי סקין. נסו שוב מאוחר יותר.")
                return False
            # CRITICAL: freshly-extracted addons are added to Kodi as DISABLED.
            # A disabled skin (or disabled dep) can't load -> "failed to load skin
            # / missing files" and Kodi reverts to Estuary. Enable them all (deps
            # first, skin last) so the restart lands on a working skin. The other
            # install paths (install_skin_only/install_skin) already do this.
            self.enable_addons_in_db(ids)
            self.setup_wizard_repo_in_db()
            xbmc.executebuiltin('UpdateAddonRepos()')
            xbmc.executebuiltin('UpdateLocalAddons()')
            self._apply_build_config(addon_id)
            progress.update(100, "[COLOR lime]הותקן![/COLOR]")
            xbmc.sleep(400); progress.close()
            return True
        except Exception as e:
            log(f"_install_from_manifest error: {e}", xbmc.LOGERROR)
            return False

    def _apply_build_config(self, skin_id=None, content=None):
        """Force-apply the build config after a skin install/switch, so a freshly-
        (re)installed skin lands with all the build defaults -- Flix view, hidden
        match%/profile info, colorful ratings, detailed notifications, etc.

        Skin settings are delivered with merge_seed (add-if-absent) so a ROUTINE
        update never overwrites a preference the user set. But an explicit
        (re)install SHOULD reset to our curated defaults -- so we first delete the
        installed skin's settings.xml, letting the seed write our full defaults
        fresh. Credentials stay excluded by policy; other skins are untouched.

        content ('gears'|'pov', default = the stored content_source): the config
        engine skips Gears-specific entries for POV; here we also skip the two
        Gears-only DB seeders (shortcut folder + gears views) so a POV install
        never writes a byte of Gears state."""
        if content is None:
            try:
                content = ADDON.getSetting('content_source') or 'gears'
            except Exception:
                content = 'gears'
        try:
            from resources.libs import modular_update as mu
            if skin_id:
                sfile = xbmcvfs.translatePath(
                    'special://profile/addon_data/%s/settings.xml' % skin_id)
                try:
                    if os.path.exists(sfile):
                        os.remove(sfile)
                except Exception as e:
                    log(f"could not reset {skin_id} settings: {e}", xbmc.LOGWARNING)
            manifest = mu.fetch_manifest()
            state = mu._load_state()
            mu._maybe_apply_config(manifest, state, force=True, content=content)
            mu._save_state(state)
            if content != 'pov':
                # Seed the Gears shortcut folder the default networks widget uses,
                # so a fresh install's FIRST boot already renders it populated.
                mu.seed_gears_shortcut_folder()
                # Point Gears' use_viewtypes at THIS skin's view ids, so gears movie/
                # tvshow lists open in the skin's intended view (else Gears forces its
                # global default -- e.g. Estuary showed Wall instead of Poster).
                mu.apply_gears_views_for_skin(skin_id)
            # Flag a one-time skinvariables view rebuild for the next boot. A freshly
            # (re)installed skin (Zephyr/AF3) builds its views on Home load with
            # no_reload, so the display never refreshes -> foreground looks frozen
            # while the background updates, until the user manually switches a view.
            # The service does that clean rebuild for us so a fresh install comes up
            # right without the manual view-switch workaround.
            try:
                marker = os.path.join(ADDON_DATA_PATH, ADDON_ID, 'pending_view_rebuild')
                os.makedirs(os.path.dirname(marker), exist_ok=True)
                with open(marker, 'w', encoding='utf-8') as fh:
                    fh.write(skin_id or '1')
            except Exception as e:
                log(f"could not set pending_view_rebuild: {e}", xbmc.LOGWARNING)
        except Exception as e:
            log(f"apply build config on skin install failed: {e}", xbmc.LOGWARNING)

    def install_skin(self, skin_key, skin_url=None):
        """Download + install an optional skin (with its deps) WITHOUT switching
        or restarting. Uses the manifest for skins flagged manifest_install,
        else the bundled build.txt zip. Returns True on success."""
        skin = self.OPTIONAL_SKINS.get(skin_key)
        if not skin:
            return False
        if skin.get('manifest_install') and (_kodi_major() >= 22 or not skin_url):
            # Piers always; Omega only when no bundle URL is available
            return self._install_from_manifest(skin['id'], skin.get('deps', []), skin['name'])
        if not skin_url:
            return False
        progress = xbmcgui.DialogProgress()
        progress.create(ADDON_NAME, f"[COLOR cyan]מתקין סקין {skin['name']}...[/COLOR]")
        try:
            ADDON.setSetting('skip_update_check', 'true')
            if not os.path.exists(TEMP_FOLDER):
                os.makedirs(TEMP_FOLDER)
            skin_zip = os.path.join(TEMP_FOLDER, skin['zip'])
            try:
                os.remove(skin_zip)
            except Exception:
                pass
            progress.update(0, f"[COLOR yellow]מוריד {skin['name']}...[/COLOR]")
            ok = self.download_file(skin_url, skin_zip, progress, f"[COLOR yellow]מוריד {skin['name']}...[/COLOR]")
            if not ok or not os.path.exists(skin_zip) or os.path.getsize(skin_zip) == 0:
                progress.close()
                self.dialog.ok(ADDON_NAME, f"[COLOR {COLOR_ERROR}]ההורדה נכשלה![/COLOR]")
                return False
            # verify the download is intact AND is actually the requested skin
            # before extracting -- this standalone path had no CRC/identity guard,
            # so a corrupt or wrong zip would extract partially and be selected.
            v_ok, v_why = self.validate_build_zip(skin_zip, expected_addon_id=skin.get('id'))
            if not v_ok:
                progress.close()
                log(f"install_skin: zip invalid ({v_why})", xbmc.LOGWARNING)
                self.dialog.ok(ADDON_NAME, f"[COLOR {COLOR_ERROR}]הקובץ פגום או שגוי![/COLOR]")
                return False
            progress.update(50, "[COLOR yellow]סורק אדונים...[/COLOR]")
            skin_addons = self.grab_addons_from_zip(skin_zip)
            progress.update(60, f"[COLOR yellow]מתקין {skin['name']}...[/COLOR]")
            success, _ = self.extract_and_merge_skin(skin_zip, progress, f"מתקין {skin['name']}...")
            if not success:
                progress.close()
                self.dialog.ok(ADDON_NAME, f"[COLOR {COLOR_ERROR}]ההתקנה נכשלה![/COLOR]")
                return False
            progress.update(85, "[COLOR yellow]מפעיל אדונים...[/COLOR]")
            self.enable_addons_in_db(skin_addons)
            self._pin_addons_in_db(skin_addons)
            self.setup_wizard_repo_in_db()
            self._seed_state_from_manifest(skin_addons)
            xbmc.executebuiltin('UpdateAddonRepos()')
            xbmc.executebuiltin('UpdateLocalAddons()')
            self._apply_build_config(skin.get('id'))
            try:
                os.remove(skin_zip)
            except Exception:
                pass
            progress.update(100, "[COLOR lime]הותקן![/COLOR]")
            xbmc.sleep(400)
            progress.close()
            return True
        except Exception as e:
            try:
                progress.close()
            except Exception:
                pass
            log(f"install_skin error: {e}", xbmc.LOGERROR)
            self.dialog.ok(ADDON_NAME, f"[COLOR {COLOR_ERROR}]שגיאה:[/COLOR] {e}")
            return False

    def _pin_addons_in_db(self, aids):
        """Freeze shipped MODDED addons: clear repo origin + disable auto-update so
        Kodi never clobbers our Hebrew-modified versions (origin='' + updateRule=1).
        Per build policy only MODDED_ADDONS are pinned -- vanilla deps
        (skinvariables, texturemaker, resource fonts, ...) auto-update from their
        own repos and get re-vendored via tools/refresh_vanilla_deps.py."""
        from resources.libs.modular_update import MODDED_ADDONS
        aids = [a for a in (aids or []) if a in MODDED_ADDONS]
        if not aids:
            return
        try:
            import sqlite3
            dbdir = xbmcvfs.translatePath('special://database/')
            for f in os.listdir(dbdir):
                if not (f.startswith('Addons') and f.endswith('.db')):
                    continue
                c = sqlite3.connect(os.path.join(dbdir, f))
                try:
                    for aid in aids:
                        c.execute("UPDATE installed SET origin='' WHERE addonID=?", (aid,))
                        row = c.execute(
                            "SELECT COUNT(*) FROM update_rules WHERE addonID=?", (aid,)).fetchone()
                        if row and row[0]:
                            c.execute("UPDATE update_rules SET updateRule=1 WHERE addonID=?", (aid,))
                        else:
                            c.execute("INSERT INTO update_rules(addonID, updateRule) VALUES(?, 1)", (aid,))
                    c.commit()
                finally:
                    c.close()
            log("Pinned shipped skin addons (origin='' + no auto-update): %s" % ', '.join(aids))
        except Exception as e:
            log("_pin_addons_in_db error: %s" % e, xbmc.LOGERROR)

    def _db_remove_addon(self, aid):
        """Delete an addon's rows from every Addons*.db (so Kodi forgets it).
        Includes update_rules -- the pinning row -- so a removed skin leaves no
        stale rule behind (it accumulated across reinstalls otherwise)."""
        try:
            import sqlite3
            dbdir = xbmcvfs.translatePath('special://database/')
            for f in os.listdir(dbdir):
                if f.startswith('Addons') and f.endswith('.db'):
                    c = sqlite3.connect(os.path.join(dbdir, f))
                    for t in ('installed', 'addons', 'repo', 'update_rules'):
                        try:
                            c.execute('DELETE FROM %s WHERE addonID=?' % t, (aid,))
                        except Exception:
                            pass
                    c.commit(); c.close()
        except Exception as e:
            log(f"_db_remove_addon {aid} failed: {e}", xbmc.LOGWARNING)

    def _purge_skin_residue(self, skin_id):
        """Delete the per-skin files the helper addons keep for skin_id, so a
        removed skin leaves nothing behind in skinshortcuts / skinvariables.
        These are named after the skin, so purging them is unambiguous and can
        never touch another skin's data. (The skin's exclusive helper ADDONS --
        e.g. script.nimbus.helper -- are deliberately NOT removed: they are
        disabled by sync_skin_stacks and harmless, and auto-removing an addon
        risks one that Estuary or another kept skin shares.)"""
        ad = os.path.join(USERDATA, 'addon_data')
        # 1) skinshortcuts: skin.X.hash / .properties / .DATA.xml (+ .pre_gears)
        ss = os.path.join(ad, 'script.skinshortcuts')
        try:
            for name in os.listdir(ss):
                if name.startswith(skin_id):
                    p = os.path.join(ss, name)
                    (shutil.rmtree if os.path.isdir(p) else os.remove)(p)
        except Exception:
            pass
        # 1b) our own menu-bundle marker for this skin. It is written but never
        # otherwise deleted, so a removed skin left "bundle vN already applied"
        # behind -- on a later REINSTALL that makes repair_skin_menu see
        # stale=False and skip, suppressing the very repair that exists because
        # a fresh install caches an EMPTY skinshortcuts menu (found in Asaf's
        # 2026-08-02 removal sweep, after we purged this skin's menu DATA above).
        try:
            os.remove(os.path.join(ad, ADDON_ID, 'menu_ver_%s.txt' % skin_id))
        except Exception:
            pass
        # 2) skinvariables: nodes/skin.X/ dir + skin.X-*.json (viewtypes etc.)
        sv = os.path.join(ad, 'script.skinvariables')
        try:
            nodes = os.path.join(sv, 'nodes', skin_id)
            if os.path.isdir(nodes):
                shutil.rmtree(nodes, ignore_errors=True)
            for name in os.listdir(sv):
                if name.startswith(skin_id):
                    p = os.path.join(sv, name)
                    (shutil.rmtree if os.path.isdir(p) else os.remove)(p)
        except Exception:
            pass

    def remove_skin(self, skin_id):
        """Uninstall an optional skin (folder + addon_data + db rows). Never
        removes Estuary or the currently-active skin."""
        if skin_id == 'skin.estuary':
            return False
        try:
            if xbmc.getSkinDir() == skin_id:
                return False
        except Exception:
            pass
        try:
            folder = os.path.join(ADDONS, skin_id)
            if os.path.isdir(folder):
                shutil.rmtree(folder, ignore_errors=True)
            ad = os.path.join(USERDATA, 'addon_data', skin_id)
            if os.path.isdir(ad):
                shutil.rmtree(ad, ignore_errors=True)
            self._db_remove_addon(skin_id)
            self._purge_skin_residue(skin_id)
            xbmc.executebuiltin('UpdateLocalAddons()')
            log(f"removed skin {skin_id} (+ residue purged)")
            return True
        except Exception as e:
            log(f"remove_skin {skin_id} failed: {e}", xbmc.LOGWARNING)
            return False

    def _ask_and_apply_oled(self, progress):
        """Ask about OLED and modify guisettings.xml if needed"""
        progress.close()  # Close progress to show dialog
        
        result = self.dialog.yesno(
            '[COLOR FF00BFFF]הגדרות OLED[/COLOR]',
            '[B]יש לך מסך OLED?[/B]\n\n'
            'אם כן, נגדיר הגדרות להגנה על המסך:\n'
            '- Screensaver שחור\n'
            '- הפעלה אחרי דקה\n'
            '- עמעום בזמן השהיה\n'
            '- כיבוי המסך אחרי 5 דקות',
            yeslabel='כן, יש לי OLED',
            nolabel='לא'
        )
        
        if not result:
            return
        
        log("User has OLED - applying screen-protection settings")

        # Through Kodi's settings API, NOT by editing guisettings.xml. Kodi keeps
        # its settings in memory and rewrites that file when it exits -- and this
        # runs moments before the installer RESTARTS Kodi, so a file edit was
        # guaranteed to be thrown away. Measured 2026-08-13: screensaver.mode
        # (the one that actually enables the black screensaver) came back EMPTY
        # after a normal Kodi close, so the feature never did anything.
        try:
            from resources.libs import oled as oled_mod
            _applied, failed = oled_mod.apply_oled_settings()
        except Exception as e:
            log('OLED apply failed: %s' % e, xbmc.LOGERROR)
            _applied, failed = [], [s for s, _v in getattr(oled_mod, 'OLED_SETTINGS', ())]

        if failed:
            self.dialog.ok(ADDON_NAME,
                           '[COLOR %s]חלק מהגדרות ה-OLED לא הוחלו[/COLOR]\n\n%s'
                           % (COLOR_WARNING, ', '.join(failed)))

    def _ask_and_apply_sdr(self):
        """Ask whether the TV can show HDR, and make the answer STICK.

        Both engines already have a "SDR only" filter in the source window, but
        it lasts exactly one window -- a user whose TV cannot display HDR was
        re-applying it on every single search. This asks once, at install, and
        writes the ENGINES' OWN filter settings (see resources/libs/sdr.py).

        Runs AFTER the addons are registered and enabled: the values go in
        through Kodi's addon-settings API (POV) and Gears' settings.db, neither
        of which exists for an addon Kodi doesn't know about yet.

        Default answer is "yes, it supports HDR" -- that is both the common case
        and the harmless one. Answering yes NEVER writes anything unless the
        filter is currently on, so it cannot clobber a user's own Prefer/Sort
        choice on a reinstall.
        """
        try:
            from resources.libs import sdr as sdr_mod
        except Exception as e:
            log(f"SDR question skipped (module load failed): {e}", xbmc.LOGWARNING)
            return

        try:
            state = sdr_mod.status()
            if all(v is None for v in state.values()):
                log('SDR question skipped: no source engine installed')
                return

            # Every line is Hebrew-leading with at most ONE Latin run, at its END.
            # A Latin run in the MIDDLE of a Hebrew line gets reordered by bidi
            # and lands somewhere else in the sentence (the same trap as the
            # maintenance sizes and the sources panel). No '>' arrows either:
            # between two Hebrew runs a neutral arrow flips and points backwards.
            heading = '[COLOR FF00BFFF]סוג המסך[/COLOR]'
            body = ('[B]הטלוויזיה שלך תומכת ב-HDR / Dolby Vision?[/B]\n\n'
                    'במסך שאינו תומך, סרט בפורמט הזה נראה דהוי או בצבעים שגויים.\n'
                    'אם אין תמיכה, נסתיר אוטומטית בכל חיפוש מקורות HDR/DV\n\n'
                    'ניתן לשנות זאת בכל עת בתפריט התחזוקה של האשף')
            labels = {'yeslabel': 'כן, תומך ב-HDR/DV', 'nolabel': 'לא, מסך רגיל'}
            # Kodi focuses NO by default, which here would mean a stray OK press
            # silently turns the filter ON -- the exact opposite of the safe
            # answer. Focus YES ("supports HDR" = change nothing). Guarded: the
            # defaultbutton kwarg only exists from Kodi 20 up.
            try:
                supports_hdr = self.dialog.yesno(
                    heading, body, defaultbutton=xbmcgui.DLG_YESNO_YES_BTN, **labels)
            except (TypeError, AttributeError):
                supports_hdr = self.dialog.yesno(heading, body, **labels)
            if supports_hdr:
                if any(v is True for v in state.values()):
                    sdr_mod.apply_sdr_only(False)   # was on -> honour the answer
                else:
                    log('SDR: display supports HDR, nothing to change')
                return

            result = sdr_mod.apply_sdr_only(True)
            failed = sdr_mod.failures(result)
            if failed:
                self.dialog.ok(ADDON_NAME,
                               '[COLOR %s]ההגדרה לא הוחלה[/COLOR]\n\n%s'
                               % (COLOR_WARNING, ', '.join(failed)))
        except Exception as e:
            log(f"SDR question failed: {e}", xbmc.LOGERROR)

    def _countdown_restart(self, build_name, skin_name):
        """Countdown and restart Kodi"""
        progress = xbmcgui.DialogProgress()
        progress.create(
            "[COLOR lime]ההתקנה הושלמה בהצלחה![/COLOR]",
            f"[COLOR cyan]בילד:[/COLOR] {build_name}\n[COLOR cyan]סקין:[/COLOR] {skin_name}"
        )
        
        # Android cannot relaunch itself (see the note above _apply_skin_live),
        # so do not imply that it will -- tell the user they have to reopen Kodi.
        tail = ('\n\n[B]קודי ייסגר בעוד %d שניות[/B]\n[COLOR yellow]יש לפתוח את '
                'Kodi מחדש כדי לסיים[/COLOR]') if _is_android() \
            else '\n\n[B]קודי ייסגר בעוד %d שניות...[/B]'
        for i in range(5, 0, -1):
            pct = int((5 - i) / 5.0 * 100)
            progress.update(pct, f"[COLOR cyan]בילד:[/COLOR] {build_name}\n"
                                 f"[COLOR cyan]סקין:[/COLOR] {skin_name}" + (tail % i))
            xbmc.sleep(1000)

        progress.close()
        # HARD exit on purpose -- NO graceful Quit here. The install wrote the
        # new skin/font/Hebrew baseline directly into guisettings.xml ON DISK;
        # a graceful Quit makes Kodi re-save guisettings from MEMORY (old skin,
        # Default font, bootstrap defaults) and wipe everything the install
        # just wrote. After an install DISK is authoritative -- skip the save.
        # (fast_exit keeps the graceful Quit: on a normal user exit, memory IS
        # authoritative.)
        # Windows: arm a relauncher first so Kodi actually COMES BACK -- the
        # skin-switch restart used to just exit and wait for the user to
        # reopen (2026-07-18). Same relauncher as the update flow, minus the
        # graceful Quit (disk is authoritative here).
        import sys
        if sys.platform.startswith('win'):
            try:
                import subprocess
                pid = os.getpid()
                exe = sys.executable if str(sys.executable).lower().endswith('kodi.exe') \
                    else os.path.join(xbmcvfs.translatePath('special://xbmc/'), 'kodi.exe')
                launch = None
                try:
                    import ctypes
                    ctypes.windll.kernel32.GetCommandLineW.restype = ctypes.c_wchar_p
                    launch = (ctypes.windll.kernel32.GetCommandLineW() or '').strip()
                except Exception:
                    pass
                if not launch or 'kodi' not in launch.lower():
                    portable = xbmcvfs.translatePath('special://home/').lower().startswith(
                        xbmcvfs.translatePath('special://xbmc/').lower())
                    launch = '"%s"%s' % (exe, ' -p' if portable else '')
                if os.path.isfile(exe):
                    cmd = ('ping -n 6 127.0.0.1 >nul & '
                           'tasklist /FI "PID eq %d" /FI "IMAGENAME eq kodi.exe" 2>nul | '
                           'findstr /I kodi.exe >nul && taskkill /F /PID %d /T >nul 2>&1 & '
                           'start "" %s' % (pid, pid, launch))
                    subprocess.Popen(cmd, shell=True, creationflags=0x08000000)
                    log("post-install restart: relauncher armed")
            except Exception as e:
                log(f"relauncher arm failed (manual relaunch needed): {e}", xbmc.LOGWARNING)
        # No Android branch on purpose: nothing can relaunch Kodi there (all
        # three mechanisms were measured and fail -- see the note above
        # _apply_skin_live). The countdown above says so instead of pretending.
        log("post-install restart: hard exit, skipping Kodi's exit-save (disk is authoritative)")
        os._exit(0)


def builds_menu():
    """Main builds menu - Select Build -> Select Skin -> Install"""
    dialog = xbmcgui.Dialog()
    manager = BuildManager()
    
    # Fetch builds
    progress = xbmcgui.DialogProgress()
    progress.create(ADDON_NAME, "[COLOR yellow]טוען רשימת בילדים...[/COLOR]")
    builds = manager.fetch_builds_list()
    progress.close()
    
    if not builds:
        dialog.ok(ADDON_NAME, "[COLOR red]לא נמצאו בילדים זמינים.[/COLOR]\nבדוק את חיבור האינטרנט.")
        return
    
    # Shown on the installed build's row.
    installed_build = manager.get_installed_build_name()
    installed_skin = manager.get_installed_skin()

    # POV is the recommended content source -> list POV builds FIRST, Gears
    # after. Stable sort keeps any other ordering from build.txt intact within
    # each group. The '(מומלץ)' tag is added to the POV row label below.
    builds.sort(key=lambda b: 0 if (b.get('content') or '').strip().lower() == 'pov' else 1)

    while True:
        # Branded rows (same custom window as the wizard menu), with a parallel
        # 'kind' list so we act on the choice by index, not by matching text.
        rows = []
        row_kind = []
        for b in builds:
            name = b.get('name', 'Unknown')
            ver = b.get('version', '?')
            # POV is the recommended source -> tag its row; Gears stays the
            # secondary option. Only the DISPLAY label changes; the installed-
            # build match below still uses the raw name.
            is_pov = (b.get('content') or '').strip().lower() == 'pov'
            label = f"{name} (מומלץ)" if is_pov else name
            if name == installed_build:
                rows.append(menu_item(label, f"v{ver}  |  מותקן ({installed_skin})", 'DefaultAddonProgram.png'))
            else:
                rows.append(menu_item(label, f"v{ver}", 'DefaultAddonProgram.png'))
            row_kind.append(('build', b))

        # (Removed: the "הוסף סקין Arctic Fuse" row. It dated from when AF3 was
        # the ONLY optional skin, before the dedicated Skins menu existed. It was
        # redundant -- 'סקינים' > 'החלפת סקין' installs/switches ANY of the four
        # skins without wiping -- inconsistent (no equivalent for Nimbus/Zephyr),
        # and BROKEN on POV: install_skin_only never re-applied the content
        # source, so AF3 landed with no POV menus/widgets at all. The skin-switch
        # flow does that correctly.)

        sel = wizard_select('התקנת בילד', rows)
        if sel < 0:
            break                                   # BACK / cancel

        kind, selected_build = row_kind[sel]

        if not selected_build:
            continue

        build_name = selected_build.get('name', 'Unknown')
        build_ver = selected_build.get('version', '?')
        has_skin = 'skin_url' in selected_build
        
        # Show skin selection. Estuary always; Arctic Fuse / Nimbus only if the
        # build advertises their zip URL.
        if has_skin:
            # (choice, name, one-line desc, preview image under resources/media/skin_previews/)
            # ORDER = measured boot speed on the weakest fleet device, fastest
            # first (docs/skin-performance.md, Xiaomi medians: Estuary 1.37s,
            # Nimbus 1.47s, Zephyr 1.66s, AF3 3.70s). The descriptions carry the
            # same verdict: the top three are one speed class; only AF3 is
            # humanly slower.
            skin_options = [('estuary', 'Estuary', _SKIN_DESC['estuary'], 'estuary.jpg')]
            if selected_build.get('nimbus_skin_url'):
                skin_options.append(('nimbus', 'Nimbus', _SKIN_DESC['nimbus'], 'nimbus.jpg'))
            # Arctic Zephyr installs from the manifest (not a build.txt url), so it's
            # always offered here.
            skin_options.append(('zephyr', 'Arctic Zephyr', _SKIN_DESC['zephyr'], 'zephyr.jpg'))
            if selected_build.get('skin_url'):
                skin_options.append(('arctic', 'Arctic Fuse', _SKIN_DESC['arctic'], 'af3.jpg'))

            # Custom picker window with a LARGE live preview of the focused skin.
            # Falls back to the old useDetails select if the window fails.
            preview_dir = os.path.join(xbmcvfs.translatePath(ADDON.getAddonInfo('path')),
                                       'resources', 'media', 'skin_previews')
            picker_items = [(_name, _desc, os.path.join(preview_dir, _img),
                             os.path.join(preview_dir, os.path.splitext(_img)[0]))
                            for _choice, _name, _desc, _img in skin_options]
            try:
                skin_sel = SkinPickerDialog.pick(f"בחר סקין עבור {build_name}", picker_items)
            except Exception as e:
                log(f"SkinPickerDialog failed ({e}), falling back to select")
                li_list = []
                for _name, _desc, _p in picker_items:
                    li = xbmcgui.ListItem(_name, _desc)
                    li.setArt({'thumb': _p, 'icon': _p, 'poster': _p})
                    li_list.append(li)
                skin_sel = dialog.select(f"[B]בחר סקין עבור {build_name}[/B]", li_list, useDetails=True)

            if skin_sel < 0:
                continue

            skin_choice = skin_options[skin_sel][0]
            skin_name = BuildManager.OPTIONAL_SKINS.get(skin_choice, {}).get('name', 'Estuary')
        else:
            skin_choice = 'estuary'
            skin_name = "Estuary"

        # Content source: Gears or POV (same skins/subtitles, different content
        # engine). The build itself now declares it via build.txt `content=` --
        # the two builds ("MasterKodi IL (Gears)" / "(POV)") ARE the choice, made
        # right on the build-selection screen. Only fall back to a separate
        # dialog for a legacy build.txt that doesn't carry `content`.
        content_choice = (selected_build.get('content') or '').strip().lower()
        if content_choice not in ('gears', 'pov'):
            # POV first (recommended); Gears second (secondary).
            cs_sel = dialog.select('מקור תוכן', [
                'POV (מומלץ)',
                'Gears (חלופה - אותם סקינים וכתוביות)'])
            if cs_sel < 0:
                continue
            content_choice = 'gears' if cs_sel == 1 else 'pov'

        # Confirm installation
        confirm_msg = (
            f"[COLOR cyan]בילד:[/COLOR] {build_name} v{build_ver}\n"
            f"[COLOR cyan]סקין:[/COLOR] {skin_name}\n"
            f"[COLOR cyan]מקור תוכן:[/COLOR] {content_choice.upper()}\n\n"
            f"[COLOR {COLOR_WARNING}]הבילד הקיים יימחק (תוכל לבחור מה לשמור בשלב הבא).[/COLOR]\n\n"
            "להתחיל בהתקנה?"
        )

        if dialog.yesno("[B]אישור התקנה[/B]", confirm_msg, yeslabel="[B]התקן[/B]", nolabel="ביטול"):
            # 'What to keep' checklist (all ticked by default) -> carried across the wipe.
            # Detect user-installed extra addons (in home/addons but not in the build).
            from resources.libs import keep as keep_mod
            extras = []
            try:
                from resources.libs import modular_update
                man = modular_update.fetch_manifest()
                extras = keep_mod.detect_extras({a.get('id') for a in man.get('addons', [])})
            except Exception as e:
                log(f"detect_extras skipped: {e}", xbmc.LOGWARNING)
            # The checklist only earns its place when there is something to lose.
            # Skip it when the user has turned it off (keep_ask=false -> always
            # keep everything), or when nothing on this box is worth carrying
            # over (fresh/just-reinstalled). Both paths keep EVERYTHING, so
            # skipping the dialog can never cause data loss -- it only removes a
            # pointless confirmation.
            # CROSS-source reinstall (Gears->POV or back): the new build doesn't
            # ship the old engine, so its viewing data + the old favourites are
            # NOT offered (they'd be orphans / clobber the new source's config).
            # Debrid/Trakt/Gemini + extras + downloads stay -- those are the
            # user's own accounts/data, not engine state. Favourites are still
            # STAGED (not shown) so restore can park them for manual recovery.
            try:
                _prev_source = ADDON.getSetting('content_source') or 'gears'
            except Exception:
                _prev_source = 'gears'
            _cross = (_prev_source != content_choice)
            _excl = set()
            if _cross:
                _excl.add('gears_content' if _prev_source == 'gears' else 'pov_content')
                _excl.add('favs')
                log(f"keep: cross-source install ({_prev_source} -> {content_choice}); "
                    f"not offering {sorted(_excl)}")
            _all_keys = [g['key'] for g in keep_mod.GROUPS if g['key'] not in _excl] \
                        + (['extras'] if extras else [])
            try:
                _ask = ADDON.getSetting('keep_ask') != 'false'
            except Exception:
                _ask = True
            if not _ask:
                keep_keys = _all_keys
                log("keep prompt skipped (keep_ask=false) - keeping everything")
            elif not keep_mod.has_anything(extras):
                keep_keys = _all_keys
                log("keep prompt skipped (nothing on this box to keep)")
            else:
                keep_keys = keep_mod.prompt(extras=extras, default_all=True, exclude=_excl)
            # None = the user cancelled the keep question. That is NOT the same
            # as an empty list (which IS the clean-install choice), so it has to
            # be checked before the `if not keep_keys` below or an abort would
            # be read as "wipe everything". Back to the build list, exactly like
            # declining the install confirm.
            if keep_keys is None:
                log('install aborted at the keep step (user cancelled)')
                continue
            # Cross-source: stage favourites purely so the OLD set can be parked
            # next to the new one. Skipped when the user asked to keep NOTHING --
            # writing a favourites side-save would contradict an explicit clean
            # install (an empty selection IS the clean choice; see keep.prompt).
            if _cross and keep_keys and 'favs' not in keep_keys:
                keep_keys.append('favs')       # staged for the parked side-save only
            if not keep_keys:
                log("keep: CLEAN install requested - nothing will be staged")
            manager.install_build(selected_build, skin_choice=skin_choice,
                                  keep_keys=keep_keys, keep_extras=extras,
                                  content_choice=content_choice)
            break


# ===================================================================== #
# Skin manager menu
# ===================================================================== #
# (key, display name, addon id, preview image). Estuary is Kodi's built-in
# fallback skin -- always available, never removable.
# One-line descriptions, shared by BOTH pickers (install flow + skin switch) so
# the wording can never drift between them. They used to live only in the
# install flow, so the switch picker showed just the install status and a user
# choosing a skin there got no idea what it was (Asaf, 2026-08-02).
_SKIN_DESC = {
    'estuary': 'הרגיל | הכי מהיר | עיצוב פשוט',
    'nimbus':  'מהיר כמעט כמו הרגיל | יפה ומודרני | מתאים גם למכשירים חלשים',
    'zephyr':  'עשיר ומעוצב בסגנון נטפליקס | מהיר | מתאים לרוב המכשירים',
    'arctic':  'הכי יפה ומעוצב | הכי איטי בטעינה | למכשירים חזקים',
}

# ORDER = measured boot speed, fastest first (docs/skin-performance.md,
# Xiaomi medians: Estuary 1.37s, Nimbus 1.47s, Zephyr 1.66s, AF3 3.70s).
_SKIN_CATALOG = [
    ('estuary', 'Estuary', 'skin.estuary', 'estuary.jpg'),
    ('nimbus', 'Nimbus', 'skin.nimbus', 'nimbus.jpg'),
    ('zephyr', 'Arctic Zephyr', 'skin.arctic.zephyr.2.resurrection.mod', 'zephyr.jpg'),
    ('arctic', 'Arctic Fuse', 'skin.arctic.fuse.3', 'af3.jpg'),
]
_OPTIONAL_SKIN_IDS = {'skin.arctic.fuse.3', 'skin.nimbus',
                      'skin.arctic.zephyr.2.resurrection.mod'}

# (AF3/Nimbus used to be hidden on Kodi 22 -- no loadable Piers build existed.
# manifest-piers now ships gui-5.18 variants of all four skins and every
# install path routes Piers through the manifest, so the picker offers them
# everywhere.)


def _kodi_major():
    try:
        return int(xbmc.getInfoLabel('System.BuildVersion').split('.')[0])
    except Exception:
        return 0


def _skin_installed(skin_id):
    # DISK is the truth here, NOT System.HasAddon: Kodi's in-memory addon
    # manager keeps a stale "installed" entry after the deferred skin removal
    # deletes the folder behind its back (UpdateLocalAddons is async), and
    # trusting it sent the switch flow down the "already installed" path --
    # switching the active skin to one that no longer exists on disk.
    return os.path.isfile(os.path.join(ADDONS, skin_id, 'addon.xml'))


def _skin_name(skin_id):
    for _k, name, sid, _img in _SKIN_CATALOG:
        if sid == skin_id:
            return name
    return skin_id


def skins_menu():
    """Dedicated skin manager: switch active skin (install if needed, ask what
    to do with the previous one) and clean up unused skins."""
    while True:
        items = [
            menu_item('החלפת סקין', 'בחר את הסקין הפעיל (יותקן אם צריך)', 'DefaultAddonSkin.png'),
            menu_item('הסרת סקינים לא בשימוש', 'פנה מקום - משאיר את הפעיל ואת Estuary', 'DefaultAddonService.png'),
            menu_item('פתח את האשף', 'חזרה לתפריט הראשי של האשף', 'DefaultAddonProgram.png'),
        ]
        sel = wizard_select('סקינים', items)
        if sel == -1:
            return
        if sel == 0:
            # a successful live (Android) switch closes the wizard and lands on
            # the new skin's home -- don't re-draw this menu on top of it
            if _skin_switch_flow() == 'close':
                return
        elif sel == 1:
            _skin_cleanup_flow()
        elif sel == 2:
            # Open the wizard's MAIN page. This menu is reached from the skin's
            # power-menu button (RunPlugin ?mode=skins), which only exposes skin
            # switch/remove -- this lets the user jump to the full wizard instead
            # of navigating Add-ons > Program add-ons by hand. RunAddon re-invokes
            # the addon's default entry (-> main_menu), exactly like launching it
            # from the Programs list; return first so this run ends cleanly.
            xbmc.executebuiltin('RunAddon(%s)' % ADDON_ID)
            return


def _skin_switch_flow():
    manager = BuildManager()
    dialog = xbmcgui.Dialog()
    preview_dir = os.path.join(xbmcvfs.translatePath(ADDON.getAddonInfo('path')),
                               'resources', 'media', 'skin_previews')
    try:
        active = xbmc.getSkinDir() or ''
    except Exception:
        active = ''

    picker, meta = [], []
    for key, name, sid, img in _SKIN_CATALOG:
        installed = _skin_installed(sid)
        # 'active' is the lookandfeel.skin SETTING -- if that skin is missing
        # on disk Kodi is actually running a fallback, so treat it as not
        # installed (installable) rather than blocking it as the active skin.
        if sid == active and installed:
            tag = 'פעיל'
        elif installed:
            tag = 'מותקן'
        else:
            tag = 'לא מותקן'
        # status FIRST (it is what the user is scanning for) then the same
        # description the install picker shows. COLOUR carries the state so the
        # three cases are distinguishable at a glance: active = green + bold,
        # installed = plain white, not installed = dimmed grey. Colour only, no
        # symbols -- a leading glyph in an RTL line lands unpredictably, and
        # these captions are Hebrew (bidi rule).
        colour = ('springgreen' if tag == 'פעיל'
                  else 'white' if tag == 'מותקן' else 'grey')
        bold = ('[B]', '[/B]') if tag == 'פעיל' else ('', '')
        caption = '[COLOR %s]%s%s%s[/COLOR]  ·  [COLOR grey]%s[/COLOR]' % (
            colour, bold[0], tag, bold[1], _SKIN_DESC.get(key, ''))
        picker.append((name, caption, os.path.join(preview_dir, img),
                       os.path.join(preview_dir, os.path.splitext(img)[0])))
        meta.append((key, name, sid, installed))

    try:
        idx = SkinPickerDialog.pick('בחר סקין', picker)
    except Exception as e:
        log(f"SkinPickerDialog failed ({e}); fallback select", xbmc.LOGWARNING)
        # picker rows are 4-tuples since the preview-slideshow change; unpacking
        # 3 here raised ValueError and turned a recoverable dialog failure into
        # a dead menu.
        idx = dialog.select('בחר סקין', [f"{row[0]}  ({row[1]})" for row in picker])
    if idx is None or idx < 0:
        return

    key, name, sid, installed = meta[idx]
    if sid == active and installed:
        dialog.ok('סקינים', f'הסקין {name} כבר פעיל.')
        return
    if not dialog.yesno('סקינים', f'להחליף לסקין {name}?', yeslabel='החלף', nolabel='ביטול'):
        return

    prev_active = active
    # install if it's an optional skin that isn't present yet
    if not installed and key != 'estuary':
        skin_cfg = BuildManager.OPTIONAL_SKINS.get(key, {})
        # fetch the bundle URL when one exists; install_skin decides zip vs
        # manifest (Piers always manifest; Omega prefers the bundle zip)
        url = (manager.get_optional_skin_url(skin_cfg.get('url_key'))
               if skin_cfg.get('url_key') else None)
        if not url and not skin_cfg.get('manifest_install'):
            dialog.ok('סקינים', f'לא נמצא קישור להורדת {name}.')
            return
        if not manager.install_skin(key, url):
            return

    # switch active skin
    manager.set_default_skin(sid)
    ADDON.setSetting('installed_skin', name)
    # activate the new skin's stack, neutralize the other skins' stacks
    manager.sync_skin_stacks(sid)

    # Ask what to do with the previous optional skin FIRST -- it's an instant
    # user decision. The POV re-apply below fetches ~17 variant files from GitHub
    # one-by-one, which used to run BEFORE this prompt and made the window take
    # seconds to appear. Prompt first, network after. Removal is DEFERRED to the
    # next startup: the old skin is still the running one until we restart, and
    # deleting a live skin (Windows file locks) fails.
    if prev_active in _OPTIONAL_SKIN_IDS and prev_active != sid:
        if dialog.yesno('סקינים',
                        f'מה לעשות עם הסקין הקודם ({_skin_name(prev_active)})?',
                        yeslabel='הסר', nolabel='השאר'):
            try:
                marker = os.path.join(ADDON_DATA_PATH, ADDON_ID, 'pending_skin_removal')
                os.makedirs(os.path.dirname(marker), exist_ok=True)
                with open(marker, 'w', encoding='utf-8') as f:
                    f.write(prev_active)
            except Exception as e:
                log(f"could not schedule skin removal: {e}", xbmc.LOGWARNING)

    # If the build is on POV, re-apply the POV config for the NEW skin (parity
    # with the Gears config re-apply on skin switch). Runs AFTER the prompt (see
    # above) so the fetch delay isn't in the user's way. install_apply uses the
    # explicit skin id + no reload (the restart below applies it). Fail-open ->
    # skin still switches on Gears config.
    try:
        import xbmcaddon as _xa
        if _xa.Addon().getSetting('content_source') == 'pov':
            _p = xbmcgui.DialogProgress()
            _p.create(ADDON_NAME, '[COLOR cyan]מחיל תצורת POV לסקין החדש...[/COLOR]')
            from resources.libs import content_source
            # _apply_pov_core, NOT install_apply: the box is already POV (checked
            # above). install_apply sets content_source='gears' when the apply
            # fails -- e.g. switching a Piers box to Nimbus/AF3, which have no
            # Piers POV variant -- which would silently convert a POV build into a
            # broken "gears" box that has no Gears content installed, and make
            # later updates apply Gears config to it. A failed re-apply must leave
            # the box on POV; the new skin simply keeps its own default menus.
            ok, err = content_source._apply_pov_core(sid)
            _p.close()
            if not ok:
                log(f"POV re-apply on skin switch left source=pov ({err})", xbmc.LOGWARNING)
    except Exception as e:
        log(f"POV re-apply on skin switch failed: {e}", xbmc.LOGWARNING)

    # Apply the new skin. Android has no working restart of any kind (see the
    # note above _apply_skin_live), so there the switch is done in-process --
    # which is also better UX: the user never leaves Kodi. Everywhere else the
    # proven restart path is kept.
    if _is_android():
        # the stack was enabled in the DB above; the running Kodi has to be told
        # too, or it refuses to load the skin and drops back to Estuary
        try:
            stack = manager.SKIN_STACKS.get(sid, set()) | {sid}
            _enable_addons_live(sorted(
                a for a in stack
                if os.path.isfile(os.path.join(ADDONS, a, 'addon.xml'))))
        except Exception as e:
            log(f"live enable of the skin stack failed: {e}", xbmc.LOGWARNING)
        # deliberately NO progress dialog here: Kodi's "keep this skin?" prompt
        # has to be clickable, and our own modal on top of it swallows the click
        xbmcgui.Dialog().notification(ADDON_NAME, f'מחליף לסקין {name}...',
                                      xbmcgui.NOTIFICATION_INFO, 4000)
        ok = _apply_skin_live(sid, manager.SKIN_FONTSET.get(sid, 'Default'))
        if ok:
            # Brief toast, NOT a modal ok(): a modal here would sit on top of the
            # freshly-loaded skin and, when dismissed, drop the user back into the
            # wizard's skins menu (the loop below). The user asked to switch the
            # skin -- so land them on the NEW skin's home, not back in the wizard.
            xbmcgui.Dialog().notification(
                ADDON_NAME, f'הסקין הוחלף ל-{name}',
                xbmcgui.NOTIFICATION_INFO, 3000)
            xbmc.executebuiltin('Dialog.Close(all,true)')
            xbmc.executebuiltin('ActivateWindow(home)')
            return 'close'          # tell skins_menu to stop looping
        else:
            # fall back to the hard exit -- disk already holds the new skin, so
            # reopening Kodi lands on it
            dialog.ok('סקינים',
                      f'[COLOR {COLOR_WARNING}]הסקין הוגדר[/COLOR]\n\n'
                      'צריך לסגור ולפתוח את Kodi כדי להחיל אותו.')
            manager._countdown_restart(manager.get_installed_build_name(), name)
    else:
        # restart to apply the new skin (service removes the old one on next launch)
        manager._countdown_restart(manager.get_installed_build_name(), name)


def _skin_cleanup_flow():
    manager = BuildManager()
    dialog = xbmcgui.Dialog()
    try:
        active = xbmc.getSkinDir() or ''
    except Exception:
        active = ''
    removable = [sid for sid in _OPTIONAL_SKIN_IDS
                 if sid != active and _skin_installed(sid)]
    if not removable:
        dialog.ok('סקינים', 'אין סקינים לא בשימוש להסרה.')
        return
    names = ', '.join(_skin_name(s) for s in removable)
    if not dialog.yesno('סקינים', f'להסיר את הסקינים הבאים?\n{names}',
                        yeslabel='הסר', nolabel='ביטול'):
        return
    removed = [s for s in removable if manager.remove_skin(s)]
    dialog.ok('סקינים', f'הוסרו {len(removed)} סקינים.' if removed else 'לא הוסר דבר.')
