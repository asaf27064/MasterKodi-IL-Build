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
HOME_ADDONS = os.path.join(xbmcvfs.translatePath('special://home/'), 'addons')
WIZARD_ID = 'plugin.program.masterkodi.il.wizard'
STAGE = os.path.join(ADDON_DATA, WIZARD_ID, '_keep_backup')   # survives the wipe

# Never treated as a "user extra" (part of our own build machinery).
_PROTECTED = {WIZARD_ID, 'service.kodi.il.firstrun', 'repository.masterkodi.il', 'packages',
              # known-dead legacy repos (JUNK_REPOS in modular_update): never
              # offer to carry them across a reinstall -- the keep step used to
              # resurrect them onto FRESH installs, and the junk purge then had
              # to re-delete them (seen on the 2026-07-18 Windows install)
              'repository.burekasKodi', 'repository.funstersplace',
              'repository.jenrepo', 'repository.universalscrapers',
              # dead kodi7rd repo -- purged from bundles, but a box that still has
              # it must NOT carry it across a reinstall as a "user extra" (it would
              # be restored + enabled before the junk-purge, and survive if that
              # completion ever failed). Keep in sync with modular_update.JUNK_REPOS.
              'repository.KodiRealDebridIsrael'}


def detect_extras(manifest_ids):
    """Addons under home/addons that the user installed themselves -- i.e. not
    part of the build (manifest) and not our own machinery. Kodi's bundled
    system addons normally live in special://xbmc/addons, but SOME (the default
    music metadata scrapers metadata.album.universal / metadata.artists.universal
    / metadata.generic.albums, and others) get copied into the profile
    home/addons dir. Those were being offered as "addons you installed yourself",
    which is a false positive -- the user never installed them. Skip anything
    that ALSO exists in Kodi's own addons dir: an id present there is a Kodi
    default, not a user addon."""
    kodi_addons = xbmcvfs.translatePath('special://xbmc/addons')
    out = []
    try:
        for name in sorted(os.listdir(HOME_ADDONS)):
            p = os.path.join(HOME_ADDONS, name)
            if not os.path.isdir(p) or name in manifest_ids or name in _PROTECTED:
                continue
            if '_old_' in name or name.endswith('_old'):
                continue
            # Kodi's own default addons (present in special://xbmc/addons) are
            # never "user-installed" even when a copy lives in the profile dir.
            if os.path.isdir(os.path.join(kodi_addons, name)):
                continue
            if os.path.isfile(os.path.join(p, 'addon.xml')):
                out.append(name)
    except Exception as e:
        log('detect_extras failed: %s' % e, xbmc.LOGWARNING)
    return out

GEARS_DB_DIR = os.path.join(ADDON_DATA, 'plugin.video.gears', 'databases')
GEARS_SETTINGS_DB = os.path.join(GEARS_DB_DIR, 'settings.db')
GEARSAI_SETTINGS = os.path.join(ADDON_DATA, 'service.subtitles.gearsai', 'settings.xml')
TMDBH_SETTINGS = os.path.join(ADDON_DATA, 'plugin.video.themoviedb.helper', 'settings.xml')
FAVOURITES = os.path.join(USERDATA, 'favourites.xml')


def log(msg, level=xbmc.LOGINFO):
    xbmc.log('[%s.keep] %s' % (WIZARD_ID, msg), level)


# Ordered so the multiselect reads sensibly; all preselected by default.
# POV stores its logins in plugin.video.pov/settings.xml (XML, not a settings.db
# like Gears), so the keep groups must cover it too -- otherwise a POV box saved
# NOTHING and lost every login on a reinstall.
POV_SETTINGS = os.path.join(ADDON_DATA, 'plugin.video.pov', 'settings.xml')
# POV keeps its databases DIRECTLY under addon_data/plugin.video.pov (NOT in a
# databases/ subdir like Gears) -- see plugin.video.pov kodi_utils.py.
POV_DB_DIR = os.path.join(ADDON_DATA, 'plugin.video.pov')
_POV_DEBRID = ['pm.token', 'pm.account_id', 'tb.token', 'tb.account_id',
               'oc.token', 'oc.account_id', 'ad.token', 'ad.account_id',
               'rd.token', 'rd.secret', 'rd.username', 'rd.client_id', 'rd.refresh',
               'premiumize.token', 'easynews_user', 'easynews_password']
