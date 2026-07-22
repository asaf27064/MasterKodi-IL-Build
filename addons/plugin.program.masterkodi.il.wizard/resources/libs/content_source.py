# -*- coding: utf-8 -*-
"""Content-source switcher: flip a skin's home menu / search / services between
GEARS and POV (the config-variants built in the repo), as an install-time /
post-install OPTION. Gears stays the default; POV is the alternative.

User-initiated + defensive by contract: prompts to close nothing (works with a
ReloadSkin), backs up what it replaces, and any failure leaves the box on the
prior source. It never touches the fragile fresh-install engine.

Variant files are fetched from the repo on demand (small text/JSON) -- no new
build asset. Per skin, the apply mirrors exactly what was validated live
2026-07-21.
"""
import os
import io
import json
import sqlite3
import shutil
import xbmc
import xbmcgui
import xbmcvfs

from resources.libs.config import (
    ADDON_NAME, HOME, ADDONS, USERDATA, ADDON_DATA_PATH, TEMP_FOLDER,
    COLOR_SUCCESS, COLOR_ERROR, COLOR_WARNING,
)

RAW = 'https://raw.githubusercontent.com/asaf27064/MasterKodi-IL-Build/main/'

# skin dir id -> (k21 variant dir, piers variant dir). 'pov' source only; the
# gears baseline is restored from the shipped config, see revert_to_gears.
SKIN_VARIANTS = {
    'skin.estuary':                          ('estuary-pov', 'estuary-piers-pov'),
    'skin.nimbus':                           ('nimbus-pov', None),
    'skin.arctic.fuse.3':                    ('af3-pov', None),
    'skin.arctic.zephyr.2.resurrection.mod': ('zephyr-pov-tmdb', 'zephyr-piers-pov'),
}


def _log(msg, level=xbmc.LOGINFO):
    xbmc.log('[content_source] %s' % msg, level)


def _kodi_major():
    try:
        return int(xbmc.getInfoLabel('System.BuildVersion').split('.')[0])
    except Exception:
        return 21


def _fetch(rel_path):
    """Fetch a repo file (text) -> bytes, or None. rel_path is repo-relative."""
    import urllib.request
    url = RAW + rel_path.replace('\\', '/').replace(' ', '%20')
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'MasterKodi-Wizard'})
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.read()
    except Exception as e:
        _log('fetch failed %s: %s' % (rel_path, e), xbmc.LOGWARNING)
        return None


def _write(dest, data):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, 'wb') as f:
        f.write(data)


def _backup_once(path, tag):
    """Back up a file/dir once per switch (idempotent)."""
    if not os.path.exists(path):
        return
    bk = path + '.pre_' + tag
    if not os.path.exists(bk):
        try:
            if os.path.isdir(path):
                shutil.copytree(path, bk)
            else:
                shutil.copy2(path, bk)
        except Exception:
            pass


def _variant_dir(skin_id):
    k21, piers = SKIN_VARIANTS.get(skin_id, (None, None))
    if _kodi_major() >= 22:
        return piers or k21   # piers-specific if it exists, else the shared one
    return k21


def _variant_roots(skin_id):
    """Repo-relative variant roots, MOST SPECIFIC FIRST.

    The piers variants are OVERLAYS: they carry only what actually differs on
    Kodi 22 (the skin's own XML/menus, because the Piers skins differ). Every
    fleet-NEUTRAL piece -- POV addon data (navigator shortcut folders, views,
    settings) and favourites -- lives once in the shared base variant. Resolving
    per file, piers-then-base, means a missing piers file falls back instead of
    being silently skipped (which used to leave a Kodi 22 POV install with no
    favourites and an unseeded POV db)."""
    k21, piers = SKIN_VARIANTS.get(skin_id, (None, None))
    roots = []
    if _kodi_major() >= 22 and piers:
        roots.append('config-variants-piers/' + piers)
    if k21:
        roots.append('config-variants/' + k21)
    return roots


