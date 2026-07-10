# -*- coding: utf-8 -*-
"""
MasterKodi IL Wizard - Service

On startup: self-updates the wizard, sweeps stale *_old_<ts> backup dirs,
then runs ONE combined update check (check_all_updates) covering Gears base +
overlay, AI Subs (gearsai), and Skin + their Hebrew files -- shown in a single
dialog and applied in one pass with a single restart. Also reinstalls Hebrew
files after an add-on auto-update is detected via Kodi notifications.
"""
import xbmc
import xbmcgui
import xbmcaddon
import xbmcvfs
import json
import os
import re
import shutil
import zipfile
import urllib.request
import ssl
import time

# Skip service on first run - let firstrun handle the wizard launch
MARKER_FILE = '.masterkodi_il_done'
def _marker_exists():
    home = xbmcvfs.translatePath('special://home/')
    return os.path.exists(os.path.join(home, MARKER_FILE))

if not _marker_exists():
    xbmc.log('[plugin.program.masterkodi.il.wizard] No marker yet, skipping wizard startup service (firstrun will handle launch)', xbmc.LOGINFO)
    raise SystemExit

try:
    import requests
except ImportError:
    requests = None

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo('id')
ADDON_NAME = ADDON.getAddonInfo('name')
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'

ADDONS_PATH = xbmcvfs.translatePath('special://home/addons/')
PACKAGES_PATH = xbmcvfs.translatePath('special://home/addons/packages/')

# Arctic Fuse Skin source
SKIN_GITHUB_API = 'https://api.github.com/repos/jurialmunkey/skin.arctic.fuse.3/releases/latest'
SKIN_ZIP_URL = 'https://github.com/jurialmunkey/skin.arctic.fuse.3/archive/refs/tags/v{version}.zip'


def log(msg, level=xbmc.LOGINFO):
    xbmc.log(f'[{ADDON_ID}] {msg}', level)


log("Service loading...")


def url_get(url, timeout=15):
    """Download URL content"""
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        
        req = urllib.request.Request(url, headers={'User-Agent': 'Kodi/20.0'})
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as response:
            return response.read()
    except Exception as e:
        log(f"URL fetch error for {url}: {e}")
        return None


def download_file(url, dest, progress_dialog, title="מוריד..."):
    """Download file with resume support, retries, and progress display"""
    try:
        if requests is None:
            log("requests module not available, falling back to url_get")
            data = url_get(url, timeout=120)
            if data:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, 'wb') as f:
                    f.write(data)
                return True
            return False

        path = os.path.split(dest)[0]
        if not os.path.exists(path):
            os.makedirs(path)

        log(f"Downloading: {url}")

        mb = 1024 * 1024
        chunk_size = 512 * 1024
        max_retries = 3
        backoff_ms = 1200

        for attempt in range(1, max_retries + 1):
            try:
                resume_from = 0
                mode = 'wb'
                headers = {'user-agent': USER_AGENT}

                if os.path.exists(dest):
                    try:
                        resume_from = os.path.getsize(dest)
                    except:
                        resume_from = 0

                if resume_from > 0:
                    headers['Range'] = f'bytes={resume_from}-'
                    mode = 'ab'

                start_time = time.time()
                last_ui = 0.0
                total_size = None

                with requests.get(
                    url,
                    headers=headers,
                    timeout=(10, 120),
                    stream=True,
                    allow_redirects=True
                ) as response:

                    if not response:
                        raise Exception("No response")

                    if response.status_code == 416:
                        if os.path.exists(dest) and os.path.getsize(dest) > 0:
                            log("Already fully downloaded (416).")
                            return True
                        raise Exception("Range not satisfiable")

                    response.raise_for_status()

                    cl = response.headers.get('content-length')
                    cr = response.headers.get('content-range')

                    if cr and '/' in cr:
                        try:
                            total_size = int(cr.split('/')[-1])
                        except:
                            total_size = None
                    elif cl:
                        try:
                            total_size = int(cl) + (resume_from if response.status_code == 206 else 0)
                        except:
                            total_size = None

                    with open(dest, mode) as f:
                        downloaded = resume_from

                        for chunk in response.iter_content(chunk_size=chunk_size):
                            if not chunk:
                                continue
                            f.write(chunk)
                            downloaded += len(chunk)

                            now = time.time()
                            if now - last_ui < 0.15 and total_size:
                                continue
                            last_ui = now

                            if total_size and total_size > 0:
                                done = int(100 * downloaded / total_size)
                            else:
                                done = 0

                            elapsed = max(now - start_time, 0.001)
                            bps = (downloaded - resume_from) / elapsed
                            eta = int((total_size - downloaded) / bps) if (total_size and bps > 0 and downloaded < total_size) else 0

                            speed = bps / 1024
                            unit = 'KB'
                            if speed >= 1024:
                                speed /= 1024
                                unit = 'MB'

                            if total_size:
                                currently_downloaded = f'[COLOR yellow][B]גודל:[/B] [COLOR lime]{downloaded/mb:.2f}[/COLOR] MB מתוך [COLOR lime]{total_size/mb:.2f}[/COLOR] MB[/COLOR]'
                            else:
                                currently_downloaded = f'[COLOR yellow][B]גודל:[/B] [COLOR lime]{downloaded/mb:.2f}[/COLOR] MB[/COLOR]'

                            div = divmod(eta, 60)
                            speed_line = f'[COLOR yellow][B]מהירות:[/B] [COLOR cyan]{speed:.2f}[/COLOR] {unit}/s'
                            if total_size:
                                speed_line += f' | [B]זמן:[/B] [COLOR orange]{div[0]:02d}:{div[1]:02d}[/COLOR]'
                            speed_line += '[/COLOR]'

                            progress_dialog.update(done, f'{title}\n' + currently_downloaded + '\n' + speed_line)

                if total_size and os.path.exists(dest) and os.path.getsize(dest) >= total_size:
                    log(f"Downloaded: {dest}")
                    return True

                if (not total_size) and os.path.exists(dest) and os.path.getsize(dest) > 0:
                    log(f"Downloaded (unknown size): {dest}")
                    return True

                raise Exception("Download incomplete")

            except Exception as e:
                log(f"Download attempt {attempt}/{max_retries} failed: {e}", xbmc.LOGWARNING)
                if attempt < max_retries:
                    try:
                        xbmc.sleep(backoff_ms * attempt)
                    except:
                        pass
                    continue
                return False

    except Exception as e:
        log(f"Download error: {e}", xbmc.LOGERROR)
        return False


