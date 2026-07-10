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
    BUILD_TXT_URL, TEMP_FOLDER, COLOR_SUCCESS, COLOR_ERROR, COLOR_WARNING
)


USER_AGENT = 'Mozilla/5.0 (Windows NT 6.1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/35.0.1916.153 Safari/537.36 SE 2.X MetaSr 1.0'
ADDON = xbmcaddon.Addon()


def log(msg, level=xbmc.LOGINFO):
    xbmc.log(f'[{ADDON_ID}] Builds: {msg}', level)


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
        
    def fetch_builds_list(self):
        """Fetch list of available builds from build.txt"""
        try:
            if requests is None:
                log("requests module not available", xbmc.LOGERROR)
                return []
            
            log(f"Fetching builds from: {BUILD_TXT_URL}")
            response = requests.get(BUILD_TXT_URL, headers={'user-agent': USER_AGENT}, timeout=10)
            response.raise_for_status()
            
            builds = []
            for line in response.text.strip().split('\n'):
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                
                build_info = {}
                parts = line.split('" ')
                for part in parts:
                    if '="' in part:
                        key, value = part.split('="', 1)
                        build_info[key.strip()] = value.rstrip('"').strip()
                
                if 'name' in build_info and 'url' in build_info:
                    builds.append(build_info)
            
            self.builds = builds
            log(f"Fetched {len(builds)} builds")
            return builds
            
        except Exception as e:
            log(f"Error fetching builds: {e}", xbmc.LOGERROR)
            return []

    def download_file(self, url, dest, progress_dialog, title="מוריד..."):
        """Download file - exact KodiIL style"""
        try:
            if requests is None:
                return False
            
            path = os.path.split(dest)[0]
            if not os.path.exists(path):
                os.makedirs(path)
            
            log(f"Downloading: {url}")
            
            with open(dest, 'wb') as f:
                response = requests.get(url, headers={'user-agent': USER_AGENT}, timeout=10, stream=True)
                
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
                        except:
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
                except:
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
                    except:
                        pass
        
        log("Wipe complete")

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
            
            # Find latest Addons database
            for f in os.listdir(db_path):
                if f.startswith('Addons') and f.endswith('.db'):
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
        don't trigger — only manual 'Check for updates' works.
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
                            # Table might not exist in target, that's ok
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
                except:
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
                except:
                    pass
                    
            except Exception as e:
                log(f"Error extracting/merging database: {e}", xbmc.LOGWARNING)
        
        # Merge ViewModes database
        if viewmodes_db_in_zip:
            try:
                temp_db = os.path.join(TEMP_FOLDER, 'skin_viewmodes.db')
                try:
                    os.remove(temp_db)
                except:
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
                except:
                    pass
                    
            except Exception as e:
                log(f"Error extracting/merging ViewModes database: {e}", xbmc.LOGWARNING)
        
        # Now extract all other files (skip database)
        for i, item in enumerate(zin.infolist()):
            filename = item.filename
            
            # Skip database files (we already merged)
            if 'Database' in filename and filename.endswith('.db'):
                continue
            
            if ADDON_ID in filename:
                continue
            
            if '__pycache__' in filename or filename.endswith('.pyc') or filename.endswith('.pyo'):
                continue
            
            if filename.endswith('.csv'):
                continue
            
            try:
                filename.encode('ascii')
            except:
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
        
        progress_dialog.update(0, f"[COLOR yellow]{title}[/COLOR]")
        
        for i, item in enumerate(zin.infolist()):
            filename = item.filename
            
            if ADDON_ID in filename:
                continue
            
            if '__pycache__' in filename or filename.endswith('.pyc') or filename.endswith('.pyo'):
                continue
            
            if filename.endswith('.csv'):
                continue
            
            try:
                filename.encode('ascii')
            except:
                continue
            
            try:
                zin.extract(item, dest)
                extracted += 1
            except Exception as e:
                errors += 1
            
            if i % 50 == 0:
                pct = int(i * 100 / total)
                progress_dialog.update(pct, f"[COLOR yellow]{title}[/COLOR]\n{extracted}/{total} קבצים")
        
        zin.close()
        
        log(f"Extraction complete. Extracted: {extracted}, Errors: {errors}")
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
            return True
            
        except Exception as e:
            log(f"Error setting default skin: {e}", xbmc.LOGERROR)
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
        'arctic': {'id': 'skin.arctic.fuse.3', 'name': 'Arctic Fuse',
                   'url_key': 'skin_url', 'zip': 'arctic_fuse.zip'},
        'nimbus': {'id': 'skin.nimbus', 'name': 'Nimbus',
                   'url_key': 'nimbus_skin_url', 'zip': 'nimbus.zip'},
    }

    def install_build(self, build_info, skin_choice='estuary', with_arctic_fuse=None):
        """Full build installation. skin_choice: 'estuary' | 'arctic' | 'nimbus'."""
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
            
            # Prepare destination
            if not os.path.exists(TEMP_FOLDER):
                os.makedirs(TEMP_FOLDER)
            
            filename = build_info['url'].split('/')[-1]
            if not filename.endswith('.zip'):
                filename = 'build.zip'
            zip_path = os.path.join(TEMP_FOLDER, filename)
            
            try:
                os.remove(zip_path)
            except:
                pass
            
            # Step 1: Download base build
            progress.update(0, f"[COLOR yellow]מוריד בילד {build_name}...[/COLOR]")
            success = self.download_file(build_info['url'], zip_path, progress, f"[COLOR yellow]מוריד בילד {build_name}...[/COLOR]")
            
            if not success or not os.path.exists(zip_path) or os.path.getsize(zip_path) == 0:
                progress.close()
                try:
                    os.remove(zip_path)
                except:
                    pass
                self.dialog.ok(ADDON_NAME, f"[COLOR {COLOR_ERROR}]ההורדה נכשלה![/COLOR]")
                return False
            
            # Step 2: Get addon list before wipe
            progress.update(0, "[COLOR yellow]סורק אדונים בבילד...[/COLOR]")
            addon_list = self.grab_addons_from_zip(zip_path)
            
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
            except:
                pass
            
            # Step 4.5: Ask about OLED and apply settings
            self._ask_and_apply_oled(progress)
            progress.create(ADDON_NAME, "[COLOR cyan]ממשיך בהתקנה...[/COLOR]")
            
            # Step 5: Install the chosen optional skin (Arctic Fuse / Nimbus)
            skin_zip_url = build_info.get(skin['url_key']) if skin else None
            if skin and skin_zip_url:
                # Small delay between downloads to avoid GitHub rate limiting
                xbmc.sleep(2000)

                dl_msg = f"[COLOR yellow]מוריד סקין {skin['name']}...[/COLOR]"
                progress.update(0, dl_msg)

                skin_zip = os.path.join(TEMP_FOLDER, skin['zip'])
                try:
                    os.remove(skin_zip)
                except:
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

                    try:
                        os.remove(skin_zip)
                    except:
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
            
            # Save build info
            ADDON.setSetting('buildname', build_name)
            ADDON.setSetting('buildversion', build_info.get('version', '1.0'))
            
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

    def install_skin_only(self, skin_url):
        """Install Arctic Fuse skin on existing build (no wipe)"""
        progress = xbmcgui.DialogProgress()
        progress.create(ADDON_NAME, "[COLOR cyan]מתקין סקין Arctic Fuse...[/COLOR]")
        
        try:
            # Set skip flag
            ADDON.setSetting('skip_update_check', 'true')
            
            if not os.path.exists(TEMP_FOLDER):
                os.makedirs(TEMP_FOLDER)
            
            skin_zip = os.path.join(TEMP_FOLDER, 'arctic_fuse.zip')
            try:
                os.remove(skin_zip)
            except:
                pass
            
            # Download skin
            progress.update(0, "[COLOR yellow]מוריד סקין Arctic Fuse...[/COLOR]")
            success = self.download_file(skin_url, skin_zip, progress, "[COLOR yellow]מוריד Arctic Fuse...[/COLOR]")
            
            if not success or not os.path.exists(skin_zip) or os.path.getsize(skin_zip) == 0:
                progress.close()
                try:
                    os.remove(skin_zip)
                except:
                    pass
                self.dialog.ok(ADDON_NAME, f"[COLOR {COLOR_ERROR}]ההורדה נכשלה![/COLOR]")
                return False
            
            # Get skin addons
            progress.update(50, "[COLOR yellow]סורק אדונים...[/COLOR]")
            skin_addons = self.grab_addons_from_zip(skin_zip)
            
            # Extract with database merge (no wipe!)
            progress.update(60, "[COLOR yellow]מתקין Arctic Fuse...[/COLOR]")
            success, errors = self.extract_and_merge_skin(skin_zip, progress, "מתקין Arctic Fuse...")
            
            if not success:
                progress.close()
                self.dialog.ok(ADDON_NAME, f"[COLOR {COLOR_ERROR}]ההתקנה נכשלה![/COLOR]")
                return False
            
            # Enable addons
            progress.update(85, "[COLOR yellow]מפעיל אדונים...[/COLOR]")
            self.enable_addons_in_db(skin_addons)
            self.setup_wizard_repo_in_db()
            
            # Set as default skin
            progress.update(90, "[COLOR yellow]מגדיר סקין ברירת מחדל...[/COLOR]")
            self.set_default_skin('skin.arctic.fuse.3')
            
            # Update
            progress.update(95, "[COLOR yellow]מעדכן...[/COLOR]")
            xbmc.executebuiltin('UpdateAddonRepos()')
            xbmc.executebuiltin('UpdateLocalAddons()')
            
            # Save setting
            ADDON.setSetting('installed_skin', 'Arctic Fuse')
            
            # Cleanup
            try:
                os.remove(skin_zip)
            except:
                pass
            
            progress.update(100, "[COLOR lime]הסקין הותקן![/COLOR]")
            xbmc.sleep(500)
            progress.close()
            
            # Countdown and restart
            build_name = self.get_installed_build_name()
            self._countdown_restart(build_name, "Arctic Fuse")
            
            return True
            
        except Exception as e:
            progress.close()
            log(f"Skin install error: {e}", xbmc.LOGERROR)
            self.dialog.ok(ADDON_NAME, f"[COLOR {COLOR_ERROR}]שגיאה:[/COLOR] {str(e)}")
            return False


    def _ask_and_apply_oled(self, progress):
        """Ask about OLED and modify guisettings.xml if needed"""
        progress.close()  # Close progress to show dialog
        
        result = self.dialog.yesno(
            '[COLOR FF00BFFF]הגדרות OLED[/COLOR]',
            '[B]יש לך מסך OLED?[/B]\n\n'
            'אם כן, נגדיר הגדרות להגנה על המסך:\n'
            '• Screensaver שחור\n'
            '• הפעלה אחרי דקה\n'
            '• עמעום בזמן השהיה',
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
        os._exit(1)


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
    
    # Check if build is installed and get skin URL for "add skin" option
    build_installed = manager.is_build_installed()
    installed_build = manager.get_installed_build_name()
    installed_skin = manager.get_installed_skin()
    
    # Get skin_url from any build (they all share the same skin URL)
    skin_url = None
    for b in builds:
        if b.get('skin_url'):
            skin_url = b['skin_url']
            break
    
    while True:
        # Build menu items
        menu_items = []
        
        # Add builds
        for b in builds:
            name = b.get('name', 'Unknown')
            ver = b.get('version', '?')
            
            # Mark installed build
            if name == installed_build:
                menu_items.append(f"[COLOR lime]✓[/COLOR] [COLOR yellow]{name}[/COLOR] v{ver} [COLOR gray](מותקן - {installed_skin})[/COLOR]")
            else:
                menu_items.append(f"[COLOR yellow]{name}[/COLOR] v{ver}")
        
        # Add "Install Arctic Fuse" option if build is installed and skin is Estuary
        if build_installed and installed_skin == 'Estuary' and skin_url:
            menu_items.append("[COLOR cyan]➕ הוסף סקין Arctic Fuse לבילד הקיים[/COLOR]")
        
        menu_items.append("[COLOR gray]< חזרה[/COLOR]")
        
        # Show menu
        sel = dialog.select("[B]התקנת בילד[/B]", menu_items)
        
        if sel < 0 or sel == len(menu_items) - 1:
            break
        
        selected_text = menu_items[sel]
        
        # Check if "Add Arctic Fuse" was selected
        if "הוסף סקין Arctic Fuse" in selected_text:
            confirm_msg = (
                f"[COLOR cyan]בילד מותקן:[/COLOR] {installed_build}\n"
                f"[COLOR cyan]סקין נוכחי:[/COLOR] Estuary\n"
                f"[COLOR cyan]סקין חדש:[/COLOR] Arctic Fuse\n\n"
                "[COLOR yellow]הסקין יותקן בלי למחוק את הבילד הקיים.[/COLOR]\n\n"
                "להמשיך?"
            )
            
            if dialog.yesno("[B]הוספת סקין Arctic Fuse[/B]", confirm_msg, yeslabel="[B]התקן[/B]", nolabel="ביטול"):
                manager.install_skin_only(skin_url)
            continue
        
        # Find selected build
        selected_build = None
        for b in builds:
            if b.get('name') in selected_text:
                selected_build = b
                break
        
        if not selected_build:
            continue
        
        build_name = selected_build.get('name', 'Unknown')
        build_ver = selected_build.get('version', '?')
        has_skin = 'skin_url' in selected_build
        
        # Show skin selection. Estuary always; Arctic Fuse / Nimbus only if the
        # build advertises their zip URL.
        if has_skin:
            # (choice, name, one-line desc, preview image under resources/media/skin_previews/)
            skin_options = [('estuary', 'Estuary', 'הרגיל · הכי מהיר · עיצוב פשוט', 'estuary.jpg')]
            if selected_build.get('skin_url'):
                skin_options.append(('arctic', 'Arctic Fuse', 'הכי יפה ומעוצב · הכי כבד · למכשירים חזקים', 'af3.jpg'))
            if selected_build.get('nimbus_skin_url'):
                skin_options.append(('nimbus', 'Nimbus', 'מהיר ויפה יותר מהרגיל · מתאים גם למכשירים חלשים', 'nimbus.jpg'))

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
        
        # Confirm installation
        confirm_msg = (
            f"[COLOR cyan]בילד:[/COLOR] {build_name} v{build_ver}\n"
            f"[COLOR cyan]סקין:[/COLOR] {skin_name}\n\n"
            f"[COLOR {COLOR_WARNING}]אזהרה: כל ההגדרות הקיימות יימחקו![/COLOR]\n\n"
            "להתחיל בהתקנה?"
        )
        
        if dialog.yesno("[B]אישור התקנה[/B]", confirm_msg, yeslabel="[B]התקן[/B]", nolabel="ביטול"):
            manager.install_build(selected_build, skin_choice=skin_choice)
            break