def _fetchv(roots, rel):
    """Fetch a variant-relative file from the first root that has it."""
    for r in roots:
        data = _fetch(r + '/' + rel)
        if data:
            return data
    return None


# ------------------------------------------------------------------ POV seeds
def _seed_pov_db(roots):
    """Seed POV navigator.db (shortcut folders) + views.db from pov/*.json."""
    pov_data = os.path.join(ADDON_DATA_PATH, 'plugin.video.pov')
    # shortcut folders -> navigator.db
    sf = _fetchv(roots, 'pov/shortcut_folders.json')
    ndb = os.path.join(pov_data, 'navigator.db')
    if sf and os.path.exists(ndb):
        try:
            folders = json.loads(sf.decode('utf-8'))
            con = sqlite3.connect(ndb)
            con.execute("CREATE TABLE IF NOT EXISTS navigator (list_name TEXT, list_type TEXT, list_contents TEXT, UNIQUE(list_name,list_type))")
            for name, items in folders.items():
                con.execute("INSERT OR REPLACE INTO navigator VALUES (?,?,?)",
                            (name, 'shortcut_folder', repr(items)))
            con.commit(); con.close()
            _log('seeded %d POV shortcut folder(s)' % len(folders))
        except Exception as e:
            _log('pov folder seed failed: %s' % e, xbmc.LOGWARNING)
    # views -> views.db
    vj = _fetchv(roots, 'pov/views.json')
    vdb = os.path.join(pov_data, 'views.db')
    if vj and os.path.exists(vdb):
        try:
            views = json.loads(vj.decode('utf-8'))
            con = sqlite3.connect(vdb)
            con.execute("CREATE TABLE IF NOT EXISTS views (view_type TEXT, view_id TEXT, UNIQUE(view_type))")
            for vt, vid in views.items():
                con.execute("INSERT OR REPLACE INTO views VALUES (?,?)", (vt, vid))
            con.commit(); con.close()
            _log('seeded %d POV view(s)' % len(views))
        except Exception as e:
            _log('pov views seed failed: %s' % e, xbmc.LOGWARNING)
    # POV addon settings
    ps = _fetchv(roots, 'pov/settings.xml')
    if ps:
        _backup_once(os.path.join(pov_data, 'settings.xml'), 'gears')
        _write(os.path.join(pov_data, 'settings.xml'), ps)


# ------------------------------------------------------------------ per skin
def _apply_estuary(roots, skin_id):
    # favourites (userdata, shared) + skin xml overrides
    fav = _fetchv(roots, 'favourites.xml')
    if fav:
        _backup_once(os.path.join(USERDATA, 'favourites.xml'), 'gears')
        _write(os.path.join(USERDATA, 'favourites.xml'), fav)
    for name in ('Home.xml', 'Includes.xml', 'Custom_1107_SearchDialog.xml',
                 'DialogButtonMenu.xml'):
        data = _fetchv(roots, 'skin-overrides/' + name)
        if data:
            dest = os.path.join(ADDONS, skin_id, 'xml', name)
            _backup_once(dest, 'gears'); _write(dest, data)
    _seed_pov_db(roots)


