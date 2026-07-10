# -*- coding: utf-8 -*-
"""
FirstRun Service v2.1.2
- Fix: set repo origin to self-reference for Kodi auto-updates
"""
import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs
import os
import re
import sqlite3
import ssl
import urllib.request

WIZARD_ID = 'plugin.program.masterkodi.il.wizard'
REPO_ID = 'repository.masterkodi.il'
MARKER_FILE = '.masterkodi_il_done'
REPO_ADDONS_XML = 'https://asaf27064.github.io/addons.xml'


def log(msg, level=xbmc.LOGINFO):
    xbmc.log(f'[KodiIL-FirstRun] {msg}', level)


def marker_exists():
    home = xbmcvfs.translatePath('special://home/')
    return os.path.exists(os.path.join(home, MARKER_FILE))


def wizard_exists():
    try:
        xbmcaddon.Addon(WIZARD_ID)
        return True
    except:
        return False


def wait_for_wizard(max_ms=3000, interval_ms=100):
    """Poll until wizard available - exits immediately when found"""
    attempts = max_ms // interval_ms
    for i in range(attempts):
        if wizard_exists():
            log(f'Wizard found after {i * interval_ms}ms')
            return True
        xbmc.sleep(interval_ms)
    return False


def setup_addons_db():
    """Setup Addons33.db with correct entries"""
    try:
        db_path = xbmcvfs.translatePath('special://database/')
        db_file = None
        
        for f in os.listdir(db_path):
            if f.startswith('Addons') and f.endswith('.db'):
                db_file = os.path.join(db_path, f)
                break
        
        if not db_file:
            return False
        
        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()
        
        # Wizard: enabled with repo origin
        cursor.execute('''
            INSERT OR REPLACE INTO installed (addonID, enabled, installDate, origin, disabledReason)
            VALUES (?, 1, datetime('now'), ?, 0)
        ''', (WIZARD_ID, REPO_ID))
        
        # Repo: enabled with self-reference origin (required for Kodi auto-updates)
        cursor.execute('''
            INSERT OR REPLACE INTO installed (addonID, enabled, installDate, origin, disabledReason)
            VALUES (?, 1, datetime('now'), ?, 0)
        ''', (REPO_ID, REPO_ID))
        
        # Repo table
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
        
        # Addons table
        cursor.execute('SELECT id FROM addons WHERE addonID = ?', (WIZARD_ID,))
        row = cursor.fetchone()
        if not row:
            cursor.execute('''
                INSERT INTO addons (addonID, version, name, summary, news, description, metadata)
                VALUES (?, '2.0.1', 'MasterKodi IL Wizard', 'MasterKodi IL Wizard', '', '', '')
            ''', (WIZARD_ID,))
            addon_id = cursor.lastrowid
        else:
            addon_id = row[0]
        
        # Link
        cursor.execute('INSERT OR IGNORE INTO addonlinkrepo (idRepo, idAddon) VALUES (?, ?)', 
                       (repo_id, addon_id))
        
        conn.commit()
        conn.close()
        return True
        
    except Exception as e:
        log(f'DB error: {e}', xbmc.LOGERROR)
        return False


def get_installed_wizard_version():
    try:
        return xbmcaddon.Addon(WIZARD_ID).getAddonInfo('version')
    except:
        return None


def get_wizard_version_from_file():
    try:
        for path in [
            xbmcvfs.translatePath(f'special://home/addons/{WIZARD_ID}/addon.xml'),
            xbmcvfs.translatePath(f'special://xbmc/addons/{WIZARD_ID}/addon.xml'),
        ]:
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    match = re.search(r'<addon[^>]+version="([^"]+)"', f.read())
                    if match:
                        return match.group(1)
    except:
        pass
    return None


def url_get(url, timeout=6):
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(url, headers={'User-Agent': 'Kodi/20.0'})
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as response:
            return response.read().decode('utf-8', errors='replace')
    except:
        return None


def get_repo_wizard_version():
    try:
        content = url_get(REPO_ADDONS_XML)
        if content:
            match = re.search(
                r'<addon[^>]+id="plugin\.program\.masterkodi\.il\.wizard"[^>]+version="([^"]+)"',
                content
            )
            if match:
                return match.group(1)
    except:
        pass
    return None


def version_tuple(v):
    try:
        return tuple(int(''.join(c for c in p if c.isdigit()) or 0) for p in str(v).split('.'))
    except:
        return (0,)


def update_wizard_if_needed():
    """Check and update wizard - returns quickly if no update needed"""
    current = get_installed_wizard_version() or get_wizard_version_from_file()
    latest = get_repo_wizard_version()
    
    log(f'Versions - Current: {current}, Repo: {latest}')
    
    if not current or not latest:
        return False
    
    if version_tuple(latest) <= version_tuple(current):
        log('Wizard up to date')
        return False
    
    log(f'Updating: {current} -> {latest}')
    
    xbmcgui.Dialog().notification('MasterKodi IL', f'מעדכן Wizard ל-{latest}...', 
                                   xbmcgui.NOTIFICATION_INFO, 1500)
    
    xbmc.executebuiltin('UpdateAddonRepos')
    xbmc.sleep(3000)  # repos need time
    xbmc.executebuiltin('UpdateLocalAddons')
    
    # Poll for update completion
    for i in range(24):
        xbmc.sleep(500)
        kodi_ver = get_installed_wizard_version()
        file_ver = get_wizard_version_from_file()
        
        if kodi_ver and version_tuple(kodi_ver) >= version_tuple(latest):
            log(f'Updated to {kodi_ver}')
            return True
        if file_ver and version_tuple(file_ver) >= version_tuple(latest):
            log(f'Files updated to {file_ver}')
            return True
    
    log('Update timeout', xbmc.LOGWARNING)
    return False


def launch_wizard():
    log('Launching wizard')
    xbmc.executebuiltin(f'RunPlugin(plugin://{WIZARD_ID}/?mode=builds)')


if __name__ == '__main__':
    log('Service started')
    
    if marker_exists():
        log('Marker exists, skipping')
    else:
        # Quick scan
        xbmc.executebuiltin('UpdateLocalAddons')
        
        # Poll for wizard (max 3 sec, check every 100ms)
        if not wait_for_wizard(max_ms=3000, interval_ms=100):
            # Not found - setup DB and try again
            log('Wizard not found, setting up DB...')
            setup_addons_db()
            xbmc.executebuiltin('UpdateLocalAddons')
            wait_for_wizard(max_ms=2000, interval_ms=100)
        
        if wizard_exists():
            setup_addons_db()  # Ensure correct origin
            update_wizard_if_needed()
            launch_wizard()
        else:
            log('Wizard not available', xbmc.LOGERROR)
    
    log('Service finished')
