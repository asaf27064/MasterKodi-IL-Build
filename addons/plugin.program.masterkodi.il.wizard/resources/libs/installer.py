# -*- coding: utf-8 -*-
"""
Hebrew Files Installer for POV, FenLight, and Arctic Fuse Skin
With version detection from installed files
"""
import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs
import os
import json
import shutil
import time
import re

try:
    from urllib.request import urlopen, Request
except ImportError:
    from urllib2 import urlopen, Request

import zipfile
import io

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo('id')
ADDON_DATA = xbmcvfs.translatePath(f'special://userdata/addon_data/{ADDON_ID}')
ADDONS_PATH = xbmcvfs.translatePath('special://home/addons/')

# GitHub URLs
GITHUB_BASE_URL = "https://raw.githubusercontent.com/asaf27064/pov-modified-heb/main"
POV_VERSION_URL = f"{GITHUB_BASE_URL}/version.json"
POV_ZIP_URL = f"{GITHUB_BASE_URL}/pov_hebrew_subtitles.zip"
FENLIGHT_VERSION_URL = f"{GITHUB_BASE_URL}/fenlight_version.json"
FENLIGHT_ZIP_URL = f"{GITHUB_BASE_URL}/fenlight_hebrew_subtitles.zip"
SKIN_VERSION_URL = f"{GITHUB_BASE_URL}/skin_version.json"
SKIN_ZIP_URL = f"{GITHUB_BASE_URL}/skin_hebrew_files.zip"

# Backup folders
POV_BACKUP_FOLDER = os.path.join(ADDON_DATA, 'pov_backup')
FENLIGHT_BACKUP_FOLDER = os.path.join(ADDON_DATA, 'fenlight_backup')
SKIN_BACKUP_FOLDER = os.path.join(ADDON_DATA, 'skin_backup')


def log(msg, level=xbmc.LOGINFO):
    xbmc.log(f'[{ADDON_ID}] {msg}', level)


def download_bytes(url, timeout=120, attempts=4, progress_callback=None, label=None):
    """Download a URL to bytes with retries + exponential-ish backoff.

    Android networks (mobile data / flaky WiFi) and the GitHub CDN drop the odd
    connection. A single urlopen used to fail the whole Hebrew install on one
    transient hiccup -- which is why it took 2-3 manual retries to succeed.
    Retrying in-process makes it succeed on the first user attempt."""
    last_err = None
    for attempt in range(1, attempts + 1):
        try:
            req = Request(url, headers={'User-Agent': 'Kodi'})
            data = urlopen(req, timeout=timeout).read()
            if data:
                return data
            last_err = 'empty response'
        except Exception as e:
            last_err = e
            log(f"Download attempt {attempt}/{attempts} failed for {url}: {e}", xbmc.LOGWARNING)
        if attempt < attempts:
            if progress_callback and label:
                progress_callback(f"{label} (ניסיון {attempt + 1})...", 15)
            xbmc.sleep(1500 * attempt)  # 1.5s, 3s, 4.5s backoff
    raise Exception(f"download failed after {attempts} attempts: {last_err}")


def get_version_from_file(filepath):
    """Read version from a version.txt file"""
    try:
        if os.path.exists(filepath):
            with open(filepath, 'r', encoding='utf-8') as f:
                return f.read().strip()
    except Exception as e:
        log(f"Error reading version file {filepath}: {e}")
    return None


def get_version_from_settings_xml(settings_path, setting_id='hebrew_subtitles.installed_version'):
    """Read version from settings.xml default value"""
    try:
        if os.path.exists(settings_path):
            with open(settings_path, 'r', encoding='utf-8') as f:
                content = f.read()
            # Look for the version setting
            match = re.search(rf'id="{setting_id}"[^>]*default="([^"]+)"', content)
            if match:
                return match.group(1)
            # Also check for version in comment
            match = re.search(r'Version:\s*(\d+\.\d+\.\d+)', content)
            if match:
                return match.group(1)
    except Exception as e:
        log(f"Error reading settings.xml {settings_path}: {e}")
    return None