def _apply_nimbus(roots, skin_id):
    for name in ('Custom_1107_SearchDialog.xml', 'DialogButtonMenu.xml',
                 'Variables_Search.xml', 'script-nimbus-main_menu_custom1.xml',
                 'script-nimbus-main_menu_movies.xml', 'script-nimbus-main_menu_tvshows.xml',
                 'script-nimbus-widget_custom1.xml', 'script-nimbus-widget_movies.xml',
                 'script-nimbus-widget_tvshows.xml'):
        data = _fetchv(roots, 'skin-overrides/' + name)
        if data:
            dest = os.path.join(ADDONS, skin_id, 'xml', name)
            _backup_once(dest, 'gears'); _write(dest, data)
    # cpath compiled menu config
    cp = _fetchv(roots, 'nimbus/cpath_seed.json')
    db = os.path.join(ADDON_DATA_PATH, 'script.nimbus.helper', 'cpath_cache.db')
    if cp and os.path.exists(db):
        try:
            rows = json.loads(cp.decode('utf-8'))
            _backup_once(db, 'gears')
            con = sqlite3.connect(db)
            con.execute("CREATE TABLE IF NOT EXISTS custom_paths (cpath_setting text unique, cpath_path text, cpath_header text, cpath_type text, cpath_label text)")
            for r in rows:
                con.execute("INSERT OR REPLACE INTO custom_paths VALUES (?,?,?,?,?)", r[:5])
            con.commit(); con.close()
            _log('seeded %d nimbus cpath rows' % len(rows))
        except Exception as e:
            _log('nimbus cpath seed failed: %s' % e, xbmc.LOGWARNING)
    _seed_pov_db(roots)


def _apply_af3(roots, skin_id):
    nodes_dir = os.path.join(ADDON_DATA_PATH, 'script.skinvariables', 'nodes', skin_id)
    for name in ('skinvariables-shortcut-homewidgets.json',
                 'skinvariables-shortcut-1101widgets.json',
                 'skinvariables-shortcut-1102widgets.json',
                 'skinvariables-shortcut-searchwidgets.json',
                 'skinvariables-shortcut-powermenu.json'):
        data = _fetchv(roots, 'nodes/' + name)
        if data:
            dest = os.path.join(nodes_dir, name)
            _backup_once(dest, 'gears'); _write(dest, data)
    for name in ('script-skinvariables-generator-includes-.xml',
                 'script-skinviewtypes-includes.xml'):
        data = _fetchv(roots, 'skin-overrides/' + name)
        if data:
            dest = os.path.join(ADDONS, skin_id, '1080i', name)
            _backup_once(dest, 'gears'); _write(dest, data)
    # viewtypes source json
    vt = _fetchv(roots, 'skinvariables/' + skin_id + '-viewtypes.json')
    if vt:
        dest = os.path.join(ADDON_DATA_PATH, 'script.skinvariables', skin_id + '-viewtypes.json')
        _backup_once(dest, 'gears'); _write(dest, vt)
    _seed_pov_db(roots)


def _apply_zephyr(roots, skin_id):
    piers = _kodi_major() >= 22
    if piers:
        # Piers Zephyr = skin's own shortcuts menu files
        for name in ('menus.xml', 'templates.xml'):
            data = _fetchv(roots, 'skin-overrides/' + name)
            if data:
                dest = os.path.join(ADDONS, skin_id, 'shortcuts', name)
                _backup_once(dest, 'gears'); _write(dest, data)
    else:
        # K21 Zephyr = script.skinshortcuts DATA + properties + tmdbhelper + viewtypes
        ss = os.path.join(ADDON_DATA_PATH, 'script.skinshortcuts')
        for name in ('srtym-1.DATA.xml', 'sdrvt-1.DATA.xml', 'mainmenu.DATA.xml',
                     'hybvrshyrvtym-1.DATA.xml',
                     'skin.arctic.zephyr.2.resurrection.mod.properties'):
            data = _fetchv(roots, 'skinshortcuts/' + name)
            if data:
                dest = os.path.join(ss, name)
                _backup_once(dest, 'gears'); _write(dest, data)
        # force menu rebuild
        h = os.path.join(ss, 'skin.arctic.zephyr.2.resurrection.mod.hash')
        try:
            if os.path.exists(h):
                os.remove(h)
        except Exception:
            pass
        # tmdbhelper widget engine (settings + player + custom node)
        th = os.path.join(ADDON_DATA_PATH, 'plugin.video.themoviedb.helper')
        for rel, sub in (('themoviedb/settings.xml', 'settings.xml'),
                         ('themoviedb/players/pov.json', 'players/pov.json'),
                         ('themoviedb/nodes/SELECTED NETWORKS.json', 'nodes/SELECTED NETWORKS.json')):
            data = _fetchv(roots, rel)
            if data:
                dest = os.path.join(th, sub)
                _backup_once(dest, 'gears'); _write(dest, data)
    _seed_pov_db(roots)


