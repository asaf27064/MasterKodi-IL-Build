# -*- coding: utf-8 -*-
"""Selective 'keep my data' across a clean build install.

A clean install wipes userdata, so logins and user content would be lost. This
module lets the user pick what to carry over: it snapshots the selected items to
a staging folder (inside the wizard's own addon_data, which survives the wipe)
BEFORE the wipe, then writes them back AFTER the fresh build is installed.

Design (mirrors the classic build-wizard KEEP-* model, adapted to our Gears
stack): each group maps to concrete targets --
  * gears_ids   -> setting ids inside Gears' binary settings.db
  * xml_targets -> (settings.xml path, [setting ids]) for xml-based addons
  * db_files    -> whole sqlite files under Gears' databases/ (user content)
  * files       -> plain files (e.g. favourites.xml)
Every restore step is wrapped so one failure (e.g. a schema change in an old db)
is skipped rather than breaking the install.
"""
import json
import os
import shutil
import sqlite3

import xbmc
import xbmcgui
import xbmcvfs

USERDATA = xbmcvfs.translatePath('special://userdata/')
ADDON_DATA = os.path.join(USERDATA, 'addon_data')
WIZARD_ID = 'plugin.program.masterkodi.il.wizard'
STAGE = os.path.join(ADDON_DATA, WIZARD_ID, '_keep_backup')   # survives the wipe

GEARS_DB_DIR = os.path.join(ADDON_DATA, 'plugin.video.gears', 'databases')
GEARS_SETTINGS_DB = os.path.join(GEARS_DB_DIR, 'settings.db')
GEARSAI_SETTINGS = os.path.join(ADDON_DATA, 'service.subtitles.gearsai', 'settings.xml')
TMDBH_SETTINGS = os.path.join(ADDON_DATA, 'plugin.video.themoviedb.helper', 'settings.xml')
FAVOURITES = os.path.join(USERDATA, 'favourites.xml')


def log(msg, level=xbmc.LOGINFO):
    xbmc.log('[%s.keep] %s' % (WIZARD_ID, msg), level)


# Ordered so the multiselect reads sensibly; all preselected by default.
GROUPS = [
    {'key': 'debrid',
     'label': 'התחברות Debrid (RD / TorBox / Premiumize / AllDebrid)',
     'gears_ids': ['rd.token', 'rd.client_id', 'rd.secret', 'rd.refresh',
                   'torbox.api_key', 'premiumize.token', 'alldebrid.token']},
    {'key': 'trakt',
     'label': 'התחברות Trakt',
     'gears_ids': ['trakt.token', 'trakt.secret', 'trakt.user'],
     'xml_targets': [(TMDBH_SETTINGS, ['trakt.token', 'trakt.refreshtoken', 'trakt.usertoken'])]},
    {'key': 'gemini',
     'label': 'מפתח Gemini אישי (כתוביות AI)',
     'xml_targets': [(GEARSAI_SETTINGS, ['api_key', 'extra_api_keys'])]},
    {'key': 'gears_content',
     'label': 'צפייה, המשך צפייה ורשימות (Gears)',
     'db_files': ['watched.db', 'personal_lists.db', 'lists.db', 'tmdb_lists.db', 'favourites.db']},
    {'key': 'favs',
     'label': 'מועדפים (Kodi)',
     'files': [FAVOURITES]},
]


# --------------------------------------------------------------------------- #
# sqlite settings.db helpers (table: settings(setting_id, setting_value))
# --------------------------------------------------------------------------- #
def _db_read(db, ids):
    out = {}
    if not os.path.isfile(db):
        return out
    try:
        c = sqlite3.connect(db)
        for sid in ids:
            row = c.execute('SELECT setting_value FROM settings WHERE setting_id=?', (sid,)).fetchone()
            if row is not None and row[0] not in (None, ''):
                out[sid] = row[0]
        c.close()
    except Exception as e:
        log('settings.db read failed: %s' % e, xbmc.LOGWARNING)
    return out


def _db_write(db, values):
    if not values or not os.path.isfile(db):
        return
    try:
        c = sqlite3.connect(db)
        for sid, val in values.items():
            cur = c.execute('UPDATE settings SET setting_value=? WHERE setting_id=?', (val, sid))
            if cur.rowcount == 0:
                try:
                    c.execute('INSERT INTO settings (setting_id, setting_value) VALUES (?, ?)', (sid, val))
                except Exception:
                    pass
        c.commit(); c.close()
    except Exception as e:
        log('settings.db write failed: %s' % e, xbmc.LOGWARNING)


# --------------------------------------------------------------------------- #
# settings.xml helpers (<setting id="X">value</setting>)
# --------------------------------------------------------------------------- #
def _xml_read(path, ids):
    out = {}
    if not os.path.isfile(path):
        return out
    try:
        import re
        txt = open(path, encoding='utf-8', errors='replace').read()
        for sid in ids:
            m = re.search(r'<setting id="%s"[^>]*>([^<]*)</setting>' % re.escape(sid), txt)
            if m and m.group(1):
                out[sid] = m.group(1)
    except Exception as e:
        log('xml read failed %s: %s' % (path, e), xbmc.LOGWARNING)
    return out