# ============================================
# POV HEBREW INSTALLER
# ============================================
class POVHebrewInstaller:
    def __init__(self):
        self.addon_id = 'plugin.video.pov'
        self.addon_path = None
        self.backup_folder = POV_BACKUP_FOLDER
        self._find_addon_path()
    
    def _find_addon_path(self):
        """Find POV addon path"""
        try:
            addon = xbmcaddon.Addon(self.addon_id)
            self.addon_path = addon.getAddonInfo('path')
            log(f"Found POV at: {self.addon_path}")
        except Exception as e:
            log(f"POV not found: {e}", xbmc.LOGWARNING)
            self.addon_path = None
    
    def is_pov_installed(self):
        """Check if POV is installed"""
        return self.addon_path is not None and os.path.exists(self.addon_path)
    
    def is_installed(self):
        """Check if Hebrew files are installed"""
        if not self.is_pov_installed():
            return False
        kodirdil_path = os.path.join(self.addon_path, 'resources', 'lib', 'kodirdil')
        return os.path.exists(kodirdil_path)
    
    def get_installed_version(self):
        """Get installed Hebrew version from multiple sources"""
        if not self.is_installed():
            return None
        
        # Method 1: Check version.txt in addon root
        version_file = os.path.join(self.addon_path, 'version.txt')
        version = get_version_from_file(version_file)
        if version:
            log(f"POV Hebrew version from file: {version}")
            return version
        
        # Method 2: Check addon's settings for hebrew_subtitles.installed_version
        try:
            addon = xbmcaddon.Addon(self.addon_id)
            version = addon.getSetting('hebrew_subtitles.installed_version')
            if version:
                log(f"POV Hebrew version from settings: {version}")
                return version
        except:
            pass
        
        # Method 3: Check wizard's saved setting
        version = ADDON.getSetting('pov_hebrew_version')
        if version and version != '0' and version != '':
            log(f"POV Hebrew version from wizard settings: {version}")
            return version
        
        # Installed but version unknown
        log("POV Hebrew installed but version unknown")
        return 'installed'
    
    def has_backup(self):
        """Check if backup exists"""
        if not os.path.exists(self.backup_folder):
            return False
        return os.path.exists(os.path.join(self.backup_folder, 'backup_info.json'))
    
    def backup_files(self, progress_callback=None):
        """Backup original POV files"""
        if not self.is_pov_installed():
            return False
        
        try:
            os.makedirs(self.backup_folder, exist_ok=True)
            
            if progress_callback:
                progress_callback("Backing up original files...", 5)
            
            files_to_backup = [
                ('resources/lib/windows/sources.py', 'windows_sources.py'),
                ('resources/lib/modules/sources.py', 'modules_sources.py'),
            ]
            
            backed_up = []
            for src_rel, backup_name in files_to_backup:
                src = os.path.join(self.addon_path, src_rel)
                dst = os.path.join(self.backup_folder, backup_name)
                if os.path.exists(src):
                    shutil.copy2(src, dst)
                    backed_up.append({'original': src_rel, 'backup': backup_name})
            
            # Save backup info
            backup_info = {
                'backup_date': time.strftime('%Y-%m-%d %H:%M:%S'),
                'files': backed_up
            }
            with open(os.path.join(self.backup_folder, 'backup_info.json'), 'w') as f:
                json.dump(backup_info, f, indent=2)
            
            return True
        except Exception as e:
            log(f"POV backup error: {e}", xbmc.LOGERROR)
            return False
    
    def install(self, progress_callback=None):
        """Download and install Hebrew files"""
        if not self.is_pov_installed():
            log("POV not installed!")
            return False
        
        try:
            # Backup first
            if not self.has_backup():
                self.backup_files(progress_callback)
            
            if progress_callback:
                progress_callback("Downloading Hebrew files...", 15)
            
            # Download ZIP
            log(f"Downloading POV Hebrew from {POV_ZIP_URL}")
            zip_data = download_bytes(POV_ZIP_URL, timeout=60, progress_callback=progress_callback,
                                      label="Downloading Hebrew files...")
            
            if progress_callback:
                progress_callback("Extracting files...", 40)
            
            # Extract files
            zip_buffer = io.BytesIO(zip_data)
            with zipfile.ZipFile(zip_buffer, 'r') as zf:
                for member in zf.namelist():
                    if member.endswith('/'):
                        continue
                    if member == 'settings.xml':
                        # Merge settings - handled separately
                        settings_content = zf.read(member)
                        self._merge_settings(settings_content)
                        continue
                    dest = os.path.join(self.addon_path, member)
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with zf.open(member) as src, open(dest, 'wb') as dst:
                        dst.write(src.read())
                    log(f"Installed: {dest}")
            
            if progress_callback:
                progress_callback("Saving version...", 90)
            
            # Get and save version
            try:
                req = Request(POV_VERSION_URL, headers={'User-Agent': 'Kodi'})
                response = urlopen(req, timeout=10)
                version_data = json.loads(response.read().decode('utf-8'))
                version = version_data.get('version', '1.0.0')
            except:
                version = '1.0.0'
            
            ADDON.setSetting('pov_hebrew_version', version)
            
            if progress_callback:
                progress_callback("Installation complete!", 100)
            
            log(f"POV Hebrew installation complete! Version: {version}")
            return True
            
        except Exception as e:
            log(f"POV installation error: {e}", xbmc.LOGERROR)
            return False
    
    def _merge_settings(self, new_settings_content):
        """Merge Hebrew settings into POV's settings.xml"""
        try:
            pov_settings_path = os.path.join(self.addon_path, 'resources', 'settings.xml')
            
            if not os.path.exists(pov_settings_path):
                log("POV settings.xml not found!")
                return
            
            with open(pov_settings_path, 'r', encoding='utf-8') as f:
                original = f.read()
            
            # Check if Hebrew settings already exist
            if 'Hebrew Subtitles' in original:
                log("Hebrew settings already in POV settings.xml")
                return
            
            # Parse the new settings to get just the category content
            new_content = new_settings_content.decode('utf-8')
            match = re.search(r'(<category label="Hebrew Subtitles">.*?</category>)', new_content, re.DOTALL)
            if not match:
                log("Could not find Hebrew category in new settings")
                return
            
            hebrew_category = match.group(1)
            
            # Insert before </settings>
            merged = original.replace('</settings>', f'\n\t{hebrew_category}\n</settings>')
            
            with open(pov_settings_path, 'w', encoding='utf-8') as f:
                f.write(merged)
            
            log("Merged Hebrew settings into POV settings.xml")
            
        except Exception as e:
            log(f"Error merging settings: {e}", xbmc.LOGERROR)
    
    def uninstall(self, progress_callback=None):
        """Remove Hebrew files"""
        if not self.is_pov_installed():
            return False
        
        try:
            lib_path = os.path.join(self.addon_path, 'resources', 'lib')
            
            # Remove kodirdil folder
            kodirdil_path = os.path.join(lib_path, 'kodirdil')
            if os.path.exists(kodirdil_path):
                shutil.rmtree(kodirdil_path)
                log("Removed kodirdil folder")
            
            # Restore backed up files if available
            if self.has_backup():
                with open(os.path.join(self.backup_folder, 'backup_info.json'), 'r') as f:
                    backup_info = json.load(f)
                
                for file_info in backup_info.get('files', []):
                    src = os.path.join(self.backup_folder, file_info['backup'])
                    dst = os.path.join(self.addon_path, file_info['original'])
                    if os.path.exists(src):
                        shutil.copy2(src, dst)
                        log(f"Restored: {dst}")
            
            ADDON.setSetting('pov_hebrew_version', '')
            log("POV Hebrew uninstalled")
            return True
            
        except Exception as e:
            log(f"POV uninstall error: {e}", xbmc.LOGERROR)
            return False


