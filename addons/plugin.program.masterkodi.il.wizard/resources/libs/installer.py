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


# ============================================
# FENLIGHT HEBREW INSTALLER
# ============================================


# ============================================
# SKIN HEBREW INSTALLER (Arctic Fuse)
# ============================================


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