_APPLY = {
    'skin.estuary': _apply_estuary,
    'skin.nimbus': _apply_nimbus,
    'skin.arctic.fuse.3': _apply_af3,
    'skin.arctic.zephyr.2.resurrection.mod': _apply_zephyr,
}


# ------------------------------------------------------------------ public
def current_source():
    """Best-effort: 'pov' if the stored wizard setting says so, else 'gears'."""
    try:
        import xbmcaddon
        return xbmcaddon.Addon().getSetting('content_source') or 'gears'
    except Exception:
        return 'gears'


def _ensure_pov_installed():
    """Install POV from the manifest if it's not on disk, and register it so
    Kodi won't auto-update it (origin='', updateRule mirrors Gears)."""
    if os.path.isdir(os.path.join(ADDONS, 'plugin.video.pov')):
        return True
    try:
        from resources.libs.builds import BuildManager
        return bool(BuildManager()._install_from_manifest(
            'plugin.video.pov', ['script.module.requests'], 'POV'))
    except Exception as e:
        _log('POV install failed: %s' % e, xbmc.LOGERROR)
        return False


def _apply_pov_core(skin_id):
    """Apply the POV variant for skin_id (no reload, no dialogs). Returns
    (ok, err). Shared by the interactive switch and the install-time apply."""
    variant = _variant_dir(skin_id)
    if not variant or skin_id not in _APPLY:
        return False, 'no POV variant for this skin/version'
    if not _ensure_pov_installed():
        return False, 'POV install failed'
    roots = _variant_roots(skin_id)
    try:
        _APPLY[skin_id](roots, skin_id)
    except Exception as e:
        _log('apply failed: %s' % e, xbmc.LOGERROR)
        return False, str(e)
    return True, None


def install_apply(skin_id, source):
    """Install-time content-source apply: explicit skin_id (the new skin isn't
    active yet at install), no ReloadSkin (the install restart handles it).
    Fail-open: a POV failure leaves the freshly-installed Gears build intact."""
    if source != 'pov':
        _set_source('gears')
        return True
    ok, err = _apply_pov_core(skin_id)
    _set_source('pov' if ok else 'gears')
    if not ok:
        _log('install POV apply skipped: %s' % err, xbmc.LOGWARNING)
    return ok


def switch_to(source):
    """source: 'pov' | 'gears'. Returns True on success."""
    dialog = xbmcgui.Dialog()
    skin_id = xbmc.getSkinDir()
    variant = _variant_dir(skin_id)
    if source == 'pov' and not variant:
        dialog.ok(ADDON_NAME, '[COLOR %s]אין וריאנט POV לסקין הזה בגרסת קודי זו.[/COLOR]' % COLOR_WARNING)
        return False

    if source == 'pov':
        if not _ensure_pov_installed():
            dialog.ok(ADDON_NAME, '[COLOR %s]התקנת POV נכשלה.[/COLOR]' % COLOR_ERROR)
            return False
        roots = _variant_roots(skin_id)
        prog = xbmcgui.DialogProgress()
        prog.create(ADDON_NAME, '[COLOR cyan]מחליף מקור תוכן ל-POV...[/COLOR]')
        try:
            _APPLY[skin_id](roots, skin_id)
        except Exception as e:
            prog.close()
            _log('apply failed: %s' % e, xbmc.LOGERROR)
            dialog.ok(ADDON_NAME, '[COLOR %s]ההחלפה נכשלה:[/COLOR] %s' % (COLOR_ERROR, e))
            return False
        prog.close()
        _set_source('pov')
    else:  # gears -> restore from the .pre_gears backups
        restored = _restore_gears(skin_id)
        # On a CLEAN POV install there are no .pre_gears backups (the Gears
        # config was never applied). Switching to Gears must then BUILD the
        # Gears config fresh instead of restoring nothing -- else the box would
        # stay on POV menus. Force-apply the Gears content config for this skin.
        if not restored:
            _apply_gears_content(skin_id)
        _set_source('gears')

    xbmc.executebuiltin('ReloadSkin()')
    dialog.notification(ADDON_NAME, 'מקור התוכן: %s' % source.upper(),
                        xbmcgui.NOTIFICATION_INFO, 4000)
    return True


