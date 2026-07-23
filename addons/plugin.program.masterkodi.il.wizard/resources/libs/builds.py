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
        for name, desc, img in self.items:
            li = xbmcgui.ListItem(name, desc)
            li.setArt({'icon': img, 'thumb': img})
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
        """Wipe Kodi - delete everything except wizard and My_Builds"""
        log("Starting wipe...")
        
        exclude_dirs = [ADDON_ID, 'packages', 'My_Builds', 'temp', 'cache']
        
        total_files = 0
        for root, dirs, files in os.walk(HOME):
            dirs[:] = [d for d in dirs if d not in exclude_dirs]
            total_files += len(files)
        
        del_file = 0
        progress_dialog.update(0, "[COLOR yellow]מנקה קבצים ותיקיות...[/COLOR]")
        
        for root, dirs, files in os.walk(HOME, topdown=True):
            dirs[:] = [d for d in dirs if d not in exclude_dirs]
            
            for name in files:
                del_file += 1
                filepath = os.path.join(root, name)
                
                if name.endswith('.log') or name.endswith('.old.log'):
                    continue
                
                try:
                    os.remove(filepath)
                except Exception:
                    pass
                
                if del_file % 100 == 0:
                    pct = min(int(del_file * 100 / max(total_files, 1)), 100)
                    progress_dialog.update(pct, f"[COLOR yellow]מוחק קבצים...[/COLOR]\n{del_file}/{total_files}")
        
        progress_dialog.update(95, "[COLOR yellow]מנקה תיקיות ריקות...[/COLOR]")
        for root, dirs, files in os.walk(HOME, topdown=False):
            dirs[:] = [d for d in dirs if d not in exclude_dirs]
            for name in dirs:
                dirpath = os.path.join(root, name)
                if name not in ["Database", "userdata", "temp", "addons", "addon_data"]:
                    try:
                        if not os.listdir(dirpath):
                            os.rmdir(dirpath)
                    except Exception:
                        pass
        
        log("Wipe complete")

    def validate_build_zip(self, zip_path):
        """Is this a complete, readable build zip? Returns (ok, reason).

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
                # A zip's central directory lives at the END of the file, so a
                # successful open already proves the download wasn't truncated.
                # We deliberately do NOT testzip() the whole archive (it would
                # decompress all ~60 MB and add tens of seconds on a weak Android
                # box), BUT we DO CRC-check the boot-CRITICAL members now, before
                # the wipe: guisettings.xml and every addon.xml. Those are small,
                # so it's cheap, and a corrupt one would otherwise pass this
                # structural check and only blow up AFTER the box is already wiped.
                if not any(n.startswith('addons/') for n in names):
                    return False, 'לא נמצאו אדונים בקובץ'
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
        
        log(f"Skin extraction complete. Extracted: {extracted}, Errors: {errors}")
        return True, errors

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
        for i, item in enumerate(zin.infolist()):
            filename = item.filename

            if filename.startswith(_skip_code):
                continue

            if ADDON_ID in filename and not filename.endswith('applied_manifest.json'):
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
        
        zin.close()

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
        """Set the default skin in guisettings.xml"""
        try:
            guisettings = os.path.join(USERDATA, 'guisettings.xml')
            if not os.path.exists(guisettings):
                log("guisettings.xml not found")
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

            # Record the content source NOW, before any config apply. This makes
            # the whole install content-aware: the config engine skips every
            # Gears-specific entry when POV is chosen (no gears menus/favourites/
            # settings/shortcuts ever written), so a POV build stays fully clean
            # -- POV and Gears share no config. A Gears install is unchanged.
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
                    keep_ok, keep_n = keep_mod.backup(keep_keys, extras=keep_extras)
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
                        return False
                    progress.create(ADDON_NAME, "[COLOR cyan]ממשיך בהתקנה...[/COLOR]")

            # Step 3: Wipe
            progress.update(0, "[COLOR yellow]מכין להתקנה...[/COLOR]")
            self.wipe(progress)
            
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

                if success and os.path.exists(skin_zip) and os.path.getsize(skin_zip) > 0:
                    progress.update(0, f"[COLOR yellow]מתקין {skin['name']}...[/COLOR]")

                    # Get skin addons from zip
                    skin_addons = self.grab_addons_from_zip(skin_zip)
                    addon_list.extend(skin_addons)

                    # Use special extraction that merges database
                    success, _ = self.extract_and_merge_skin(skin_zip, progress, f"מתקין {skin['name']}...")

                    if success:
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

                    try:
                        os.remove(skin_zip)
                    except Exception:
                        pass
                else:
                    log(f"Failed to download {skin['name']} skin")
                    skin_name = "Estuary"
                    ADDON.setSetting('installed_skin', 'Estuary')
            else:
                ADDON.setSetting('installed_skin', 'Estuary')
            
            # Step 6: Enable addons in database
            progress.update(90, "[COLOR yellow]מפעיל אדונים...[/COLOR]")
            self.enable_addons_in_db(addon_list)
            self.setup_wizard_repo_in_db()
            
            # Step 7: Update
            progress.update(95, "[COLOR yellow]מעדכן...[/COLOR]")
            xbmc.executebuiltin('UpdateAddonRepos()')
            xbmc.executebuiltin('UpdateLocalAddons()')

            # Step 7.5: restore the 'keep' selections onto the fresh build
            if keep_keys:
                try:
                    from resources.libs import keep as keep_mod
                    progress.update(96, "[COLOR yellow]משחזר נתונים שנשמרו...[/COLOR]")
                    restored_extras, restore_failed = keep_mod.restore()
                    # register + enable any restored user addons
                    if restored_extras:
                        self.enable_addons_in_db(restored_extras)
                        xbmc.executebuiltin('UpdateLocalAddons()')
                    # tell the user if some kept data didn't come back -- the
                    # backup was deliberately NOT deleted so it can be recovered.
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
                except Exception as e:
                    log(f"keep restore failed: {e}", xbmc.LOGWARNING)

            # Step 8: Complete the build from the manifest BEFORE we exit, so the
            # first re-launch already shows our full defaults. The base zip ships
            # STOCK skins (e.g. vanilla Estuary); our MODIFIED skins -- the power
            # menu, home arrangement, skin-switch button -- and the config live in
            # the manifest. Applying it now (while the user is already waiting on
            # the install) means re-entry is complete, with no extra restart.
            try:
                progress.update(97, "[COLOR yellow]מחיל את ברירות המחדל של הבילד...[/COLOR]")
                from resources.libs import modular_update as mu
                mu.run_update(silent=True, no_reload=True)
            except Exception as e:
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

            # Content source: if the user picked POV at install, apply the POV
            # variant for the chosen skin on top of the (Gears) build. Explicit
            # skin id (the new skin isn't active until restart), no reload (the
            # install restart applies it). Fail-open: a POV problem leaves the
            # working Gears build untouched.
            if content_choice == 'pov':
                try:
                    from resources.libs import content_source
                    target_skin = skin['id'] if skin else 'skin.estuary'
                    progress.update(98, "[COLOR yellow]מחיל מקור תוכן POV...[/COLOR]")
                    content_source.install_apply(target_skin, 'pov')
                except Exception as e:
                    log(f"install POV apply failed: {e}", xbmc.LOGWARNING)
            else:
                try:
                    import xbmcaddon as _xa
                    _xa.Addon().setSetting('content_source', 'gears')
                except Exception:
                    pass

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
                    if aid == addon_id:
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
        """Delete an addon's rows from every Addons*.db (so Kodi forgets it)."""
        try:
            import sqlite3
            dbdir = xbmcvfs.translatePath('special://database/')
            for f in os.listdir(dbdir):
                if f.startswith('Addons') and f.endswith('.db'):
                    c = sqlite3.connect(os.path.join(dbdir, f))
                    for t in ('installed', 'addons', 'repo'):
                        try:
                            c.execute('DELETE FROM %s WHERE addonID=?' % t, (aid,))
                        except Exception:
                            pass
                    c.commit(); c.close()
        except Exception as e:
            log(f"_db_remove_addon {aid} failed: {e}", xbmc.LOGWARNING)

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
            xbmc.executebuiltin('UpdateLocalAddons()')
            log(f"removed skin {skin_id}")
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
            '- עמעום בזמן השהיה',
            yeslabel='כן, יש לי OLED',
            nolabel='לא'
        )
        
        if not result:
            return
        
        log("User has OLED - modifying guisettings.xml")
        
        try:
            guisettings_path = os.path.join(
                xbmcvfs.translatePath('special://home/'),
                'userdata',
                'guisettings.xml'
            )
            
            if not os.path.exists(guisettings_path):
                log(f"guisettings.xml not found at {guisettings_path}")
                return
            
            with open(guisettings_path, 'r', encoding='utf-8') as f:
                file_content = f.read()
            
            oled_settings = {
                'screensaver.mode': 'screensaver.xbmc.builtin.black',
                'screensaver.time': '1',
                'screensaver.disableforaudio': 'false',
                'screensaver.usedimonpause': 'true'
            }
            
            import re
            for setting_id, setting_value in oled_settings.items():
                pattern = rf'<setting id="{setting_id}"[^>]*>[^<]*</setting>'
                replacement = f'<setting id="{setting_id}">{setting_value}</setting>'
                
                if re.search(pattern, file_content):
                    file_content = re.sub(pattern, replacement, file_content)
                    log(f"Updated {setting_id} to {setting_value}")
                else:
                    file_content = file_content.replace('</settings>', f'    {replacement}\n</settings>')
                    log(f"Added {setting_id} = {setting_value}")
            
            with open(guisettings_path, 'w', encoding='utf-8') as f:
                f.write(file_content)
            
            log("OLED settings applied to guisettings.xml")
            
        except Exception as e:
            log(f"Error applying OLED settings: {e}", xbmc.LOGERROR)

    def _countdown_restart(self, build_name, skin_name):
        """Countdown and restart Kodi"""
        progress = xbmcgui.DialogProgress()
        progress.create(
            "[COLOR lime]ההתקנה הושלמה בהצלחה![/COLOR]",
            f"[COLOR cyan]בילד:[/COLOR] {build_name}\n[COLOR cyan]סקין:[/COLOR] {skin_name}"
        )
        
        for i in range(5, 0, -1):
            pct = int((5 - i) / 5.0 * 100)
            progress.update(pct, f"[COLOR cyan]בילד:[/COLOR] {build_name}\n[COLOR cyan]סקין:[/COLOR] {skin_name}\n\n[B]קודי ייסגר בעוד {i} שניות...[/B]")
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
            skin_options = [('estuary', 'Estuary', 'הרגיל | הכי מהיר | עיצוב פשוט', 'estuary.jpg')]
            if selected_build.get('skin_url'):
                skin_options.append(('arctic', 'Arctic Fuse', 'הכי יפה ומעוצב | הכי כבד | למכשירים חזקים', 'af3.jpg'))
            if selected_build.get('nimbus_skin_url'):
                skin_options.append(('nimbus', 'Nimbus', 'מהיר ויפה יותר מהרגיל | מתאים גם למכשירים חלשים', 'nimbus.jpg'))
            # Arctic Zephyr installs from the manifest (not a build.txt url), so it's
            # always offered here.
            skin_options.append(('zephyr', 'Arctic Zephyr', 'עשיר ומעוצב בסגנון נטפליקס | בינוני-כבד | למכשירים חזקים', 'zephyr.jpg'))

            # Custom picker window with a LARGE live preview of the focused skin.
            # Falls back to the old useDetails select if the window fails.
            preview_dir = os.path.join(xbmcvfs.translatePath(ADDON.getAddonInfo('path')),
                                       'resources', 'media', 'skin_previews')
            picker_items = [(_name, _desc, os.path.join(preview_dir, _img))
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
            _all_keys = [g['key'] for g in keep_mod.GROUPS] + (['extras'] if extras else [])
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
                keep_keys = keep_mod.prompt(extras=extras, default_all=True)
            manager.install_build(selected_build, skin_choice=skin_choice,
                                  keep_keys=keep_keys, keep_extras=extras,
                                  content_choice=content_choice)
            break


# ===================================================================== #
# Skin manager menu
# ===================================================================== #
# (key, display name, addon id, preview image). Estuary is Kodi's built-in
# fallback skin -- always available, never removable.
_SKIN_CATALOG = [
    ('estuary', 'Estuary', 'skin.estuary', 'estuary.jpg'),
    ('arctic', 'Arctic Fuse', 'skin.arctic.fuse.3', 'af3.jpg'),
    ('nimbus', 'Nimbus', 'skin.nimbus', 'nimbus.jpg'),
    ('zephyr', 'Arctic Zephyr', 'skin.arctic.zephyr.2.resurrection.mod', 'zephyr.jpg'),
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
        ]
        sel = wizard_select('סקינים', items)
        if sel == -1:
            return
        if sel == 0:
            _skin_switch_flow()
        elif sel == 1:
            _skin_cleanup_flow()


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
        picker.append((name, tag, os.path.join(preview_dir, img)))
        meta.append((key, name, sid, installed))

    try:
        idx = SkinPickerDialog.pick('בחר סקין', picker)
    except Exception as e:
        log(f"SkinPickerDialog failed ({e}); fallback select", xbmc.LOGWARNING)
        idx = dialog.select('בחר סקין', [f"{n}  ({t})" for n, t, _ in picker])
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