# ============================================
# FENLIGHT HEBREW INSTALLER
# ============================================
class FenLightHebrewInstaller:
    def __init__(self):
        self.addon_id = 'plugin.video.fenlight'
        self.addon_path = None
        self.backup_folder = FENLIGHT_BACKUP_FOLDER
        self._find_addon_path()
    
    def _find_addon_path(self):
        """Find FenLight addon path"""
        try:
            addon = xbmcaddon.Addon(self.addon_id)
            self.addon_path = addon.getAddonInfo('path')
            log(f"Found FenLight at: {self.addon_path}")
        except Exception as e:
            log(f"FenLight not found: {e}", xbmc.LOGWARNING)
            self.addon_path = None
    
    def is_addon_installed(self):
        """Check if FenLight is installed"""
        return self.addon_path is not None and os.path.exists(self.addon_path)
    
    def is_hebrew_installed(self):
        """Check if Hebrew files are installed"""
        if not self.is_addon_installed():
            return False
        kodirdil_path = os.path.join(self.addon_path, 'resources', 'lib', 'kodirdil')
        return os.path.exists(kodirdil_path)
    
    def is_installed(self):
        """Alias for is_hebrew_installed"""
        return self.is_hebrew_installed()
    
    def get_installed_version(self):
        """Get installed Hebrew version"""
        if not self.is_hebrew_installed():
            return None
        
        # Method 1: Check version.txt in addon root
        version_file = os.path.join(self.addon_path, 'version.txt')
        version = get_version_from_file(version_file)
        if version:
            log(f"FenLight Hebrew version from file: {version}")
            return version
        
        # Method 2: Check wizard's saved setting
        version = ADDON.getSetting('fenlight_hebrew_version')
        if version and version != '0' and version != '':
            log(f"FenLight Hebrew version from wizard settings: {version}")
            return version
        
        log("FenLight Hebrew installed but version unknown")
        return 'installed'
    
    def has_backup(self):
        """Check if backup exists"""
        if not os.path.exists(self.backup_folder):
            return False
        return os.path.exists(os.path.join(self.backup_folder, 'backup_info.json'))
    
    def backup_files(self, progress_callback=None):
        """Backup original files"""
        if not self.is_addon_installed():
            return False
        
        try:
            os.makedirs(self.backup_folder, exist_ok=True)
            
            files_to_backup = [
                ('resources/lib/modules/sources.py', 'modules_sources.py'),
                ('resources/lib/windows/sources.py', 'windows_sources.py'),
                ('resources/lib/caches/settings_cache.py', 'settings_cache.py'),
                ('resources/lib/apis/tmdb_api.py', 'tmdb_api.py'),
                ('resources/skins/Default/1080i/settings_manager.xml', 'settings_manager.xml'),
            ]
            
            backed_up = []
            for src_rel, backup_name in files_to_backup:
                src = os.path.join(self.addon_path, src_rel)
                dst = os.path.join(self.backup_folder, backup_name)
                if os.path.exists(src):
                    shutil.copy2(src, dst)
                    backed_up.append({'original': src_rel, 'backup': backup_name})
            
            backup_info = {
                'backup_date': time.strftime('%Y-%m-%d %H:%M:%S'),
                'files': backed_up
            }
            with open(os.path.join(self.backup_folder, 'backup_info.json'), 'w') as f:
                json.dump(backup_info, f, indent=2)
            
            return True
        except Exception as e:
            log(f"FenLight backup error: {e}", xbmc.LOGERROR)
            return False
    
    def install_hebrew_files(self, progress_callback=None):
        """Download and install Hebrew files"""
        if not self.is_addon_installed():
            log("FenLight not installed!")
            return False
        
        try:
            if not self.has_backup():
                self.backup_files(progress_callback)
            
            if progress_callback:
                progress_callback("Downloading Hebrew files...", 15)
            
            log(f"Downloading FenLight Hebrew from {FENLIGHT_ZIP_URL}")
            zip_data = download_bytes(FENLIGHT_ZIP_URL, timeout=60, progress_callback=progress_callback,
                                      label="Downloading Hebrew files...")
            
            if progress_callback:
                progress_callback("Extracting files...", 40)
            
            zip_buffer = io.BytesIO(zip_data)
            with zipfile.ZipFile(zip_buffer, 'r') as zf:
                for member in zf.namelist():
                    if member.endswith('/'):
                        continue
                    dest = os.path.join(self.addon_path, member)
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with zf.open(member) as src, open(dest, 'wb') as dst:
                        dst.write(src.read())
                    log(f"Installed: {dest}")
            
            if progress_callback:
                progress_callback("Saving version...", 90)
            
            try:
                req = Request(FENLIGHT_VERSION_URL, headers={'User-Agent': 'Kodi'})
                response = urlopen(req, timeout=10)
                version_data = json.loads(response.read().decode('utf-8'))
                version = version_data.get('version', '1.0.0')
            except:
                version = '1.0.0'
            
            ADDON.setSetting('fenlight_hebrew_version', version)
            
            if progress_callback:
                progress_callback("Installation complete!", 100)
            
            log(f"FenLight Hebrew installation complete! Version: {version}")
            return True
            
        except Exception as e:
            log(f"FenLight installation error: {e}", xbmc.LOGERROR)
            return False
    
    def install(self, progress_callback=None):
        """Alias for install_hebrew_files"""
        return self.install_hebrew_files(progress_callback)
    
    def uninstall(self, progress_callback=None):
        """Remove Hebrew files"""
        if not self.is_addon_installed():
            return False
        
        try:
            lib_path = os.path.join(self.addon_path, 'resources', 'lib')
            
            kodirdil_path = os.path.join(lib_path, 'kodirdil')
            if os.path.exists(kodirdil_path):
                shutil.rmtree(kodirdil_path)
            
            if self.has_backup():
                with open(os.path.join(self.backup_folder, 'backup_info.json'), 'r') as f:
                    backup_info = json.load(f)
                
                for file_info in backup_info.get('files', []):
                    src = os.path.join(self.backup_folder, file_info['backup'])
                    dst = os.path.join(self.addon_path, file_info['original'])
                    if os.path.exists(src):
                        shutil.copy2(src, dst)
            
            ADDON.setSetting('fenlight_hebrew_version', '')
            log("FenLight Hebrew uninstalled")
            return True
            
        except Exception as e:
            log(f"FenLight uninstall error: {e}", xbmc.LOGERROR)
            return False


