# -*- coding: utf-8 -*-
"""
MasterKodi IL Wizard - Configuration
"""
import os
import xbmc
import xbmcaddon
import xbmcvfs

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo('id')
ADDON_NAME = ADDON.getAddonInfo('name')
ADDON_VERSION = ADDON.getAddonInfo('version')
ADDON_PATH = xbmcvfs.translatePath(ADDON.getAddonInfo('path'))
ADDON_DATA = xbmcvfs.translatePath(ADDON.getAddonInfo('profile'))

# Kodi paths
HOME = xbmcvfs.translatePath('special://home/')
ADDONS = os.path.join(HOME, 'addons')
USERDATA = xbmcvfs.translatePath('special://userdata/')
ADDON_DATA_PATH = os.path.join(USERDATA, 'addon_data')
DATABASE = os.path.join(USERDATA, 'Database')

# XML files
GUISETTINGS = os.path.join(USERDATA, 'guisettings.xml')
SOURCES = os.path.join(USERDATA, 'sources.xml')
FAVOURITES = os.path.join(USERDATA, 'favourites.xml')

# Wizard folders
BACKUPS_FOLDER = os.path.join(ADDON_DATA, 'backups')
TEMP_FOLDER = os.path.join(ADDON_DATA, 'temp')
POV_BACKUP_FOLDER = os.path.join(ADDON_DATA, 'pov_backup')
FENLIGHT_BACKUP_FOLDER = os.path.join(ADDON_DATA, 'fenlight_backup')
SKIN_BACKUP_FOLDER = os.path.join(ADDON_DATA, 'skin_backup')

# GitHub Pages URL (for builds)
GITHUB_PAGES_URL = "https://asaf27064.github.io/"
BUILD_TXT_URL = GITHUB_PAGES_URL + "assets/build.txt"

# GitHub URLs for Hebrew files
GITHUB_BASE_URL = "https://raw.githubusercontent.com/asaf27064/pov-modified-heb/main"
POV_VERSION_URL = f"{GITHUB_BASE_URL}/version.json"
POV_ZIP_URL = f"{GITHUB_BASE_URL}/pov_hebrew_subtitles.zip"

FENLIGHT_GITHUB_URL = "https://raw.githubusercontent.com/asaf27064/FenLight-Hebrew/main"
FENLIGHT_VERSION_URL = f"{FENLIGHT_GITHUB_URL}/version.json"
FENLIGHT_ZIP_URL = f"{FENLIGHT_GITHUB_URL}/fenlight_hebrew.zip"

SKIN_GITHUB_URL = "https://raw.githubusercontent.com/asaf27064/arctic-fuse-hebrew/main"
SKIN_VERSION_URL = f"{SKIN_GITHUB_URL}/version.json"
SKIN_ZIP_URL = f"{SKIN_GITHUB_URL}/skin_hebrew.zip"

# Addon IDs
POV_ADDON_ID = 'plugin.video.pov'
FENLIGHT_ADDON_ID = 'plugin.video.fenlight'
GEARS_ADDON_ID = 'plugin.video.gears'
GEARSAI_ADDON_ID = 'service.subtitles.gearsai'
ARCTIC_FUSE_SKIN_ID = 'skin.arctic.fuse.3'


def backup_location():
    """Where backups are stored. Defaults to the wizard's addon_data, but the
    user can point `backup_location` at external storage so backups survive a
    full build reinstall (which wipes addon_data)."""
    try:
        custom = (ADDON.getSetting('backup_location') or '').strip()
    except Exception:
        custom = ''
    folder = xbmcvfs.translatePath(custom) if custom else BACKUPS_FOLDER
    try:
        if not os.path.exists(folder):
            os.makedirs(folder)
    except Exception:
        folder = BACKUPS_FOLDER
        if not os.path.exists(folder):
            os.makedirs(folder)
    return folder

# Debrid services
DEBRID_SERVICES = {
    'premiumize': {'name': 'Premiumize', 'settings': ['pm.token', 'pm.account_id']},
    'realdebrid': {'name': 'Real-Debrid', 'settings': ['rd.token', 'rd.client_id', 'rd.refresh', 'rd.secret']},
    'easydebrid': {'name': 'EasyDebrid', 'settings': ['ed.token', 'ed.account_id']},
    'torbox': {'name': 'TorBox', 'settings': ['tb.token', 'tb.account_id']},
    'alldebrid': {'name': 'AllDebrid', 'settings': ['ad.token', 'ad.account_id']},
    'offcloud': {'name': 'Offcloud', 'settings': ['oc.token', 'oc.account_id']}
}

# Trakt settings
TRAKT_SETTINGS = ['trakt.token', 'trakt.refresh', 'trakt.expires', 'trakt.user']

# Colors
COLOR_SUCCESS = 'lime'
COLOR_ERROR = 'red'
COLOR_WARNING = 'yellow'

def ensure_folders():
    for folder in [ADDON_DATA, BACKUPS_FOLDER, TEMP_FOLDER, POV_BACKUP_FOLDER, FENLIGHT_BACKUP_FOLDER, SKIN_BACKUP_FOLDER]:
        if not os.path.exists(folder):
            os.makedirs(folder)

ensure_folders()
