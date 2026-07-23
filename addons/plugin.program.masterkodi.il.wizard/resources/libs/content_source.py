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
import json
import sqlite3
import shutil
import xbmc
import xbmcgui

from resources.libs.config import (
    ADDON_NAME, HOME, ADDONS, USERDATA, ADDON_DATA_PATH,
    COLOR_ERROR, COLOR_WARNING,
)

RAW = 'https://raw.githubusercontent.com/asaf27064/MasterKodi-IL-Build/main/'

# skin dir id -> (k21 variant dir, piers variant dir). 'pov' source only; the
# gears baseline is restored from the shipped config, see revert_to_gears.
SKIN_VARIANTS = {
    'skin.estuary':                          ('estuary-pov', 'estuary-piers-pov'),
    'skin.nimbus':                           ('nimbus-pov', None),
    'skin.arctic.fuse.3':                    ('af3-pov-tmdb', None),
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


def _apply_index(roots, skin_id):
    """Apply every file the variant declares in its index.json.

    Replaces the old hard-coded per-skin file lists, which silently drifted from
    what the variants actually shipped (the wizard cannot list a directory over
    raw.githubusercontent, so a name missing from the list was simply never
    fetched). That cost us the Zephyr MAINTENANCE menu and left the POV power
    menu calling gears' clear_all_cache. The variant directory is now the source
    of truth: tools/gen_variant_index.py regenerates the index, CI checks it is
    current, and adding a file to a variant needs no wizard change.

    Returns (applied, failed)."""
    # MERGE the indexes across roots, base first so a more specific root wins.
    # Taking only the first root's index would drop everything the piers variant
    # doesn't restate -- on Kodi 22 that silently lost Estuary's favourites.xml
    # again, the exact bug the per-file fallback was added to kill.
    # Load most-specific first so we can honour its `inherit` flag: a variant
    # that REPLACES the base (different menu architecture, e.g. Piers Zephyr)
    # sets inherit=false and the base is ignored -- otherwise we would write
    # Omega-era skin XML into a Piers skin. Overlay variants (the default)
    # inherit, so the base still supplies whatever piers doesn't restate.
    merged = {}
    found = False
    loaded = []
    for root in roots:                            # most specific ... base
        raw = _fetch(root + '/index.json')
        if not raw:
            continue
        try:
            loaded.append((root, json.loads(raw.decode('utf-8'))))
            found = True
        except Exception as e:
            _log('bad index.json in %s: %s' % (root, e), xbmc.LOGERROR)
    if loaded and loaded[0][1].get('inherit') is False:
        _log('index: %s is self-contained, not inheriting base' % loaded[0][0])
        loaded = loaded[:1]
    for root, index in reversed(loaded):          # base first, specific wins
        for entry in index.get('files', []):
            if entry.get('src') and entry.get('dest'):
                merged[entry['dest']] = entry
    if not found:
        _log('no index.json for %s (roots=%s)' % (skin_id, roots), xbmc.LOGWARNING)
        return 0, 0
    subs = {'{addons}': ADDONS, '{addon_data}': ADDON_DATA_PATH,
            '{userdata}': USERDATA, '{home}': HOME, '{skin}': skin_id}
    applied = failed = 0
    for entry in merged.values():
        src, dest = entry.get('src'), entry.get('dest')
        if not src or not dest:
            continue
        for k, v in subs.items():
            dest = dest.replace(k, v)
        dest = os.path.normpath(dest)
        data = _fetchv(roots, src)
        if not data:
            failed += 1
            _log('index: could not fetch %s' % src, xbmc.LOGWARNING)
            continue
        try:
            _backup_once(dest, 'gears')
            _write(dest, data)
            applied += 1
        except Exception as e:
            failed += 1
            _log('index: write failed %s: %s' % (dest, e), xbmc.LOGWARNING)
    _log('index applied for %s: %d file(s), %d failed' % (skin_id, applied, failed))
    return applied, failed


# POV login fields to PRESERVE across a variant re-apply. The shipped
# pov/settings.xml carries these as empty (scrubbed) defaults; blindly writing it
# over the live file wiped the user's own debrid/Trakt/etc logins on EVERY wizard
# update (the re-apply keys on the wizard version). Shipping no credentials is now
# guaranteed by tools/check_no_credentials.py, so preserving the user's live
# values here is safe -- we keep the USER's own tokens on the USER's box, we never
# ship one.
_POV_CRED_IDS = {
    'pm.token', 'tb.token', 'rd.token', 'rd.secret', 'rd.username', 'ad.token',
    'oc.token', 'premiumize.token', 'easynews_user', 'easynews_password',
    'trakt.token', 'trakt.refresh', 'trakt.usertoken', 'trakt.user', 'trakt_user',
    'trakt.expires', 'tmdb.token', 'tmdb.username', 'tmdb.sessionid',
    'mdblist.token', 'mdblist_user', 'rpdb_api_key',
    'hebrew_subtitles.ktuvit_password', 'hebrew_subtitles.opensubtitles_apikey',
}


def _merge_preserve_creds(shipped, live_path):
    """Return the shipped settings.xml (bytes) with the user's existing non-empty
    login values carried over from live_path. Regex-based (the files are flat
    <setting id=..> lists); on any parse issue we fall back to shipped as-is."""
    try:
        if not os.path.isfile(live_path):
            return shipped
        import re
        live = open(live_path, encoding='utf-8', errors='replace').read()
        text = shipped.decode('utf-8', 'replace')
        for sid in _POV_CRED_IDS:
            m = re.search(r'<setting id="%s"[^>]*>([^<]+)</setting>' % re.escape(sid), live)
            val = m.group(1).strip() if m else ''
            if not val or val.lower() in ('true', 'false'):
                continue                       # user has no value -> keep shipped
            repl = '<setting id="%s">%s</setting>' % (sid, val)
            pat = re.compile(r'<setting id="%s"[^>]*/>|<setting id="%s"[^>]*>[^<]*</setting>'
                             % (re.escape(sid), re.escape(sid)))
            text, n = pat.subn(repl, text, count=1)
            if n:
                _log('preserved user login: %s' % sid)
        return text.encode('utf-8')
    except Exception as e:
        _log('cred-preserve merge failed (%s), shipping as-is' % e, xbmc.LOGWARNING)
        return shipped


# ------------------------------------------------------------------ POV seeds
def _seed_pov_db(roots):
    """Seed POV navigator.db (shortcut folders) + views.db from pov/*.json.

    These DBs must be CREATED if absent, not skipped: on a clean install POV has
    never run yet, so its addon_data DBs don't exist until after the restart --
    requiring them to pre-exist silently no-opped the whole seed, leaving a
    fresh POV box with default views and no shortcut folders. sqlite3.connect
    creates the file, and the CREATE TABLE below matches POV's own schema, so
    POV simply picks up the pre-seeded rows on first run."""
    pov_data = os.path.join(ADDON_DATA_PATH, 'plugin.video.pov')
    try:
        os.makedirs(pov_data, exist_ok=True)
    except Exception as e:
        _log('could not create POV addon_data dir: %s' % e, xbmc.LOGWARNING)
    # shortcut folders -> navigator.db
    sf = _fetchv(roots, 'pov/shortcut_folders.json')
    ndb = os.path.join(pov_data, 'navigator.db')
    if sf:
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
    if vj:
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
    # POV addon settings -- MERGE, preserving the user's live logins (see
    # _merge_preserve_creds). A blind overwrite wiped debrid/Trakt tokens on
    # every wizard update.
    ps = _fetchv(roots, 'pov/settings.xml')
    if ps:
        dest = os.path.join(pov_data, 'settings.xml')
        _backup_once(dest, 'gears')
        _write(dest, _merge_preserve_creds(ps, dest))


# ------------------------------------------------------------------ per skin
# The file lists live in each variant's index.json (see tools/gen_variant_index.py),
# NOT here -- hard-coded lists drifted and silently dropped files. These wrappers
# now only carry the steps an index cannot express: database seeding and the
# skinshortcuts hash reset that forces Zephyr to rebuild its menus.
# Each returns the number of variant files that FAILED to apply, so the caller
# can refuse to flip the box onto a half-applied (mixed Gears/POV) state.
def _apply_estuary(roots, skin_id):
    _applied, failed = _apply_index(roots, skin_id)
    _seed_pov_db(roots)
    return failed


def _apply_nimbus(roots, skin_id):
    _applied, failed = _apply_index(roots, skin_id)
    # cpath compiled menu config (a sqlite seed, not a file copy)
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
    return failed


def _apply_af3(roots, skin_id):
    _applied, failed = _apply_index(roots, skin_id)
    _seed_pov_db(roots)
    return failed


def _apply_zephyr(roots, skin_id):
    _applied, failed = _apply_index(roots, skin_id)
    # Force skinshortcuts to rebuild the menus from the DATA files we just wrote.
    # Without dropping the hash it keeps serving the previously compiled menu.
    h = os.path.join(ADDON_DATA_PATH, 'script.skinshortcuts',
                     'skin.arctic.zephyr.2.resurrection.mod.hash')
    try:
        if os.path.exists(h):
            os.remove(h)
    except Exception:
        pass
    _seed_pov_db(roots)
    return failed



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
        failed = _APPLY[skin_id](roots, skin_id)
    except Exception as e:
        _log('apply failed: %s' % e, xbmc.LOGERROR)
        return False, str(e)
    # A partial fetch (network drop mid-apply) must NOT be reported as success:
    # flipping the box to POV with only some variant files written leaves a mixed
    # Gears/POV menu. Fail so the caller keeps the prior source and retries later.
    if failed:
        return False, '%d variant file(s) failed to apply' % failed
    return True, None


def install_apply(skin_id, source):
    """Install-time content-source apply: explicit skin_id (the new skin isn't
    active yet at install), no ReloadSkin (the install restart handles it).
    Fail-open: a POV failure leaves the freshly-installed Gears build intact.

    CAUTION: this DOWNGRADES the stored source to 'gears' when a POV apply fails
    -- correct only at install time (Gears build, POV is the optional overlay).
    Do NOT call it on an already-POV box (e.g. a skin switch): a failed apply
    would flip a POV build to 'gears' with no Gears content installed. Use
    _apply_pov_core(skin_id) there -- it never touches the source flag."""
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
            failed = _APPLY[skin_id](roots, skin_id)
        except Exception as e:
            prog.close()
            _log('apply failed: %s' % e, xbmc.LOGERROR)
            dialog.ok(ADDON_NAME, '[COLOR %s]ההחלפה נכשלה:[/COLOR] %s' % (COLOR_ERROR, e))
            return False
        prog.close()
        # partial fetch -> do NOT switch source; leave the box on Gears so it
        # isn't left with a half-POV menu. The user can retry.
        if failed:
            dialog.ok(ADDON_NAME, '[COLOR %s]ההחלפה נכשלה: %d קבצים לא הורדו. נסה שוב.[/COLOR]'
                      % (COLOR_ERROR, failed))
            return False
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
               # Re-apply the CURRENT source for the CURRENT skin. Without this
               # there was no way to pick up a fixed/updated variant short of a
               # full reinstall: the switch options refuse to run when you are
               # already on that source, so a corrected menu/widget file could
               # never reach an existing box.
               'החל מחדש את התצורה (%s) לסקין הנוכחי' % cur.upper(),
               'ביטול']
    sel = dialog.select(ADDON_NAME + ' · מקור תוכן', choices)
    if sel == 0 and cur != 'pov' and has_pov:
        switch_to('pov')
    elif sel == 1 and cur != 'gears':
        switch_to('gears')
    elif sel == 2:
        if cur == 'pov':
            if not has_pov:
                dialog.ok(ADDON_NAME,
                          '[COLOR %s]אין וריאנט POV לסקין/גרסה זו.[/COLOR]' % COLOR_WARNING)
                return False
            prog = xbmcgui.DialogProgress()
            prog.create(ADDON_NAME, '[COLOR cyan]מחיל מחדש את תצורת POV...[/COLOR]')
            ok, err = _apply_pov_core(skin_id)
            prog.close()
            if not ok:
                dialog.ok(ADDON_NAME, '[COLOR %s]ההחלה נכשלה:[/COLOR] %s' % (COLOR_ERROR, err))
                return False
        else:
            _apply_gears_content(skin_id)
        xbmc.executebuiltin('ReloadSkin()')
        dialog.notification(ADDON_NAME, 'התצורה הוחלה מחדש (%s)' % cur.upper(),
                            xbmcgui.NOTIFICATION_INFO, 4000)
        return True