# ============================================
# SKIN HEBREW INSTALLER (Arctic Fuse)
# ============================================
class ArcticFuseHebrewInstaller:
    def __init__(self):
        self.skin_id = 'skin.arctic.fuse.3'
        self.skin_path = os.path.join(ADDONS_PATH, self.skin_id)
        self.backup_folder = SKIN_BACKUP_FOLDER
    
    def is_skin_installed(self):
        """Check if Arctic Fuse skin is installed"""
        return os.path.exists(self.skin_path)
    
    def is_installed(self):
        """Check if Hebrew files are installed"""
        if not self.is_skin_installed():
            return False
        # Check for Hebrew font
        font_path = os.path.join(self.skin_path, 'fonts', 'Rubik-VariableFont_wght.ttf')
        return os.path.exists(font_path)
    
    def is_hebrew_installed(self):
        """Alias for is_installed - for compatibility with default.py"""
        return self.is_installed()
    
    def get_installed_version(self):
        """Get installed Hebrew version"""
        if not self.is_installed():
            return None
        
        # Method 1: Check version file
        version_file = os.path.join(self.skin_path, 'hebrew_version.txt')
        version = get_version_from_file(version_file)
        if version:
            log(f"Skin Hebrew version from file: {version}")
            return version
        
        # Method 2: Check wizard setting
        version = ADDON.getSetting('skin_hebrew_version')
        if version and version != '0' and version != '':
            log(f"Skin Hebrew version from wizard settings: {version}")
            return version
        
        log("Skin Hebrew installed but version unknown")
        return 'installed'
    
    def has_backup(self):
        """Check if backup exists"""
        if not os.path.exists(self.backup_folder):
            return False
        return os.path.exists(os.path.join(self.backup_folder, 'backup_info.json'))
    
    def backup_files(self, progress_callback=None):
        """Backup original files"""
        if not self.is_skin_installed():
            return False
        
        try:
            os.makedirs(self.backup_folder, exist_ok=True)
            
            files_to_backup = [
                ('1080i/Font.xml', 'Font.xml'),
                ('1080i/Includes_Font.xml', 'Includes_Font.xml'),
            ]
            
            backed_up = []
            for src_rel, backup_name in files_to_backup:
                src = os.path.join(self.skin_path, src_rel)
                dst = os.path.join(self.backup_folder, backup_name)
                if os.path.exists(src):
                    shutil.copy2(src, dst)
                    backed_up.append({'original': src_rel, 'backup': backup_name})
            
            backup_info = {
                'backup_date': time.strftime('%Y-%m-%d %H:%M:%S'),
                'files': backed_up
            }
            with open(os.path.join(self.backup_folder, 'backup_info.json'), 'w') as f:
                json.dump(backup_info, f, indent=2)
            
            return True
        except Exception as e:
            log(f"Skin backup error: {e}", xbmc.LOGERROR)
            return False
    
    def install(self, progress_callback=None):
        """Download and install Hebrew files"""
        if not self.is_skin_installed():
            log("Arctic Fuse skin not installed!")
            return False
        
        try:
            if not self.has_backup():
                self.backup_files(progress_callback)
            
            if progress_callback:
                progress_callback("Downloading Hebrew files...", 15)
            
            log(f"Downloading Skin Hebrew from {SKIN_ZIP_URL}")
            zip_data = download_bytes(SKIN_ZIP_URL, timeout=60, progress_callback=progress_callback,
                                      label="Downloading Hebrew files...")
            
            if progress_callback:
                progress_callback("Extracting files...", 40)
            
            installed_count = 0
            zip_buffer = io.BytesIO(zip_data)
            with zipfile.ZipFile(zip_buffer, 'r') as zf:
                for member in zf.namelist():
                    if member.endswith('/'):
                        continue
                    
                    # Handle both formats:
                    # 1. skin_hebrew_files/skin.arctic.fuse.3/...
                    # 2. skin.arctic.fuse.3/...
                    rel_path = None
                    if 'skin.arctic.fuse.3/' in member:
                        # Find where skin.arctic.fuse.3/ starts and take everything after it
                        idx = member.find('skin.arctic.fuse.3/')
                        rel_path = member[idx + len('skin.arctic.fuse.3/'):]
                    
                    if rel_path:
                        dest = os.path.join(self.skin_path, rel_path)
                        
                        os.makedirs(os.path.dirname(dest), exist_ok=True)
                        with zf.open(member) as src, open(dest, 'wb') as dst:
                            dst.write(src.read())
                        log(f"Installed: {dest}")
                        installed_count += 1
            
            log(f"Total files installed: {installed_count}")
            
            if progress_callback:
                progress_callback("Saving version...", 90)
            
            try:
                req = Request(SKIN_VERSION_URL, headers={'User-Agent': 'Kodi'})
                response = urlopen(req, timeout=10)
                version_data = json.loads(response.read().decode('utf-8'))
                version = version_data.get('version', '1.0.0')
            except:
                version = '1.0.0'
            
            ADDON.setSetting('skin_hebrew_version', version)
            
            if progress_callback:
                progress_callback("Installation complete!", 100)
            
            log(f"Skin Hebrew installation complete! Version: {version}")
            return True
            
        except Exception as e:
            log(f"Skin installation error: {e}", xbmc.LOGERROR)
            return False
    
    def install_hebrew_files(self, progress_callback=None):
        """Alias for install - for compatibility with default.py"""
        return self.install(progress_callback)
    
    def uninstall(self, progress_callback=None):
        """Remove Hebrew files"""
        if not self.is_skin_installed():
            return False
        
        try:
            # Remove fonts
            fonts_to_remove = [
                'fonts/Rubik-VariableFont_wght.ttf',
                'fonts/Rubik-Italic-VariableFont_wght.ttf',
            ]
            for font in fonts_to_remove:
                path = os.path.join(self.skin_path, font)
                if os.path.exists(path):
                    os.remove(path)
            
            # Remove Hebrew language folder
            he_lang = os.path.join(self.skin_path, 'language', 'resource.language.he_il')
            if os.path.exists(he_lang):
                shutil.rmtree(he_lang)
            
            # Remove version file
            version_file = os.path.join(self.skin_path, 'hebrew_version.txt')
            if os.path.exists(version_file):
                os.remove(version_file)
            
            # Restore backed up files
            if self.has_backup():
                with open(os.path.join(self.backup_folder, 'backup_info.json'), 'r') as f:
                    backup_info = json.load(f)
                
                for file_info in backup_info.get('files', []):
                    src = os.path.join(self.backup_folder, file_info['backup'])
                    dst = os.path.join(self.skin_path, file_info['original'])
                    if os.path.exists(src):
                        shutil.copy2(src, dst)
            
            ADDON.setSetting('skin_hebrew_version', '')
            log("Skin Hebrew uninstalled")
            return True
            
        except Exception as e:
            log(f"Skin uninstall error: {e}", xbmc.LOGERROR)
            return False


