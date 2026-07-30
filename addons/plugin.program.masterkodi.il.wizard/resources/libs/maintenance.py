# -*- coding: utf-8 -*-
"""
MasterKodi IL Wizard - Maintenance Tools
"""
import os
import shutil
import xbmc

from resources.libs.config import (
    ADDON_ID, HOME, ADDONS, USERDATA, DATABASE, TEMP_FOLDER
)


def log(msg, level=xbmc.LOGINFO):
    xbmc.log(f'[{ADDON_ID}] Maintenance: {msg}', level)


def get_size(path):
    total = 0
    try:
        for root, _, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except Exception:
                    pass
    except Exception:
        pass
    return total


def format_size(b):
    for u in ['B', 'KB', 'MB', 'GB']:
        if b < 1024:
            return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} TB"


def summarize(count, freed):
    """Human summary for a purge result, used verbatim in the Hebrew dialogs."""
    # phrased to read correctly after the dialog's "נמחקו: " prefix
    if not count:
        return '0 פריטים (כבר היה נקי)'
    return f'{count} פריטים ({format_size(freed)})'


def purge_dir(path, keep_suffixes=()):
    """Delete the CONTENTS of `path` (not the folder itself).

    `keep_suffixes` protects files in the ROOT of `path` -- used to keep
    kodi.log alive: it lives in special://temp, and unlinking it while Kodi
    holds the handle open silently kills all further logging (the log is then
    unrecoverable until Kodi restarts), which would blind every support session.

    Returns (items_removed, bytes_freed).
    """
    items = 0
    freed = 0
    if not os.path.exists(path):
        return (0, 0)
    try:
        entries = os.listdir(path)
    except Exception:
        return (0, 0)
    for name in entries:
        p = os.path.join(path, name)
        try:
            is_file = os.path.isfile(p)
            if is_file and keep_suffixes and name.lower().endswith(tuple(keep_suffixes)):
                continue
            size = os.path.getsize(p) if is_file else get_size(p)
            if is_file:
                os.remove(p)
            else:
                shutil.rmtree(p)
            items += 1
            freed += size
        except Exception:
            pass
    return (items, freed)


def clear_cache():
    """Silent worker -- the caller (default.py) owns confirmation + reporting."""
    total_items = 0
    total_freed = 0
    for loc, keep in ((os.path.join(HOME, 'cache'), ()),
                      (os.path.join(HOME, 'temp'), ('.log',)),
                      (TEMP_FOLDER, ())):
        items, freed = purge_dir(loc, keep_suffixes=keep)
        total_items += items
        total_freed += freed
    log(f'cache cleared: {total_items} items, {format_size(total_freed)} (logs kept)')
    return summarize(total_items, total_freed)


def clear_packages():
    """Silent worker -- the caller owns confirmation + reporting."""
    items, freed = purge_dir(os.path.join(ADDONS, 'packages'))
    log(f'packages cleared: {items} items, {format_size(freed)}')
    return summarize(items, freed)


def clear_thumbnails(drop_texture_db=False):
    """Silent worker -- the caller owns confirmation + reporting.

    `drop_texture_db` deletes Textures*.db, which Kodi holds OPEN. On Android
    that invalidates the handle (SQLITE_READONLY_DBMOVED) and the texture cache
    stays broken until a restart, so the caller may only pass True when it is
    actually going to restart Kodi right after.
    """
    thumb = os.path.join(USERDATA, 'Thumbnails')
    items, freed = purge_dir(thumb)
    # Kodi expects the 0-f/ shard folders to exist
    for shard in '0123456789abcdef':
        try:
            os.makedirs(os.path.join(thumb, shard), exist_ok=True)
        except Exception:
            pass

    if drop_texture_db:
        try:
            for f in os.listdir(DATABASE):
                if f.startswith('Textures') and f.endswith('.db'):
                    p = os.path.join(DATABASE, f)
                    try:
                        freed += os.path.getsize(p)
                        os.remove(p)
                        items += 1
                    except Exception:
                        pass
        except Exception:
            pass

    log(f'thumbnails cleared: {items} items, {format_size(freed)} '
        f'(texture db {"dropped" if drop_texture_db else "kept"})')
    return summarize(items, freed)


def current_sizes():
    """{'cache': '3.4 MB', 'packages': ..., 'thumbnails': ..., 'total': ...}
    for the menu rows, so the user sees what a clear would actually free."""
    cache = sum(get_size(l) for l in (os.path.join(HOME, 'cache'),
                                      os.path.join(HOME, 'temp'),
                                      TEMP_FOLDER) if os.path.exists(l))
    pkg = get_size(os.path.join(ADDONS, 'packages'))
    thumb = get_size(os.path.join(USERDATA, 'Thumbnails'))
    return {
        'cache': format_size(cache),
        'packages': format_size(pkg),
        'thumbnails': format_size(thumb),
        'total': format_size(cache + pkg + thumb),
    }