def _restore_gears(skin_id):
    """Restore every .pre_gears backup this switcher made (per-skin)."""
    roots = [os.path.join(ADDONS, skin_id),
             os.path.join(ADDON_DATA_PATH, 'script.skinshortcuts'),
             os.path.join(ADDON_DATA_PATH, 'script.skinvariables'),
             os.path.join(ADDON_DATA_PATH, 'script.nimbus.helper'),
             os.path.join(ADDON_DATA_PATH, 'plugin.video.themoviedb.helper'),
             os.path.join(ADDON_DATA_PATH, 'plugin.video.pov'),
             USERDATA]
    restored = 0
    for base in roots:
        if not os.path.isdir(base):
            continue
        for dpath, _dirs, files in os.walk(base):
            for f in list(files):
                if f.endswith('.pre_gears'):
                    src = os.path.join(dpath, f)
                    dst = src[:-len('.pre_gears')]
                    try:
                        shutil.copy2(src, dst)
                        restored += 1
                    except Exception:
                        pass
    _log('restored %d gears backup file(s)' % restored)
    return restored


def _apply_gears_content(skin_id):
    """Build the Gears content config for skin_id from scratch (used when
    switching a clean POV install back to Gears -- no backups to restore).
    Delegates to the wizard's own config engine with content forced to 'gears',
    which delivers the Gears menus/favourites/players/nodes + gears_settings +
    gears_shortcuts, then seeds the gears shortcut folder and per-skin views."""
    try:
        from resources.libs import modular_update as mu
        manifest = mu.fetch_manifest()
        state = mu._load_state()
        mu._maybe_apply_config(manifest, state, force=True, content='gears')
        mu._save_state(state)
        try:
            mu.seed_gears_shortcut_folder()
            mu.apply_gears_views_for_skin(skin_id)
        except Exception as e:
            _log('gears seed after switch failed: %s' % e, xbmc.LOGWARNING)
        _log('applied Gears content config fresh (no backups to restore)')
    except Exception as e:
        _log('apply gears content failed: %s' % e, xbmc.LOGERROR)


def _set_source(source):
    try:
        import xbmcaddon
        xbmcaddon.Addon().setSetting('content_source', source)
    except Exception:
        pass


def menu():
    """Wizard menu action: show current source + let the user switch."""
    dialog = xbmcgui.Dialog()
    cur = current_source()
    skin_id = xbmc.getSkinDir()
    has_pov = bool(_variant_dir(skin_id))
    body = ('מקור התוכן הנוכחי: [B]%s[/B]\n\n'
            'Gears = ברירת המחדל. POV = חלופה (אותם סקינים, אותן כתוביות).\n'
            'ההחלפה משנה את התפריטים/וידג\'טים/חיפוש של הסקין הנוכחי בלבד.'
            % cur.upper())
    if not has_pov:
        body += '\n\n[COLOR %s](אין וריאנט POV לסקין/גרסה זו)[/COLOR]' % COLOR_WARNING
    choices = ['החלף ל-POV' if cur != 'pov' else 'כבר על POV',
               'החזר ל-Gears' if cur != 'gears' else 'כבר על Gears',
               'ביטול']
    sel = dialog.select(ADDON_NAME + ' · מקור תוכן', choices)
    if sel == 0 and cur != 'pov' and has_pov:
        switch_to('pov')
    elif sel == 1 and cur != 'gears':
        switch_to('gears')