def _cleanup_old_addon_dirs():
    """Remove stale 'plugin.video.gears_old_<timestamp>' style backup folders.

    perform_addon_updates() falls back to renaming an in-use add-on to
    '<id>_old_<ts>' when it can't be deleted. Those folders are never
    cleaned up otherwise, and Kodi tries to parse each one as an add-on
    (log spam + clutter). We sweep them on every startup -- safe because
    the suffix pattern is specific (an addon id never ends in _old_<digits>).
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
    """Get addon version from addon.xml"""
    try:
        addon_xml = os.path.join(ADDONS_PATH, addon_id, 'addon.xml')
        if os.path.exists(addon_xml):
            with open(addon_xml, 'r', encoding='utf-8') as f:
                content = f.read()
                match = re.search(r'<addon[^>]*version="([^"]+)"', content)
                if match:
                    return match.group(1)
    except:
        pass
    return None


def version_compare(v1, v2):
    """Compare versions. Returns: 1 if v1>v2, -1 if v1<v2, 0 if equal"""
    try:
        def normalize(v):
            parts = []
            for part in v.split('.'):
                num = ''.join(c for c in part if c.isdigit())
                parts.append(int(num) if num else 0)
            return parts
        
        v1_parts = normalize(v1)
        v2_parts = normalize(v2)
        
        max_len = max(len(v1_parts), len(v2_parts))
        v1_parts.extend([0] * (max_len - len(v1_parts)))
        v2_parts.extend([0] * (max_len - len(v2_parts)))
        
        for i in range(max_len):
            if v1_parts[i] > v2_parts[i]:
                return 1
            elif v1_parts[i] < v2_parts[i]:
                return -1
        return 0
    except:
        return 0


def get_skin_online_version():
    """Get latest Arctic Fuse skin version from GitHub releases"""
    try:
        data = url_get(SKIN_GITHUB_API)
        if not data:
            log("Could not fetch skin releases")
            return None, None
        
        release = json.loads(data.decode('utf-8'))
        tag_name = release.get('tag_name', '')
        
        # Tag is usually "v3.1.15" - remove the 'v'
        version = tag_name.lstrip('v')
        
        if version:
            zip_url = SKIN_ZIP_URL.format(version=version)
            log(f"Skin latest version: {version}")
            return version, zip_url
        
    except Exception as e:
        log(f"Error getting skin version: {e}")
    
    return None, None


class POVHebrewService(xbmc.Monitor):
    def __init__(self):
        super().__init__()
        log("Service initialized")
        
        self.pov_version = get_addon_version('plugin.video.pov')
        self.gears_version = get_addon_version('plugin.video.gears')
        self.skin_version = get_addon_version('skin.arctic.fuse.3')

        log(f"Initial versions - Gears: {self.gears_version}, POV: {self.pov_version}, Skin: {self.skin_version}")
    
    def onNotification(self, sender, method, data):
        """Handle Kodi notifications - detect addon updates/reinstalls"""
        
        log(f"Notification: sender={sender}, method={method}")
        
        if method in ('Addon.OnInstalled', 'Addon.OnEnabled'):
            try:
                data_dict = json.loads(data)
                addon_id = data_dict.get('id', '')
                
                log(f"Addon event: {method} for {addon_id}")
                
                # Handle POV install/update/reinstall
                if addon_id == 'plugin.video.pov':
                    xbmc.sleep(3000)
                    new_version = get_addon_version('plugin.video.pov')
                    log(f"POV version: {new_version}")
                    
                    saved_hebrew_ver = ADDON.getSetting('pov_hebrew_version')
                    hebrew_was_installed = saved_hebrew_ver and saved_hebrew_ver != '0' and saved_hebrew_ver != ''
                    
                    if hebrew_was_installed:
                        try:
                            pov_addon = xbmcaddon.Addon('plugin.video.pov')
                            pov_path = pov_addon.getAddonInfo('path')
                            kodirdil_exists = os.path.exists(os.path.join(pov_path, 'resources', 'lib', 'kodirdil'))
                            
                            if not kodirdil_exists:
                                log("Hebrew files missing after POV update - triggering reinstall")
                                self.pov_version = new_version
                                xbmc.sleep(1000)
                                self.reinstall_pov_hebrew(new_version)
                        except Exception as e:
                            log(f"Error checking POV path: {e}")
                    
                    self.pov_version = new_version
                
                # Handle Arctic Fuse Skin updates
                elif addon_id == 'skin.arctic.fuse.3':
                    xbmc.sleep(3000)
                    log("Skin Arctic Fuse event detected")
                    
                    saved_hebrew_ver = ADDON.getSetting('skin_hebrew_version')
                    hebrew_was_installed = saved_hebrew_ver and saved_hebrew_ver != '0' and saved_hebrew_ver != ''
                    
                    if hebrew_was_installed:
                        skin_path = os.path.join(ADDONS_PATH, 'skin.arctic.fuse.3')
                        font_exists = os.path.exists(os.path.join(skin_path, 'fonts', 'Rubik-VariableFont_wght.ttf'))
                        
                        if not font_exists:
                            log("Hebrew files missing after Skin update - triggering reinstall")
                            xbmc.sleep(1000)
                            self.reinstall_skin_hebrew()
                        
            except Exception as e:
                log(f"Error handling notification: {e}", xbmc.LOGERROR)
    
    def reinstall_skin_hebrew(self):
        """Reinstall Hebrew files for Arctic Fuse Skin after update"""
        if not ADDON.getSettingBool('auto_reinstall_skin'):
            log("Auto reinstall for Skin is disabled")
            return
        
        try:
            from resources.libs.installer import ArcticFuseHebrewInstaller
            installer = ArcticFuseHebrewInstaller()
            
            dialog = xbmcgui.Dialog()
            dialog.notification(ADDON_NAME, "מעדכן עברית לסקין...", xbmcgui.NOTIFICATION_INFO, 3000)
            
            xbmc.sleep(2000)
            
            progress = xbmcgui.DialogProgress()
            progress.create(ADDON_NAME, "מתקין מחדש קבצי עברית לסקין...")
            
            success = installer.install(progress_callback=lambda msg, pct: progress.update(pct, msg))
            progress.close()
            
            if success:
                try:
                    GITHUB_BASE = "https://raw.githubusercontent.com/asaf27064/pov-modified-heb/main/"
                    online_data = url_get(GITHUB_BASE + 'skin_version.json')
                    if online_data:
                        online_info = json.loads(online_data.decode('utf-8'))
                        new_ver = online_info.get('version', '1.0.0')
                        ADDON.setSetting('skin_hebrew_version', new_ver)
                except:
                    pass
                
                dialog.ok(ADDON_NAME, "[COLOR lime]עברית לסקין הותקנה מחדש בהצלחה![/COLOR]\n\nKodi יופעל מחדש.")
                xbmc.executebuiltin('Quit')
            else:
                dialog.ok(ADDON_NAME, "[COLOR red]התקנת עברית נכשלה![/COLOR]")
                
        except Exception as e:
            log(f"Error reinstalling Skin Hebrew: {e}", xbmc.LOGERROR)
    
    def reinstall_pov_hebrew(self, new_version):
        """Reinstall Hebrew files for POV after update"""
        if not ADDON.getSettingBool('auto_reinstall_pov'):
            log("Auto reinstall for POV is disabled")
            return
        
        try:
            from resources.libs.installer import POVHebrewInstaller
            installer = POVHebrewInstaller()
            
            pov_hebrew_version = ADDON.getSetting('pov_hebrew_version')
            hebrew_was_installed = pov_hebrew_version and pov_hebrew_version != '0' and pov_hebrew_version != ''
            
            if not hebrew_was_installed:
                log("Hebrew was not installed for POV, skipping")
                return
            
            dialog = xbmcgui.Dialog()
            dialog.notification(ADDON_NAME, f"POV עודכן ל-{new_version}. מתקין עברית...", xbmcgui.NOTIFICATION_INFO, 3000)
            
            xbmc.sleep(2000)
            
            progress = xbmcgui.DialogProgress()
            progress.create(ADDON_NAME, f"POV עודכן ל-{new_version}\nמתקין מחדש קבצי עברית...")
            
            success = installer.install(progress_callback=lambda msg, pct: progress.update(pct, msg))
            progress.close()
            
            if success:
                ADDON.setSetting('last_pov_version', new_version)
                
                try:
                    GITHUB_BASE = "https://raw.githubusercontent.com/asaf27064/pov-modified-heb/main/"
                    online_data = url_get(GITHUB_BASE + 'version.json')
                    if online_data:
                        online_info = json.loads(online_data.decode('utf-8'))
                        heb_ver = online_info.get('version', '1.0.0')
                        ADDON.setSetting('pov_hebrew_version', heb_ver)
                except:
                    pass
                
                dialog.ok(ADDON_NAME, f"[COLOR lime]POV עודכן ל-{new_version}[/COLOR]\n\nעברית הותקנה מחדש!\n\nKodi יופעל מחדש.")
                xbmc.executebuiltin('Quit')
            else:
                dialog.ok(ADDON_NAME, "[COLOR red]התקנת עברית נכשלה![/COLOR]")
                
        except Exception as e:
            log(f"Error reinstalling POV Hebrew: {e}", xbmc.LOGERROR)
    
    def check_wizard_self_update(self):
        """Check if a newer wizard version exists and install it directly.
        
        Downloads the ZIP from the repo and extracts it over the current
        installation. Does NOT rely on Kodi's auto-update system at all.
        """
        try:
            current = ADDON.getAddonInfo('version')
            
            # Fetch latest version from repo addons.xml
            content = url_get('https://asaf27064.github.io/addons.xml')
            if not content:
                log("Could not fetch addons.xml for self-update check")
                return
            
            text = content.decode('utf-8', errors='replace') if isinstance(content, bytes) else content
            match = re.search(
                r'<addon[^>]+id="plugin\.program\.masterkodi\.il\.wizard"[^>]+version="([^"]+)"',
                text
            )
            if not match:
                log("Wizard not found in addons.xml")
                return
            
            latest = match.group(1)
            log(f"Wizard self-update check - Current: {current}, Repo: {latest}")
            
            if version_compare(latest, current) <= 0:
                log("Wizard is up to date")
                return
            
            log(f"Wizard update available: {current} -> {latest}")
            xbmcgui.Dialog().notification(
                ADDON_NAME, f'מעדכן Wizard ל-{latest}...',
                xbmcgui.NOTIFICATION_INFO, 3000
            )
            
            # Download the wizard ZIP directly from the repo
            zip_url = f'https://asaf27064.github.io/zips/{ADDON_ID}/{ADDON_ID}-{latest}.zip'
            log(f"Downloading wizard from: {zip_url}")
            
            zip_data = url_get(zip_url, timeout=30)
            if not zip_data:
                log("Failed to download wizard ZIP", xbmc.LOGWARNING)
                return
            
            # Save to temp file
            temp_zip = os.path.join(
                xbmcvfs.translatePath('special://temp/'),
                f'{ADDON_ID}-{latest}.zip'
            )
            with open(temp_zip, 'wb') as f:
                f.write(zip_data)
            
            log(f"Downloaded {len(zip_data)} bytes to {temp_zip}")
            
            # Extract to temp directory first
            temp_extract = os.path.join(
                xbmcvfs.translatePath('special://temp/'),
                'wizard_update'
            )
            if os.path.exists(temp_extract):
                shutil.rmtree(temp_extract)
            
            with zipfile.ZipFile(temp_zip, 'r') as zf:
                zf.extractall(temp_extract)
            
            # Find the addon root inside extracted files
            addon_root = None
            for item in os.listdir(temp_extract):
                item_path = os.path.join(temp_extract, item)
                if os.path.isdir(item_path) and os.path.exists(os.path.join(item_path, 'addon.xml')):
                    addon_root = item_path
                    break
            
            if not addon_root:
                log("Could not find addon.xml in downloaded ZIP", xbmc.LOGWARNING)
                return
            
            # Replace current addon files
            target = os.path.join(ADDONS_PATH, ADDON_ID)
            
            if os.path.exists(target):
                shutil.rmtree(target)
            
            shutil.copytree(addon_root, target)
            
            log(f"Wizard updated: {current} -> {latest}")
            
            # Update repo cache in DB directly (UpdateAddonRepos is async and unreliable)
            try:
                db_path = xbmcvfs.translatePath('special://database/')
                addon_db = None
                for f in os.listdir(db_path):
                    if f.startswith('Addons') and f.endswith('.db'):
                        addon_db = os.path.join(db_path, f)
                if addon_db:
                    import sqlite3
                    conn = sqlite3.connect(addon_db)
                    conn.execute(
                        'UPDATE addons SET version = ? WHERE addonID = ?',
                        (latest, ADDON_ID)
                    )
                    conn.commit()
                    conn.close()
                    log(f"Updated addons table cache to {latest}")
            except Exception as e:
                log(f"DB cache update failed (cosmetic only): {e}", xbmc.LOGWARNING)
            
            # Tell Kodi to refresh addon list
            xbmc.executebuiltin('UpdateLocalAddons')
            
            # Cleanup
            try:
                os.remove(temp_zip)
                shutil.rmtree(temp_extract, ignore_errors=True)
            except:
                pass
            
            xbmcgui.Dialog().notification(
                ADDON_NAME, f'Wizard עודכן ל-{latest}!',
                xbmcgui.NOTIFICATION_INFO, 3000
            )
            
        except Exception as e:
            log(f"Wizard self-update error: {e}", xbmc.LOGWARNING)
    
    def run(self):
        """Main service loop"""
        
        # Check if we should skip update check (after build install)
        skip_check = ADDON.getSetting('skip_update_check')
        if skip_check == 'true':
            log("Skipping update check (after build installation)")
            ADDON.setSetting('skip_update_check', 'false')
            while not self.abortRequested():
                if self.waitForAbort(300):
                    break
            return
        
        # Get configurable delay from settings (default 15 seconds)
        try:
            delay = int(ADDON.getSetting('update_check_delay'))
            if delay < 5:
                delay = 5
            elif delay > 60:
                delay = 60
        except:
            delay = 15
        
        log("Service started, waiting for Kodi to settle...")
        xbmc.sleep(5000)

        # Sweep stale '<addon>_old_<timestamp>' backup dirs from past updates
        # (Kodi tries to parse every folder under addons/ as an add-on).
        _cleanup_old_addon_dirs()

        # Manifest-driven update: ONE pass updates every addon (Gears + overlay,
        # AI Subs, skins, and the wizard itself) from the MasterKodi-IL-Build
        # manifest, verifying each sha256 before installing. Replaces the old
        # per-addon raw-URL checks and the separate wizard self-update.
        if ADDON.getSettingBool('auto_update_check'):
            log("Running manifest update check...")
            try:
                from resources.libs import modular_update
                modular_update.silent_check()
            except Exception as e:
                log(f"manifest update error: {e}", xbmc.LOGERROR)
        else:
            log("Auto update check disabled")

        # Keep service alive to catch notifications
        while not self.abortRequested():
            if self.waitForAbort(300):
                break
        
        log("Service stopped")
    
    def check_all_updates(self, silent_if_none=True):
        """THE single combined update check (replaces the two old separate
        prompts check_all_updates_combined + check_gears_gearsai_updates).

        Gathers every available update -- Gears base (gated) + Gears overlay,
        AI Subs (gearsai), and Skin + its Hebrew -- and shows them in ONE dialog.

        silent_if_none=False also reports "everything up to date" and ignores
        the global auto_update_check toggle (used by the manual button)."""
        if not ADDON.getSettingBool('auto_update_check') and silent_if_none:
            log("Auto update check is disabled")
            return

        GBASE = "https://raw.githubusercontent.com/asaf27064/pov-modified-heb/main/"
        addon_updates, hebrew_updates = [], []

        # ---- Gears base (gated) + overlay-only ----
        if ADDON.getSettingBool('auto_reinstall_gears'):
            try:
                g = self.check_gears_update()
                if g:
                    addon_updates.append(g)
                else:
                    from resources.libs.installer import GearsHebrewInstaller
                    gi = GearsHebrewInstaller()
                    if gi.is_installed():
                        data = url_get(GBASE + 'gears_version.json')
                        if data:
                            info = json.loads(data.decode('utf-8'))
                            online = info.get('version', '0')
                            compatible = info.get('compatible_gears', '0')
                            local = gi.get_installed_version()
                            installed_base = get_addon_version('plugin.video.gears') or '0'
                            # overlay-only update ONLY when base already matches compatible
                            if version_compare(online, local) > 0 and version_compare(installed_base, compatible) >= 0:
                                hebrew_updates.append({'type': 'gears_hebrew', 'name': 'קבצי עברית Gears',
                                    'current': local, 'online': online, 'installer': gi, 'setting_key': 'gears_hebrew_version'})
            except Exception as e:
                log(f"Gears check: {e}", xbmc.LOGERROR)

        # ---- AI Subs (gearsai) ----
        if ADDON.getSettingBool('auto_install_gearsai'):
            try:
                from resources.libs.installer import GearsaiInstaller
                ai = GearsaiInstaller()
                data = url_get(GBASE + 'gearsai_version.json')
                if data:
                    online = json.loads(data.decode('utf-8')).get('version', '0')
                    local = ai.get_installed_version()
                    if version_compare(online, local) > 0:
                        nm = 'כתוביות AI (תרגום עברית)' if local == '0' else 'עדכון כתוביות AI'
                        hebrew_updates.append({'type': 'gearsai', 'name': nm,
                            'current': local, 'online': online, 'installer': ai, 'setting_key': 'gearsai_version'})
            except Exception as e:
                log(f"gearsai check: {e}", xbmc.LOGERROR)

        # ---- Skin base + Hebrew ----
        if ADDON.getSettingBool('auto_reinstall_skin'):
            s = self.check_skin_update()
            if s:
                addon_updates.append(s)
            else:
                try:
                    from resources.libs.installer import ArcticFuseHebrewInstaller
                    installer = ArcticFuseHebrewInstaller()
                    if installer.is_installed():
                        local_ver = installer.get_installed_version() or '0'
                        if local_ver == 'installed':
                            local_ver = '0'
                        online_data = url_get(GBASE + 'skin_version.json')
                        if online_data:
                            online_info = json.loads(online_data.decode('utf-8'))
                            online_ver = online_info.get('version', '0')
                            if version_compare(online_ver, local_ver) > 0:
                                hebrew_updates.append({'type': 'skin_hebrew', 'name': 'קבצי עברית Skin',
                                    'current': local_ver, 'online': online_ver, 'installer': installer, 'setting_key': 'skin_hebrew_version'})
                except Exception as e:
                    log(f"Skin Hebrew check: {e}", xbmc.LOGERROR)

        # ---- One dialog for everything ----
        if not addon_updates and not hebrew_updates:
            log("Everything up to date")
            if not silent_if_none:
                xbmcgui.Dialog().ok(ADDON_NAME,
                    "[COLOR lime]הכל מעודכן![/COLOR]\n\nאין עדכונים זמינים כרגע.")
            return

        lines = []
        for u in addon_updates:
            lines.append(f"[COLOR cyan]• {u['name']}:[/COLOR] {u.get('current','?')} → [COLOR lime]{u.get('online','?')}[/COLOR]")
        for u in hebrew_updates:
            lines.append(f"[COLOR yellow]• {u['name']}:[/COLOR] {u.get('current','?')} → [COLOR lime]{u.get('online','?')}[/COLOR]")
        note = "\n\n[COLOR gray](עדכון תוסף כולל התקנת עברית אוטומטית)[/COLOR]" if addon_updates else ""

        if not xbmcgui.Dialog().yesno(ADDON_NAME,
                "[COLOR yellow][B]עדכונים זמינים[/B][/COLOR]\n\n" + "\n".join(lines) + note + "\n\nלעדכן עכשיו?",
                yeslabel="עדכן הכל", nolabel="מאוחר יותר"):
            log("User declined updates")
            return

        # ONE pass for EVERYTHING (Hebrew overlay + AI Subs + Gears + Skin, any
        # mix). File/overlay updates run first WITHOUT restarting; the full
        # add-on updates run last and own the single restart. Kodi restarts
        # exactly once, after every item is installed.
        if hebrew_updates and addon_updates:
            heb_done = self.perform_hebrew_updates(hebrew_updates, restart=False)
            restarted = self.perform_addon_updates(addon_updates, restart=True)
            # If the add-ons all failed (so no restart happened) but the file/AI
            # updates DID apply, restart anyway so those take effect now.
            if not restarted and heb_done:
                xbmcgui.Dialog().ok(ADDON_NAME,
                    "[COLOR lime]קבצי העברית / כתוביות AI עודכנו[/COLOR]\n\nKodi יופעל מחדש.")
                xbmc.executebuiltin('Quit')
        elif addon_updates:
            self.perform_addon_updates(addon_updates, restart=True)
        elif hebrew_updates:
            self.perform_hebrew_updates(hebrew_updates, restart=True)

    def check_gears_update(self):
        """Gears BASE update -- GATED: only when our overlay's compatible_gears
        == the LATEST upstream Gears (i.e. we've re-patched + tested it). Returns
        an addon_updates dict (downloaded from chainsrepo) or None."""
        try:
            data = url_get("https://raw.githubusercontent.com/asaf27064/pov-modified-heb/main/gears_version.json")
            if not data:
                return None
            info = json.loads(data.decode('utf-8'))
            compatible = info.get('compatible_gears', '0')
            ax = url_get("https://raw.githubusercontent.com/unhingedthemes/zips/main/_zips/addons.xml")
            if not ax:
                return None
            import re as _re
            m = _re.search(r'id="plugin\.video\.gears"[^>]*version="([^"]+)"', ax.decode('utf-8', 'replace'))
            upstream = m.group(1) if m else '0'
            # GATE: never move to a version our overlay doesn't support
            if compatible in ('0', '') or upstream in ('0', '') or compatible != upstream:
                log(f"Gears gate closed (compatible={compatible}, upstream={upstream})")
                return None
            installed_base = get_addon_version('plugin.video.gears') or '0'
            if version_compare(compatible, installed_base) > 0:
                return {
                    'type': 'gears', 'name': 'Gears', 'current': installed_base, 'online': compatible,
                    'zip_url': f"https://raw.githubusercontent.com/unhingedthemes/zips/main/_zips/plugin.video.gears/plugin.video.gears-{compatible}.zip",
                }
        except Exception as e:
            log(f"check_gears_update error: {e}", xbmc.LOGERROR)
        return None

    def perform_hebrew_updates(self, updates, restart=True):
        """Perform Hebrew-only file updates. When restart=False the caller will
        follow up with add-on updates and trigger the single restart itself."""
        progress = xbmcgui.DialogProgress()
        progress.create(ADDON_NAME, "מעדכן קבצי עברית...")
        
        success_count = 0
        total = len(updates)
        
        for i, update in enumerate(updates):
            progress.update(int((i / total) * 100), f"מעדכן {update['name']}...")
            
            try:
                installer = update['installer']
                
                if hasattr(installer, 'install'):
                    result = installer.install(progress_callback=lambda msg, pct: None)
                elif hasattr(installer, 'install_hebrew_files'):
                    result = installer.install_hebrew_files(progress_callback=lambda msg, pct: None)
                else:
                    result = False
                
                if result:
                    ADDON.setSetting(update['setting_key'], update['online'])
                    success_count += 1
                    log(f"{update['name']} updated to {update['online']}")
            except Exception as e:
                log(f"Error updating {update['name']}: {e}", xbmc.LOGERROR)
        
        progress.close()

        if not restart:
            # Add-on updates still to come in this same pass -- don't restart
            # yet, and don't pop a dialog (the add-on step shows the final one).
            return success_count

        dialog = xbmcgui.Dialog()
        if success_count > 0:
            dialog.ok(
                ADDON_NAME,
                f"[COLOR lime]עודכנו {success_count}/{total} קבצי עברית[/COLOR]\n\nKodi יופעל מחדש."
            )
            xbmc.executebuiltin('Quit')
        else:
            dialog.ok(ADDON_NAME, "[COLOR red]עדכון עברית נכשל![/COLOR]")
    
    def check_skin_update(self):
        """Check if Arctic Fuse Skin has an update available"""
        skin_path = os.path.join(ADDONS_PATH, 'skin.arctic.fuse.3')
        if not os.path.exists(skin_path):
            log("Skin not installed")
            return None
        
        try:
            from resources.libs.installer import ArcticFuseHebrewInstaller
            installer = ArcticFuseHebrewInstaller()
            if not installer.is_installed():
                log("Hebrew not installed for Skin, skipping")
                return None
        except Exception as e:
            log(f"Error checking Skin Hebrew: {e}")
            return None
        
        current_version = get_addon_version('skin.arctic.fuse.3')
        if not current_version:
            return None
        
        log(f"Current Skin version: {current_version}")
        
        online_version, zip_url = get_skin_online_version()
        
        if not online_version:
            log("Could not get online Skin version")
            return None
        
        log(f"Online Skin version: {online_version}")
        
        if version_compare(online_version, current_version) <= 0:
            log("Skin is up to date")
            return None
        
        return {
            'type': 'skin',
            'name': 'Arctic Fuse Skin',
            'current': current_version,
            'online': online_version,
            'zip_url': zip_url,
            'source': 'GitHub'
        }
    
    def perform_addon_updates(self, updates, restart=True):
        """Perform the actual addon updates (full add-on replace + its Hebrew).
        restart=True quits Kodi at the end (always the case when called as the
        last step of a combined update)."""
        progress = xbmcgui.DialogProgress()
        progress.create(ADDON_NAME, "מעדכן...")
        
        success_list = []
        failed_list = []
        
        # 3 steps per addon: extract, install, hebrew (download has its own progress)
        total_steps = len(updates) * 3
        current_step = 0
        
        for update in updates:
            addon_name = update['name']
            version = update['online']
            zip_url = update['zip_url']
            
            try:
                # Determine addon info
                if update['type'] == 'gears':
                    addon_id = 'plugin.video.gears'
                    zip_name = f'plugin.video.gears-{version}.zip'
                else:
                    addon_id = 'skin.arctic.fuse.3'
                    zip_name = f'skin.arctic.fuse.3-{version}.zip'
                
                zip_location = os.path.join(PACKAGES_PATH, zip_name)
                os.makedirs(PACKAGES_PATH, exist_ok=True)
                
                # Remove old partial download if exists
                if os.path.exists(zip_location):
                    try:
                        os.remove(zip_location)
                    except:
                        pass
                
                # Download with progress, resume, and retries
                download_title = f"מוריד {addon_name} {version}"
                if not download_file(zip_url, zip_location, progress, download_title):
                    raise Exception("ההורדה נכשלה")
                
                # Extract to temp location first
                current_step += 1
                progress.update(int((current_step / total_steps) * 100), f"מחלץ {addon_name}...")
                
                temp_extract = os.path.join(PACKAGES_PATH, f'temp_{addon_id}')
                if os.path.exists(temp_extract):
                    shutil.rmtree(temp_extract)
                
                with zipfile.ZipFile(zip_location, 'r') as zf:
                    zf.extractall(temp_extract)
                
                # Find the extracted folder (might have version in name)
                extracted_items = os.listdir(temp_extract)
                if extracted_items:
                    extracted_folder = os.path.join(temp_extract, extracted_items[0])
                    if os.path.isdir(extracted_folder):
                        source_folder = extracted_folder
                    else:
                        source_folder = temp_extract
                else:
                    raise Exception("חילוץ נכשל")
                
                # Install new version
                current_step += 1
                progress.update(int((current_step / total_steps) * 100), f"מתקין {addon_name} חדש...")
                
                addon_path = os.path.join(ADDONS_PATH, addon_id)
                
                if update['type'] == 'skin':
                    # For skin: overwrite files instead of delete (skin is in use!)
                    # First, clear the existing folder contents
                    if os.path.exists(addon_path):
                        log(f"Clearing contents of {addon_path}")
                        for item in os.listdir(addon_path):
                            item_path = os.path.join(addon_path, item)
                            try:
                                if os.path.isdir(item_path):
                                    shutil.rmtree(item_path)
                                else:
                                    os.remove(item_path)
                            except Exception as e:
                                log(f"Could not remove {item_path}: {e}")
                    else:
                        os.makedirs(addon_path)
                    
                    # Copy new files (skip git files like Kodi does)
                    git_files = {'.git', '.github', '.gitattributes', '.gitignore', '.gitmodules'}
                    log(f"Copying new files from {source_folder} to {addon_path}")
                    for item in os.listdir(source_folder):
                        if item in git_files:
                            log(f"Skipping git file: {item}")
                            continue
                        src = os.path.join(source_folder, item)
                        dst = os.path.join(addon_path, item)
                        try:
                            if os.path.isdir(src):
                                shutil.copytree(src, dst)
                            else:
                                shutil.copy2(src, dst)
                        except Exception as e:
                            log(f"Error copying {item}: {e}")
                    
                    log(f"Installed new skin at {addon_path}")
                else:
                    # For Gears base: regular delete and move (addon not in use)
                    if os.path.exists(addon_path):
                        for attempt in range(5):
                            try:
                                shutil.rmtree(addon_path)
                                log(f"Removed old {addon_id}")
                                break
                            except Exception as e:
                                log(f"Remove attempt {attempt + 1} failed: {e}")
                                xbmc.sleep(500)
                        else:
                            try:
                                old_backup = addon_path + '_old_' + str(int(time.time()))
                                shutil.move(addon_path, old_backup)
                                log(f"Moved old addon to {old_backup}")
                            except Exception as e:
                                raise Exception(f"לא ניתן להסיר את הגרסה הישנה: {e}")
                    
                    # Remove git files from source before moving
                    git_files = {'.git', '.github', '.gitattributes', '.gitignore', '.gitmodules'}
                    for git_item in git_files:
                        git_path = os.path.join(source_folder, git_item)
                        if os.path.exists(git_path):
                            try:
                                if os.path.isdir(git_path):
                                    shutil.rmtree(git_path)
                                else:
                                    os.remove(git_path)
                                log(f"Removed git file: {git_item}")
                            except:
                                pass
                    
                    shutil.move(source_folder, addon_path)
                    log(f"Installed new {addon_id} at {addon_path}")
                
                # Cleanup
                try:
                    shutil.rmtree(temp_extract)
                    os.remove(zip_location)
                except:
                    pass
                
                # Install Hebrew
                current_step += 1
                progress.update(int((current_step / total_steps) * 100), f"מתקין עברית ל-{addon_name}...")
                
                if update['type'] == 'gears':
                    from resources.libs.installer import GearsHebrewInstaller
                    installer = GearsHebrewInstaller()
                    hebrew_success = installer.install_hebrew_files(progress_callback=lambda msg, pct: None)
                    if hebrew_success:
                        ADDON.setSetting('last_gears_version', version)
                else:
                    from resources.libs.installer import ArcticFuseHebrewInstaller
                    installer = ArcticFuseHebrewInstaller()
                    hebrew_success = installer.install(progress_callback=lambda msg, pct: None)
                    if hebrew_success:
                        ADDON.setSetting('last_skin_version', version)
                
                if hebrew_success:
                    success_list.append(f"{addon_name} {version}")
                else:
                    success_list.append(f"{addon_name} {version} (עברית נכשלה)")
                
            except Exception as e:
                log(f"Error updating {addon_name}: {e}", xbmc.LOGERROR)
                failed_list.append(addon_name)
                current_step += (3 - (current_step % 3))
                try:
                    temp_extract = os.path.join(PACKAGES_PATH, f'temp_{addon_id}')
                    if os.path.exists(temp_extract):
                        shutil.rmtree(temp_extract)
                except:
                    pass
        
        progress.close()
        
        # Show result
        dialog = xbmcgui.Dialog()
        
        if success_list:
            success_text = "\n".join([f"✓ {item}" for item in success_list])
            failed_text = "\n".join([f"✗ {item}" for item in failed_list]) if failed_list else ""
            
            msg = f"[COLOR lime]עודכנו בהצלחה:[/COLOR]\n{success_text}"
            if failed_text:
                msg += f"\n\n[COLOR red]נכשלו:[/COLOR]\n{failed_text}"
            if restart:
                msg += "\n\nKodi יופעל מחדש."

            dialog.ok(ADDON_NAME, msg)
            if restart:
                xbmc.executebuiltin('Quit')
            return bool(restart)  # True only if we actually issued the restart
        else:
            dialog.ok(ADDON_NAME, "[COLOR red]כל העדכונים נכשלו![/COLOR]")
            return False


def run_update_check():
    """Manual 'Check for updates now' entry (called from the wizard UI).
    Reuses the exact same combined check the startup service runs, but
    reports when everything is already up to date."""
    POVHebrewService().check_all_updates(silent_if_none=False)


if __name__ == '__main__':
    service = POVHebrewService()
    service.run()