_POV_TRAKT = ['trakt.token', 'trakt.refresh', 'trakt.usertoken', 'trakt.user', 'trakt_user']

# --------------------------------------------------------------------------- #
# Debrid / Trakt credential ids.
#
# POV and Gears use the SAME setting ids (tb.token, rd.token, pm.token,
# ad.token, trakt.token ...). What differs is the STORAGE: POV keeps them in
# addon_data/plugin.video.pov/settings.xml, Gears in its binary settings.db.
# Carrying credentials between the engines is therefore a COPY, never a rename.
#
# VERIFIED against Asaf's live box 2026-08-02: Gears' settings.db really holds
# tb.token / tb.enabled / rd.* / pm.token / ad.token / oc.token. The ids
# previously listed for Gears -- `torbox.api_key`, `premiumize.token`,
# `alldebrid.token` -- exist in NEITHER engine, so those logins were never
# staged at all: a plain Gears->Gears KEEP reinstall lost the TorBox login too,
# not only a cross-source one.
#
# Deliberately excluded: the *.account_id fields (each engine re-derives them
# from the token) and rd.client_id / rd.secret (per-install OAuth app
# registration -- pushing one box's registration onto another is wrong).
# --------------------------------------------------------------------------- #
_DEBRID_IDS = ['tb.token', 'tb.enabled', 'rd.token', 'rd.refresh',
               'pm.token', 'ad.token', 'oc.token',
               'easynews_user', 'easynews_password']
# Both spellings of the Trakt username are listed so the value is STAGED from
# whichever engine we back up; _ID_ALIASES renames it on the way in.
_TRAKT_IDS = ['trakt.token', 'trakt.refresh', 'trakt.user', 'trakt_user']

# Nearly every id is identical across the engines -- storage differs, not names.
# The ONE exception found on a live box (2026-08-02): Gears stores the Trakt
# username as `trakt.user`, POV as `trakt_user` (POV's settings.xml has
# trakt_user and no trakt.user at all). An identity copy therefore wrote an id
# POV does not read, and the username silently vanished on a Gears->POV
# reinstall -- caught only because the snapshot count fell 10 -> 9.
_ID_ALIASES = {'trakt.user': 'trakt_user'}                 # gears id -> pov id
_ID_ALIASES_REV = {v: k for k, v in _ID_ALIASES.items()}   # pov id -> gears id

# Values meaning "not configured" -- carrying these across would make an empty
# slot look like a live account.
_PLACEHOLDERS = (None, '', 'false', 'true', '0', 'None')


def _real_creds(values, allowed, rename=None):
    """{id: value} limited to `allowed` ids, placeholders dropped, ids renamed
    into the TARGET engine's spelling (see _ID_ALIASES)."""
    rename = rename or {}
    return {rename.get(k, k): v for k, v in (values or {}).items()
            if k in allowed and v not in _PLACEHOLDERS}
_POV_SERVICES = ['tmdb.token', 'tmdb.username', 'tmdb.account_id',
                 'tmdb.session_account_id', 'tmdb.session_id',
                 'mdblist.token', 'mdblist_user', 'rpdb_api_key',
                 'hebrew_subtitles.ktuvit_password', 'hebrew_subtitles.opensubtitles_apikey']

GROUPS = [
    {'key': 'debrid',
     'label': 'התחברות Debrid (RD / TorBox / Premiumize / AllDebrid)',
     'gears_ids': _DEBRID_IDS,
     'xml_targets': [(POV_SETTINGS, _POV_DEBRID)]},
    {'key': 'trakt',
     'label': 'התחברות Trakt',
     'gears_ids': _TRAKT_IDS,
     'xml_targets': [(TMDBH_SETTINGS, ['trakt.token', 'trakt.refreshtoken', 'trakt.usertoken']),
                     (POV_SETTINGS, _POV_TRAKT)]},
    {'key': 'gemini',
     'label': 'מפתח Gemini אישי (כתוביות AI)',
     'xml_targets': [(GEARSAI_SETTINGS, ['api_key', 'extra_api_keys'])]},
    {'key': 'pov_services',
     'label': 'חשבונות POV (TMDb / MDbList / כתוביות)',
     'xml_targets': [(POV_SETTINGS, _POV_SERVICES)]},
    {'key': 'gears_content',
     'label': 'צפייה, המשך צפייה ורשימות (Gears)',
     'db_files': ['watched.db', 'personal_lists.db', 'lists.db', 'tmdb_lists.db', 'favourites.db']},
    {'key': 'pov_content',
     'label': 'צפייה, המשך צפייה והיסטוריה (POV)',
     # watched/resume + search history live in POV's OWN databases dir, NOT the
     # settings xml -- without this a POV reinstall kept the logins but lost all
     # viewing state. (navigator/views are re-seeded on install so we don't stage
     # them here; watched/maincache are pure user data, untouched by the seed.)
     'pov_db_files': ['watched.db', 'maincache.db']},
    {'key': 'favs',
     'label': 'מועדפים (Kodi)',
     'files': [FAVOURITES]},
]