# Alias for backwards compatibility
SkinHebrewInstaller = ArcticFuseHebrewInstaller


# ============================================
# HELPER FUNCTIONS
# ============================================
def detect_hebrew_installed_version(addon_type):
    """
    Detect installed Hebrew version for an addon.
    
    Args:
        addon_type: 'pov', 'fenlight', or 'skin'
    
    Returns:
        str: Version string, 'installed' if version unknown, or None if not installed
    """
    if addon_type == 'pov':
        installer = POVHebrewInstaller()
        if installer.is_installed():
            return installer.get_installed_version()
        return None
        
    elif addon_type == 'fenlight':
        installer = FenLightHebrewInstaller()
        if installer.is_hebrew_installed():
            return installer.get_installed_version()
        return None
        
    elif addon_type == 'skin':
        installer = ArcticFuseHebrewInstaller()
        if installer.is_installed():
            return installer.get_installed_version()
        return None
    
    return None



# =====================================================================
# MasterKodi: Gears (overlay onto upstream base) + AI Subs (gearsai)
# FenLight-style: clean base from chainsrepo + our overlay on top.
# =====================================================================
GEARS_VERSION_URL = f"{GITHUB_BASE_URL}/gears_version.json"
GEARS_OVERLAY_ZIP_URL = f"{GITHUB_BASE_URL}/gears_hebrew_subtitles.zip"
GEARSAI_VERSION_URL = f"{GITHUB_BASE_URL}/gearsai_version.json"
GEARSAI_ZIP_URL = f"{GITHUB_BASE_URL}/gearsai_subtitles.zip"
# Upstream clean Gears (chainsrepo) -- prunes old versions, so always the latest.
GEARS_UPSTREAM_ADDONS_XML = "https://raw.githubusercontent.com/unhingedthemes/zips/main/_zips/addons.xml"
GEARS_UPSTREAM_ZIP_FMT = "https://raw.githubusercontent.com/unhingedthemes/zips/main/_zips/plugin.video.gears/plugin.video.gears-{version}.zip"
GEARS_BACKUP_FOLDER = os.path.join(ADDON_DATA, 'gears_backup')