def _xml_write(path, values):
    if not values:
        return
    try:
        import re
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.isfile(path):
            txt = open(path, encoding='utf-8', errors='replace').read()
        else:
            txt = '<settings version="2">\n</settings>\n'
        for sid, val in values.items():
            if re.search(r'<setting id="%s"[^>]*>' % re.escape(sid), txt):
                txt = re.sub(r'(<setting id="%s"[^>]*>)[^<]*(</setting>)' % re.escape(sid),
                             lambda m: m.group(1) + val + m.group(2), txt, count=1)
            elif '</settings>' in txt:
                txt = txt.replace('</settings>', '    <setting id="%s">%s</setting>\n</settings>' % (sid, val), 1)
        open(path, 'w', encoding='utf-8').write(txt)
    except Exception as e:
        log('xml write failed %s: %s' % (path, e), xbmc.LOGWARNING)


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def prompt(default_all=True):
    """Show the 'what to keep' checklist (all ticked by default). Returns a list
    of selected group keys. Cancel -> keep the defaults (all) so nothing is lost
    by accident."""
    labels = [g['label'] for g in GROUPS]
    preselect = list(range(len(GROUPS))) if default_all else []
    try:
        chosen = xbmcgui.Dialog().multiselect('מה לשמור בהתקנה?', labels, preselect=preselect)
    except Exception:
        chosen = preselect
    if chosen is None:
        chosen = preselect
    return [GROUPS[i]['key'] for i in chosen]


def backup(keys):
    """Snapshot the selected groups to STAGE (call BEFORE the wipe)."""
    try:
        if os.path.isdir(STAGE):
            shutil.rmtree(STAGE, ignore_errors=True)
        os.makedirs(STAGE, exist_ok=True)
    except Exception as e:
        log('cannot create stage: %s' % e, xbmc.LOGERROR)
        return
    saved = {'keys': keys, 'settings': {}, 'xml': {}}
    for g in GROUPS:
        if g['key'] not in keys:
            continue
        if g.get('gears_ids'):
            saved['settings'].setdefault('gears', {}).update(_db_read(GEARS_SETTINGS_DB, g['gears_ids']))
        for path, ids in g.get('xml_targets', []):
            saved['xml'].setdefault(path, {}).update(_xml_read(path, ids))
        for name in g.get('db_files', []):
            src = os.path.join(GEARS_DB_DIR, name)
            if os.path.isfile(src):
                try:
                    shutil.copy2(src, os.path.join(STAGE, 'gearsdb__' + name))
                except Exception as e:
                    log('backup db %s failed: %s' % (name, e), xbmc.LOGWARNING)
        for f in g.get('files', []):
            if os.path.isfile(f):
                try:
                    shutil.copy2(f, os.path.join(STAGE, 'file__' + os.path.basename(f)))
                except Exception as e:
                    log('backup file %s failed: %s' % (f, e), xbmc.LOGWARNING)
    try:
        json.dump(saved, open(os.path.join(STAGE, 'manifest.json'), 'w', encoding='utf-8'))
    except Exception as e:
        log('save manifest failed: %s' % e, xbmc.LOGWARNING)
    log('backed up groups: %s' % ', '.join(keys) if keys else 'none')


def restore():
    """Write back whatever backup() staged (call AFTER install + config)."""
    mf = os.path.join(STAGE, 'manifest.json')
    if not os.path.isfile(mf):
        return
    try:
        saved = json.load(open(mf, encoding='utf-8'))
    except Exception:
        return
    # gears settings.db credentials
    _db_write(GEARS_SETTINGS_DB, saved.get('settings', {}).get('gears', {}))
    # xml settings (gearsai key, tmdb-helper trakt)
    for path, values in saved.get('xml', {}).items():
        _xml_write(path, values)
    # staged db files + plain files
    for name in os.listdir(STAGE):
        try:
            if name.startswith('gearsdb__'):
                os.makedirs(GEARS_DB_DIR, exist_ok=True)
                shutil.copy2(os.path.join(STAGE, name),
                             os.path.join(GEARS_DB_DIR, name[len('gearsdb__'):]))
            elif name.startswith('file__'):
                base = name[len('file__'):]
                if base == 'favourites.xml':
                    shutil.copy2(os.path.join(STAGE, name), FAVOURITES)
        except Exception as e:
            log('restore %s failed: %s' % (name, e), xbmc.LOGWARNING)
    log('restore complete')
    cleanup()


def cleanup():
    try:
        if os.path.isdir(STAGE):
            shutil.rmtree(STAGE, ignore_errors=True)
    except Exception:
        pass