# --------------------------------------------------------------------------- #
# sqlite settings.db helpers (table: settings(setting_id, setting_value))
# --------------------------------------------------------------------------- #
def _db_read(db, ids):
    """Returns a dict of found values, {} if the db is absent (fine -- nothing to
    keep), or None on a READ ERROR (locked/corrupt) so backup() can tell a real
    failure apart from 'the user has no values' and refuse to wipe silently."""
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
        log('settings.db read FAILED (%s): %s' % (db, e), xbmc.LOGERROR)
        return None
    return out


# kept Gears credentials that couldn't be written yet (settings.db not born on a
# fresh install) are stashed here and applied by modular_update's first-boot
# catch-up. Without this the creds were silently dropped and the backup deleted.
KEEP_PENDING = os.path.join(ADDON_DATA, WIZARD_ID, 'gears_keep_pending.json')


def _db_write(db, values):
    """Write settings into a gears settings.db. Returns:
      True   -> written
      'nodb' -> the DB doesn't exist yet (fresh install; caller should defer)
      False  -> a real write error (caller should treat as a restore failure)."""
    if not values:
        return True
    if not os.path.isfile(db):
        return 'nodb'
    try:
        c = sqlite3.connect(db)
        for sid, val in values.items():
            cur = c.execute('UPDATE settings SET setting_value=? WHERE setting_id=?', (val, sid))
            if cur.rowcount == 0:
                # let an INSERT failure RAISE -> caught below -> False. Swallowing
                # it (as before) then returned True, so the keep-stage was deleted
                # even though that credential row was never written.
                c.execute('INSERT INTO settings (setting_id, setting_value) VALUES (?, ?)', (sid, val))
        c.commit()
        # readback: confirm every value actually landed before we report success
        for sid, val in values.items():
            row = c.execute('SELECT setting_value FROM settings WHERE setting_id=?', (sid,)).fetchone()
            if not row or row[0] != val:
                c.close()
                log('settings.db write UNVERIFIED for %s' % sid, xbmc.LOGERROR)
                return False
        c.close()
        return True
    except Exception as e:
        log('settings.db write failed: %s' % e, xbmc.LOGERROR)
        return False


def _stash_keep_pending(values):
    """Defer kept Gears creds to the first-boot catch-up when the db isn't born."""
    try:
        os.makedirs(os.path.dirname(KEEP_PENDING), exist_ok=True)
        existing = {}
        if os.path.isfile(KEEP_PENDING):
            try:
                existing = json.load(open(KEEP_PENDING, encoding='utf-8-sig'))
            except Exception:
                existing = {}
        existing.update(values)
        json.dump(existing, open(KEEP_PENDING, 'w', encoding='utf-8'), ensure_ascii=False)
        log('deferred %d kept gears cred(s) to first-boot catch-up' % len(values))
        return True
    except Exception as e:
        log('stash keep-pending failed: %s' % e, xbmc.LOGERROR)
        return False


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
        log('xml read FAILED %s: %s' % (path, e), xbmc.LOGERROR)
        return None                          # read error -> a real failure, not "empty"
    return out