class GearsHebrewInstaller:
    """Applies our gears_hebrew_subtitles.zip overlay ONTO the installed Gears
    base (exactly like FenLightHebrewInstaller). The base itself is updated
    separately by perform_addon_updates (clean upstream zip)."""
    def __init__(self):
        self.addon_id = 'plugin.video.gears'
        self.addon_path = os.path.join(ADDONS_PATH, self.addon_id)

    def is_gears_installed(self):
        return os.path.exists(os.path.join(self.addon_path, 'addon.xml'))

    def is_installed(self):
        return self.is_gears_installed() and os.path.exists(
            os.path.join(self.addon_path, 'resources', 'lib', 'kodirdil'))

    is_hebrew_installed = is_installed

    def get_installed_version(self):
        v = get_version_from_file(os.path.join(self.addon_path, 'version.txt'))
        if v:
            return v
        v = ADDON.getSetting('gears_hebrew_version')
        return v if v and v not in ('0', '') else '0'

    def install_hebrew_files(self, progress_callback=None):
        """Download our overlay zip and extract it on top of the Gears addon
        (overwrites the patched files, adds kodirdil/, icons, version.txt)."""
        if not self.is_gears_installed():
            log("Gears base not present; cannot apply Hebrew overlay")
            return False
        try:
            if progress_callback:
                progress_callback("מוריד קבצי עברית ל-Gears...", 15)
            data = download_bytes(GEARS_OVERLAY_ZIP_URL, timeout=120,
                                  progress_callback=progress_callback,
                                  label="מוריד קבצי עברית ל-Gears")
            if progress_callback:
                progress_callback("מחיל עברית...", 50)
            installed = 0
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                for member in zf.namelist():
                    if member.endswith('/'):
                        continue
                    # overlay zip is rooted at resources/... + version.txt
                    dst = os.path.join(self.addon_path, member.replace('/', os.sep))
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    with open(dst, 'wb') as f:
                        f.write(zf.read(member))
                    installed += 1
            if installed < 10 or not self.is_installed():
                log(f"Gears overlay apply looks wrong ({installed} files)", xbmc.LOGERROR)
                return False
            v = get_version_from_file(os.path.join(self.addon_path, 'version.txt')) or ''
            if v:
                ADDON.setSetting('gears_hebrew_version', v)
            log(f"Gears Hebrew overlay applied ({installed} files, v{v})")
            return True
        except Exception as e:
            log(f"Gears overlay error: {e}", xbmc.LOGERROR)
            return False

    # perform_hebrew_updates / perform_addon_updates call .install()
    install = install_hebrew_files