def _xml_write(path, values):
    """Write settings into an XML settings file (created if absent). Returns
    True on success, False on error, so restore() can count a real failure."""
    if not values:
        return True
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
        return True
    except Exception as e:
        log('xml write failed %s: %s' % (path, e), xbmc.LOGERROR)
        return False


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def has_anything(extras=None):
    """Is there actually anything worth carrying across the wipe?

    Used to skip the 'what to keep' dialog entirely on a box where nothing is
    configured yet (fresh install, or a reinstall right after one) -- the
    checklist is pure noise there. Cheap probes only: existence/non-empty, no
    full reads."""
    try:
        if extras:
            return True
        for g in GROUPS:
            if _group_has_data(g):
                return True
    except Exception as e:
        log('has_anything probe failed: %s' % e, xbmc.LOGWARNING)
        return True          # unsure -> ask, never silently skip a real backup
    return False


def _group_has_data(g):
    """Cheap probe: does THIS box actually have anything to keep for group g?

    Mirrors exactly what backup() would stage (existence/non-empty only, no full
    reads). Used to hide empty groups from the checklist -- notably a Gears box
    has no POV data and a POV box has no Gears data, so this naturally makes the
    prompt source-aware WITHOUT guessing the content source: a group is shown iff
    its data is present, and backup() stages nothing for an absent group anyway,
    so hiding it can never drop a real backup."""
    try:
        if g.get('gears_ids') and _db_read(GEARS_SETTINGS_DB, g['gears_ids']):
            return True
        for path, ids in g.get('xml_targets', []):
            if _xml_read(path, ids):
                return True
        for name in g.get('db_files', []):
            if os.path.isfile(os.path.join(GEARS_DB_DIR, name)):
                return True
        for name in g.get('pov_db_files', []):
            if os.path.isfile(os.path.join(POV_DB_DIR, name)):
                return True
        for f in g.get('files', []):
            if os.path.isfile(f) and os.path.getsize(f) > 0:
                return True
    except Exception as e:
        log('group probe failed for %s: %s' % (g.get('key'), e), xbmc.LOGWARNING)
        return True          # unsure -> show it, never silently hide a real backup
    return False


def prompt(extras=None, default_all=True, exclude=None):
    """Show the 'what to keep' checklist (all ticked by default). Returns a list
    of selected group keys (may include 'extras'). Cancel -> keep the defaults
    (all) so nothing is lost by accident.

    Only groups that actually have data on this box are listed: this hides the
    other content source's groups (POV groups on a Gears box, and vice versa) and
    any empty group, so the checklist shows only real, keepable data. `exclude`
    drops keys the CALLER already decided not to offer -- a CROSS-source
    reinstall (Gears->POV or back) excludes the old source's viewing data and
    the favourites (they'd be orphans / clobber the new source's config)."""
    exclude = set(exclude or ())
    entries = [(g['key'], g['label']) for g in GROUPS
               if g['key'] not in exclude and _group_has_data(g)]
    if extras:
        entries.append(('extras', 'תוספים שהתקנת בעצמך (%d)' % len(extras)))
    labels = [lbl for _k, lbl in entries]
    preselect = list(range(len(entries))) if default_all else []
    try:
        chosen = xbmcgui.Dialog().multiselect('מה לשמור בהתקנה?', labels, preselect=preselect)
    except Exception:
        chosen = preselect
    if chosen is None:
        chosen = preselect
    return [entries[i][0] for i in chosen]


def _safe_db_copy(src, dst):
    """Consistent snapshot of a live SQLite database via the backup API (which
    checkpoints WAL), then VERIFY the staged copy with PRAGMA quick_check. Returns
    True only on a verified-good copy. These are all known SQLite files, so a
    backup-API error means locked/corrupt -- we do NOT fall back to a raw copy2
    (that used to stage a torn/corrupt db and report success, so the wipe then
    destroyed the good original)."""
    import sqlite3
    try:
        s = sqlite3.connect(src)
        try:
            d = sqlite3.connect(dst)
            try:
                s.backup(d)
            finally:
                d.close()
        finally:
            s.close()
    except Exception as e:
        log('safe_db_copy backup failed for %s: %s' % (os.path.basename(src), e), xbmc.LOGERROR)
        try:
            if os.path.isfile(dst):
                os.remove(dst)
        except Exception:
            pass
        return False
    # integrity-verify the STAGED copy before trusting it
    try:
        v = sqlite3.connect(dst)
        row = v.execute('PRAGMA quick_check').fetchone()
        v.close()
        if not row or str(row[0]).lower() != 'ok':
            log('safe_db_copy quick_check FAILED for %s: %s' % (os.path.basename(dst), row), xbmc.LOGERROR)
            return False
    except Exception as e:
        log('safe_db_copy verify failed for %s: %s' % (os.path.basename(dst), e), xbmc.LOGERROR)
        return False
    return True


def backup(keys, extras=None, target_content=None, source_content=None):
    """Snapshot the selected groups to STAGE (call BEFORE the wipe).

    target_content ('gears'/'pov') is the CONTENT SOURCE of the build being
    installed; together with this box's current source it's recorded in the
    manifest so restore() can be source-aware on a CROSS-source reinstall
    (skip data that belongs to an engine the new build doesn't ship).

    Returns (ok, staged_count). ok is False if the stage can't be created OR any
    selected item that exists failed to copy OR the manifest couldn't be written
    -- the caller MUST honour it (it already prompts 'backup failed, continue?').
    Everything after the backup is destructive: a partial backup reported as
    success is exactly how 'kept' data gets destroyed by the wipe."""
    try:
        if os.path.isdir(STAGE):
            shutil.rmtree(STAGE, ignore_errors=True)
        os.makedirs(STAGE, exist_ok=True)
    except Exception as e:
        log('cannot create stage: %s' % e, xbmc.LOGERROR)
        return False, 0
    staged = 0
    failed = 0
    # The CALLER must pass source_content: install_build flips the live
    # content_source setting to the TARGET before the keep step runs, so reading
    # the setting here yields the target and cross-source detection silently
    # collapses (caught on-device 2026-07-30: gears creds were still deferred on a
    # POV-target install). Only fall back to the setting when not supplied.
    if source_content:
        _src_content = source_content
    else:
        try:
            import xbmcaddon
            _src_content = xbmcaddon.Addon().getSetting('content_source') or 'gears'
        except Exception:
            _src_content = 'gears'
    saved = {'keys': keys, 'settings': {}, 'xml': {},
             'source_content': _src_content, 'target_content': target_content or _src_content}
    for g in GROUPS:
        if g['key'] not in keys:
            continue
        if g.get('gears_ids'):
            _got = _db_read(GEARS_SETTINGS_DB, g['gears_ids'])
            if _got is None:                 # read ERROR (locked/corrupt), not empty
                failed += 1
                log('keep: could not read gears settings for group %s' % g['key'], xbmc.LOGERROR)
                _got = {}
            saved['settings'].setdefault('gears', {}).update(_got)
            staged += len(_got)
        for path, ids in g.get('xml_targets', []):
            _gotx = _xml_read(path, ids)
            if _gotx is None:                # read ERROR, not empty
                failed += 1
                log('keep: could not read %s for group %s' % (path, g['key']), xbmc.LOGERROR)
                _gotx = {}
            saved['xml'].setdefault(path, {}).update(_gotx)
            staged += len(_gotx)
        for name in g.get('db_files', []):
            src = os.path.join(GEARS_DB_DIR, name)
            if os.path.isfile(src):
                if _safe_db_copy(src, os.path.join(STAGE, 'gearsdb__' + name)):
                    staged += 1
                else:
                    failed += 1
                    log('backup db %s FAILED' % name, xbmc.LOGERROR)
        for name in g.get('pov_db_files', []):
            src = os.path.join(POV_DB_DIR, name)
            if os.path.isfile(src):
                if _safe_db_copy(src, os.path.join(STAGE, 'povdb__' + name)):
                    staged += 1
                else:
                    failed += 1
                    log('backup POV db %s FAILED' % name, xbmc.LOGERROR)
        for f in g.get('files', []):
            if os.path.isfile(f):
                try:
                    shutil.copy2(f, os.path.join(STAGE, 'file__' + os.path.basename(f))); staged += 1
                except Exception as e:
                    failed += 1
                    log('backup file %s failed: %s' % (f, e), xbmc.LOGERROR)
    # user-installed extra addons: whole folder + their addon_data
    if 'extras' in (keys or []) and extras:
        for aid in extras:
            src = os.path.join(HOME_ADDONS, aid)
            if os.path.isdir(src):
                try:
                    shutil.copytree(src, os.path.join(STAGE, 'addon__' + aid)); staged += 1
                except Exception as e:
                    failed += 1
                    log('backup addon %s failed: %s' % (aid, e), xbmc.LOGERROR)
            ad = os.path.join(ADDON_DATA, aid)
            if os.path.isdir(ad):
                try:
                    shutil.copytree(ad, os.path.join(STAGE, 'addondata__' + aid)); staged += 1
                except Exception as e:
                    failed += 1
                    log('backup addon_data %s failed: %s' % (aid, e), xbmc.LOGERROR)
    # The manifest is what restore() reads -- if it doesn't land, EVERYTHING
    # staged is unrecoverable, so a manifest write failure is fatal (ok=False).
    manifest_ok = True
    try:
        json.dump(saved, open(os.path.join(STAGE, 'manifest.json'), 'w', encoding='utf-8'))
    except Exception as e:
        manifest_ok = False
        log('save manifest FAILED: %s' % e, xbmc.LOGERROR)
    ok = (failed == 0) and manifest_ok
    log('backed up groups: %s (%d staged, %d failed, ok=%s)'
        % (', '.join(keys) if keys else 'none', staged, failed, ok))
    return ok, staged