class GearsaiInstaller:
    """Installs/updates service.subtitles.gearsai (MasterKodi AI Subs). For
    existing users this is a FIRST install -> also register + enable."""
    def __init__(self):
        self.addon_id = 'service.subtitles.gearsai'
        self.addon_path = os.path.join(ADDONS_PATH, self.addon_id)

    def is_installed(self):
        return os.path.exists(os.path.join(self.addon_path, 'addon.xml'))

    is_hebrew_installed = is_installed

    def get_installed_version(self):
        if not self.is_installed():
            return '0'
        try:
            import xml.etree.ElementTree as ET
            return ET.parse(os.path.join(self.addon_path, 'addon.xml')).getroot().get('version', '0')
        except Exception:
            v = ADDON.getSetting('gearsai_version')
            return v if v and v not in ('0', '') else '0'

    def install(self, progress_callback=None):
        try:
            was_new = not self.is_installed()
            if progress_callback:
                progress_callback("מוריד כתוביות AI...", 15)
            data = download_bytes(GEARSAI_ZIP_URL, timeout=120,
                                  progress_callback=progress_callback,
                                  label="מוריד כתוביות AI")
            if progress_callback:
                progress_callback("מתקין כתוביות AI...", 50)
            prefix = 'service.subtitles.gearsai/'
            tmp = self.addon_path + '.upd_tmp'
            if os.path.isdir(tmp):
                shutil.rmtree(tmp, ignore_errors=True)
            os.makedirs(tmp)
            count = 0
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                for m in zf.namelist():
                    if m.endswith('/'):
                        continue
                    idx = m.find(prefix)
                    if idx < 0:
                        continue
                    rel = m[idx + len(prefix):]
                    d = os.path.join(tmp, rel.replace('/', os.sep))
                    os.makedirs(os.path.dirname(d), exist_ok=True)
                    with open(d, 'wb') as f:
                        f.write(zf.read(m))
                    count += 1
            if count < 5 or not os.path.exists(os.path.join(tmp, 'addon.xml')):
                shutil.rmtree(tmp, ignore_errors=True)
                return False
            if os.path.isdir(self.addon_path):
                shutil.rmtree(self.addon_path, ignore_errors=True)
            shutil.move(tmp, self.addon_path)
            if was_new:
                try:
                    xbmc.executebuiltin('UpdateLocalAddons')
                    xbmc.sleep(2000)
                    xbmc.executeJSONRPC(json.dumps({"jsonrpc": "2.0", "id": 1,
                        "method": "Addons.SetAddonEnabled",
                        "params": {"addonid": self.addon_id, "enabled": True}}))
                except Exception as e:
                    log(f"gearsai register: {e}")
            v = self.get_installed_version()
            if v and v != '0':
                ADDON.setSetting('gearsai_version', v)
            log(f"gearsai installed/updated to {v} (new={was_new})")
            return True
        except Exception as e:
            log(f"gearsai install error: {e}", xbmc.LOGERROR)
            return False