def restore():
    """Write back whatever backup() staged (call AFTER install + config).

    Returns (restored_addon_ids, failed_count). CRITICAL: on ANY failure the
    STAGE backup is NOT deleted -- the source data was already wiped, so the
    staged copy is the user's only remaining copy. Deleting it on a failed
    restore (as this used to) is the final, unrecoverable data loss."""
    restored_addons = []
    failed = 0
    mf = os.path.join(STAGE, 'manifest.json')
    if not os.path.isfile(mf):
        return restored_addons, 0            # nothing staged -> nothing to lose
    try:
        saved = json.load(open(mf, encoding='utf-8'))
    except Exception as e:
        log('restore: manifest unreadable, keeping STAGE: %s' % e, xbmc.LOGERROR)
        return restored_addons, 1            # staged data exists but unreadable
    # Source-awareness: on a CROSS-source reinstall (Gears->POV or back) the new
    # build does NOT ship the old engine, so its viewing dbs would be orphans and
    # the old favourites (plugin:// links into the absent engine) would CLOBBER
    # the favourites the new source's config just delivered. Skip those; the old
    # favourites are parked next to favourites.xml for manual recovery.
    _src = saved.get('source_content') or 'gears'
    _tgt = saved.get('target_content') or _src
    cross = (_src != _tgt)
    if cross:
        log('keep: cross-source restore (%s -> %s) -- source-specific data skipped'
            % (_src, _tgt))

    # gears settings.db credentials. On a fresh install the db isn't born yet
    # (the base zip ships none) -> defer to the first-boot catch-up instead of
    # silently dropping the creds (which then deleted the only backup).
    # On a POV-target cross install there will NEVER be a gears settings.db --
    # writing/stashing would wait forever, so skip (debrid login is redone in POV).
    gears_creds = dict(saved.get('settings', {}).get('gears', {}))
    # CROSS-SOURCE credential carry-over. The same account lives under different
    # ids per engine, so on POV->Gears the POV xml values (which we are about to
    # skip, since the new build has no POV) are the ONLY copy of the user's
    # logins -- translate them into gears ids instead of dropping them. Values
    # already read from a real gears db win over anything mapped.
    if cross and _tgt == 'gears':
        _carried = {}
        for path, values in saved.get('xml', {}).items():
            if 'plugin.video.pov' in path.replace('\\', '/'):
                _carried.update(_real_creds(values, _DEBRID_IDS + _TRAKT_IDS,
                                            _ID_ALIASES_REV))   # -> gears ids
        if _carried:
            _carried.update(gears_creds)        # a real gears value always wins
            gears_creds = _carried
            log('keep: carried %d credential(s) POV -> Gears' % len(_carried))
    if gears_creds and not (cross and _tgt == 'pov'):
        res = _db_write(GEARS_SETTINGS_DB, gears_creds)
        if res == 'nodb':
            if not _stash_keep_pending(gears_creds):
                failed += 1                  # couldn't even defer -> keep STAGE
        elif res is False:
            failed += 1                      # real write error -> keep STAGE
    elif gears_creds:
        log('keep: %d gears cred(s) not written directly (POV build has no Gears)'
            % len(gears_creds))
    # xml settings (gearsai key, tmdb-helper trakt, pov logins). On a GEARS-target
    # cross install don't write into plugin.video.pov's addon_data (the addon
    # isn't shipped -- it would just plant orphan credential files).
    _xml = {k: dict(v) for k, v in saved.get('xml', {}).items()}
    # ...and the mirror of the carry-over above: on Gears->POV the gears db values
    # are the only copy, so translate them INTO the POV settings.xml we are about
    # to write. POV's own staged values (if any) win.
    if cross and _tgt == 'pov' and gears_creds:
        _pov_path = next((p for p in _xml if 'plugin.video.pov' in p.replace('\\', '/')),
                         POV_SETTINGS)
        _carried = _real_creds(gears_creds, _DEBRID_IDS + _TRAKT_IDS,
                               _ID_ALIASES)                      # -> pov ids
        if _carried:
            _carried.update(_xml.get(_pov_path) or {})   # staged POV value wins
            _xml[_pov_path] = _carried
            log('keep: carried %d credential(s) Gears -> POV' % len(_carried))
    for path, values in _xml.items():
        if cross and _tgt == 'gears' and 'plugin.video.pov' in path.replace('\\', '/'):
            log('keep: skipping POV xml restore (Gears build has no POV)')
            continue
        if not _xml_write(path, values):
            failed += 1                      # real write error -> keep STAGE

    # staged db files, plain files, and extra addons/addon_data
    for name in os.listdir(STAGE):
        try:
            if name.startswith('gearsdb__'):
                if _tgt == 'pov':
                    log('keep: skipping %s (POV build has no Gears)' % name)
                    continue
                os.makedirs(GEARS_DB_DIR, exist_ok=True)
                shutil.copy2(os.path.join(STAGE, name),
                             os.path.join(GEARS_DB_DIR, name[len('gearsdb__'):]))
            elif name.startswith('povdb__'):
                if _tgt == 'gears':
                    log('keep: skipping %s (Gears build has no POV)' % name)
                    continue
                os.makedirs(POV_DB_DIR, exist_ok=True)
                shutil.copy2(os.path.join(STAGE, name),
                             os.path.join(POV_DB_DIR, name[len('povdb__'):]))
            elif name.startswith('file__'):
                if name[len('file__'):] == 'favourites.xml':
                    if cross:
                        park = FAVOURITES.replace('favourites.xml',
                                                  'favourites.pre_%s.xml' % _src)
                        shutil.copy2(os.path.join(STAGE, name), park)
                        log('keep: favourites parked at %s (cross-source)' % park)
                    else:
                        shutil.copy2(os.path.join(STAGE, name), FAVOURITES)
            elif name.startswith('addon__'):
                aid = name[len('addon__'):]
                dst = os.path.join(HOME_ADDONS, aid)
                # An ADDON is executable code: MERGING a staged tree over an
                # existing one would leave a hybrid of old + new .py/.so (mismatched
                # modules -> import errors). Replace it wholesale. On a fresh
                # install dst won't exist anyway; if it does we clear it first so
                # the restored addon is internally consistent. A failure raises ->
                # counted -> STAGE kept.
                if os.path.isdir(dst):
                    shutil.rmtree(dst, ignore_errors=True)
                shutil.copytree(os.path.join(STAGE, name), dst)
                restored_addons.append(aid)
            elif name.startswith('addondata__'):
                aid = name[len('addondata__'):]
                dst = os.path.join(ADDON_DATA, aid)
                # addon_data is user STATE, not code -> MERGE (staged wins) so a
                # dir a fresh Kodi/bundle pre-created doesn't cause us to silently
                # drop the user's settings (which then deleted the only backup).
                shutil.copytree(os.path.join(STAGE, name), dst, dirs_exist_ok=True)
        except Exception as e:
            failed += 1
            log('restore %s failed: %s' % (name, e), xbmc.LOGERROR)
    log('restore complete (extras: %s, %d failed)'
        % (', '.join(restored_addons) or 'none', failed))
    # Only drop the backup when EVERYTHING restored. On any failure keep STAGE so
    # the user (or a retry) can still recover -- the originals are already gone.
    if failed == 0:
        cleanup()
    else:
        log('restore had %d failure(s); STAGE kept for recovery: %s' % (failed, STAGE),
            xbmc.LOGERROR)
    return restored_addons, failed


def cleanup():
    try:
        if os.path.isdir(STAGE):
            shutil.rmtree(STAGE, ignore_errors=True)
    except Exception:
        pass
