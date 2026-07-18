# -*- coding: utf-8 -*-
"""
MasterKodi IL - modular auto-updater (manifest driven).

Reads the build manifest published by the MasterKodi-IL-Build CI, compares each
addon's installed version + content hash against it, downloads ONLY what changed,
verifies the sha256, extracts it, and refreshes Kodi's addon database.

Design goals (why it's built this way):
  * Hash-verified: every download is checked against the manifest sha256 before
    it touches the addons folder. A corrupted/half download is never applied.
  * Minimal: only addons whose version OR content hash differ are fetched.
  * Safe optional skins: heavy skins (Arctic Fuse, Nimbus) are flagged
    'optional' in the manifest and only updated if the user already has them.
  * Self-update aware: if the wizard itself changes, it's applied but flagged so
    the caller can advise a restart (a running script can't reload itself).

The whole update contract is one JSON file, so the client stays tiny.
"""

import hashlib
import io
import json
import os
import re
import shutil
import zipfile

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

try:
    from urllib.request import urlopen, Request
except ImportError:  # py2 safety, never hit on Kodi 19+
    from urllib2 import urlopen, Request

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo('id')
ADDON_PATH = xbmcvfs.translatePath(ADDON.getAddonInfo('path'))
ADDON_DATA = xbmcvfs.translatePath('special://userdata/addon_data/%s' % ADDON_ID)
ADDONS_PATH = xbmcvfs.translatePath('special://home/addons/')


def _kodi_major():
    try:
        return int(xbmc.getInfoLabel('System.BuildVersion').split('.')[0])
    except Exception:
        return 0


# Kodi major version (21 = Omega, 22 = Piers). Gates the Omega-only machinery:
# on Piers the Zephyr menu/widget defaults are DECLARED IN THE SKIN
# (skinshortcuts v3 shortcuts/menus.xml), so the old menu bundle and the
# config-delivered viewtypes are obsolete there.
KODI_MAJOR = _kodi_major()
STATE_FILE = os.path.join(ADDON_DATA, 'applied_manifest.json')

MANIFEST_URL = 'https://raw.githubusercontent.com/asaf27064/MasterKodi-IL-Build/main/manifest.json'
if KODI_MAJOR >= 22:
    # Piers fleet reads its own manifest (piers skin variants + skinshortcuts
    # 3.0.1). Both fleets build from main since the repo restructure.
    MANIFEST_URL = ('https://raw.githubusercontent.com/asaf27064/MasterKodi-IL-Build/'
                    'main/manifest-piers.json')

# The wizard never blind-updates these Kodi-core/system ids even if present.
NEVER_TOUCH = {'xbmc.python'}


def _vparts(v):
    """Split a version into comparable integer/string tuples (semver-ish)."""
    out = []
    for tok in str(v).replace('+', '.').replace('-', '.').replace('~', '.').split('.'):
        out.append((0, int(tok)) if tok.isdigit() else (1, tok))
    return out


def version_newer(a, b):
    """True if version a is strictly newer than b."""
    try:
        return _vparts(a) > _vparts(b)
    except Exception:
        return str(a) != str(b)


def log(msg, level=xbmc.LOGINFO):
    xbmc.log('[%s.modupd] %s' % (ADDON_ID, msg), level)


# --------------------------------------------------------------------------- #
# networking
# --------------------------------------------------------------------------- #
def _download(url, timeout=120, attempts=4):
    """Bytes with retry/backoff (mobile data + GitHub CDN drop connections)."""
    last = None
    for attempt in range(1, attempts + 1):
        try:
            req = Request(url, headers={'User-Agent': 'Kodi-MasterKodi'})
            data = urlopen(req, timeout=timeout).read()
            if data:
                return data
            last = 'empty response'
        except Exception as e:
            last = e
            log('download %d/%d failed %s: %s' % (attempt, attempts, url, e), xbmc.LOGWARNING)
        if attempt < attempts:
            xbmc.sleep(1500 * attempt)
    raise Exception('download failed after %d attempts: %s' % (attempts, last))


def fetch_manifest(url=MANIFEST_URL):
    raw = _download(url, timeout=30)
    # utf-8-sig tolerates a stray BOM from raw.githubusercontent
    return json.loads(raw.decode('utf-8-sig'))


# --------------------------------------------------------------------------- #
# local state
# --------------------------------------------------------------------------- #
def _installed_version(addon_id):
    """Version from the installed addon.xml on disk, or None if not installed."""
    xml = os.path.join(ADDONS_PATH, addon_id, 'addon.xml')
    if not os.path.isfile(xml):
        return None
    try:
        import re
        with open(xml, 'r', encoding='utf-8', errors='replace') as fh:
            head = fh.read(4000)
        m = re.search(r'<addon\s+[^>]*version="([^"]+)"', head) or re.search(r'version="([^"]+)"', head)
        return m.group(1) if m else None
    except Exception:
        return None


def _load_state():
    try:
        with open(STATE_FILE, 'r', encoding='utf-8') as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save_state(state):
    try:
        os.makedirs(ADDON_DATA, exist_ok=True)
        with open(STATE_FILE, 'w', encoding='utf-8') as fh:
            json.dump(state, fh, indent=2)
    except Exception as e:
        log('could not save state: %s' % e, xbmc.LOGWARNING)


# --------------------------------------------------------------------------- #
# gears shortcut-folder seed
# --------------------------------------------------------------------------- #

# The default home menus (Zephyr skinshortcuts bundle + AF3 skinvariables
# nodes) ship a streaming-networks widget that points at this Gears
# "shortcut folder". Gears databases are NEVER shipped in the base zip
# (they'd carry personal data: trakt cache, watched history), so a fresh
# box has no such folder and the widget renders empty ("Add Content...").
# Seed it once per marker version; a user who later edits or deletes the
# folder isn't fought.
_GEARS_NET_ICON = 'special://home/addons/plugin.video.gears/resources/media/network_icons/%s.png'
GEARS_NETWORKS_FOLDER = ('SELECTED NETWROKS', [
    {'mode': 'build_tvshow_list', 'action': 'tmdb_tv_networks', 'key_id': '213',  'name': 'Netflix',    'iconImage': _GEARS_NET_ICON % 'jI5c3bw', 'full_list': 'false'},
    {'mode': 'build_tvshow_list', 'action': 'tmdb_tv_networks', 'key_id': '1024', 'name': 'Amazon',     'iconImage': _GEARS_NET_ICON % 'ru9DDlL', 'full_list': 'false'},
    {'mode': 'build_tvshow_list', 'action': 'tmdb_tv_networks', 'key_id': '2552', 'name': 'Apple TV +', 'iconImage': _GEARS_NET_ICON % 'fAQMVNp', 'full_list': 'false'},
    {'mode': 'build_tvshow_list', 'action': 'tmdb_tv_networks', 'key_id': '2739', 'name': 'Disney+',    'iconImage': _GEARS_NET_ICON % 'DVrPgbM', 'full_list': 'false'},
    {'mode': 'build_tvshow_list', 'action': 'tmdb_tv_networks', 'key_id': '49',   'name': 'HBO',        'iconImage': _GEARS_NET_ICON % 'Hyu8ZGq', 'full_list': 'false'},
    {'mode': 'build_tvshow_list', 'action': 'tmdb_tv_networks', 'key_id': '3186', 'name': 'HBO Max',    'iconImage': _GEARS_NET_ICON % 'mmRMG75', 'full_list': 'false'},
    {'mode': 'build_tvshow_list', 'action': 'tmdb_tv_networks', 'key_id': '453',  'name': 'Hulu',       'iconImage': _GEARS_NET_ICON % 'uSD2Cdw', 'full_list': 'false'},
    {'mode': 'build_tvshow_list', 'action': 'tmdb_tv_networks', 'key_id': '4330', 'name': 'Paramount+', 'iconImage': _GEARS_NET_ICON % 'dmUjWmU', 'full_list': 'false'},
    {'mode': 'build_tvshow_list', 'action': 'tmdb_tv_networks', 'key_id': '19',   'name': 'FOX',        'iconImage': _GEARS_NET_ICON % '6vc0Iov', 'full_list': 'false'},
    {'mode': 'build_tvshow_list', 'action': 'tmdb_tv_networks', 'key_id': '71',   'name': 'The CW',     'iconImage': _GEARS_NET_ICON % 'Q8tooeM', 'full_list': 'false'},
    {'mode': 'build_tvshow_list', 'action': 'tmdb_tv_networks', 'key_id': '2',    'name': 'ABC',        'iconImage': _GEARS_NET_ICON % 'qePLxos', 'full_list': 'false'},
])

# The TorBox Services shortcut folder (חיבור שירותים widget) - the generic
# auth-flow entry points, NO tokens. Same seed pattern as the networks folder.
_GEARS_TB_ICON = 'special://home/addons/plugin.video.gears/resources/media/icons/%s.png'
GEARS_TORBOX_FOLDER = ('TorBox Services', [
    {'mode': 'torbox.authenticate',           'name': '[B]התחבר ל-TorBox[/B]',   'iconImage': _GEARS_TB_ICON % 'mk_tb_connect',    'isFolder': 'false'},
    {'mode': 'torbox.tb_account_info',        'name': '[B]פרטי מנוי TorBox[/B]', 'iconImage': _GEARS_TB_ICON % 'mk_tb_info',       'isFolder': 'false'},
    {'mode': 'torbox.revoke_authentication',  'name': '[B]התנתק מ-TorBox[/B]',   'iconImage': _GEARS_TB_ICON % 'mk_tb_disconnect', 'isFolder': 'false'},
])

# All shortcut folders the build seeds into Gears' navigator.db.
GEARS_SEED_FOLDERS = [GEARS_NETWORKS_FOLDER, GEARS_TORBOX_FOLDER]


def seed_gears_shortcut_folder():
    """Insert the build's Gears shortcut folders (SELECTED NETWROKS +
    TorBox Services) into Gears' navigator.db if absent. These back the
    רשתות סטרימינג + חיבור שירותים home widgets, which render empty on a
    fresh box (Gears databases are never shipped). Each folder is seeded
    only if absent, so a user who edits one isn't fought. Bump the marker
    _v suffix to re-seed the fleet."""
    marker = os.path.join(ADDON_DATA, 'gears_networks_seed_v2')
    if os.path.isfile(marker):
        return
    try:
        import sqlite3
        dbdir = xbmcvfs.translatePath(
            'special://profile/addon_data/plugin.video.gears/databases/')
        os.makedirs(dbdir, exist_ok=True)
        con = sqlite3.connect(os.path.join(dbdir, 'navigator.db'))
        # same schema gears itself creates (navigator_cache.py)
        con.execute('CREATE TABLE IF NOT EXISTS navigator '
                    '(list_name text, list_type text, list_contents text, '
                    'unique (list_name, list_type))')
        for name, items in GEARS_SEED_FOLDERS:
            have = con.execute(
                'SELECT 1 FROM navigator WHERE list_name = ? AND list_type = ?',
                (name, 'shortcut_folder')).fetchone()
            if not have:
                # gears reads list_contents with eval() -> store a python repr
                con.execute('INSERT INTO navigator VALUES (?, ?, ?)',
                            (name, 'shortcut_folder', repr(items)))
                log('seeded gears shortcut folder: %s (%d items)' % (name, len(items)))
        con.commit()
        con.close()
        os.makedirs(ADDON_DATA, exist_ok=True)
        with open(marker, 'w', encoding='utf-8') as fh:
            fh.write('1')
    except Exception as e:
        log('gears shortcut-folder seed failed: %s' % e, xbmc.LOGWARNING)


# --------------------------------------------------------------------------- #
# diff
# --------------------------------------------------------------------------- #
def _addon_requires(aid):
    """<import addon=..> ids from an INSTALLED addon's addon.xml (on disk)."""
    try:
        p = os.path.join(ADDONS_PATH, aid, 'addon.xml')
        if not os.path.isfile(p):
            return []
        t = open(p, 'r', encoding='utf-8', errors='replace').read()
        t = re.sub(r'<!--.*?-->', '', t, flags=re.S)   # ignore commented-out imports
        return re.findall(r'<import\s+addon="([^"]+)"', t)
    except Exception:
        return []


def _missing_deps_of_installed(manifest):
    """GENERAL dependency self-heal: for every INSTALLED addon, any dependency
    that IS in our manifest but is NOT installed must be (re)installed. The wizard
    installs by extracting zips, which BYPASSES Kodi's own dependency resolution --
    so a manifest dep can silently go missing (e.g. TMDbHelper without
    script.module.jurialmunkey -> crash on every startup). Binary deps that we
    deliberately don't ship (script.module.pil) aren't in the manifest, so they
    are correctly left to the per-platform base build."""
    manifest_ids = {a.get('id') for a in manifest.get('addons', [])}
    needed = set()
    try:
        for a in manifest.get('addons', []):
            aid = a.get('id')
            if not aid or _installed_version(aid) is None:
                continue
            for dep in _addon_requires(aid):
                if dep in manifest_ids and dep not in NEVER_TOUCH \
                        and _installed_version(dep) is None:
                    needed.add(dep)
    except Exception:
        pass
    return needed


def _jsonrpc(method, params):
    try:
        req = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})
        return json.loads(xbmc.executeJSONRPC(req))
    except Exception:
        return {}


def _is_enabled(aid):
    """True if Kodi has the addon ENABLED. A disabled xbmc.python.module is not
    added to the Python path, so importing it fails even though its files exist."""
    r = _jsonrpc('Addons.GetAddonDetails', {'addonid': aid, 'properties': ['enabled']})
    return bool(r.get('result', {}).get('addon', {}).get('enabled'))


def _enable_addon(aid):
    _jsonrpc('Addons.SetAddonEnabled', {'addonid': aid, 'enabled': True})


def repair_disabled_deps(manifest):
    """Re-enable any manifest dependency that Kodi has DISABLED while an addon
    requiring it is still installed. Kodi's orphan-dependency cleanup disables a
    module when no addon it *knows about* depends on it -- but our parents
    (TMDbHelper, skins) are zip-installed, so Kodi's dependency graph never learns
    they need e.g. script.module.jurialmunkey. Removing a skin then disables it,
    and TMDbHelper crashes forever with 'No module named jurialmunkey' even though
    the files are on disk. Re-extracting can't fix this (the disabled flag lives in
    Addons33.db, not on disk); only SetAddonEnabled clears it. We also restore the
    files first, in case the module was partially removed too."""
    by_id = {a.get('id'): a for a in manifest.get('addons', [])}
    manifest_ids = set(by_id)
    targets = set()
    try:
        for a in manifest.get('addons', []):
            aid = a.get('id')
            if not aid or aid in NEVER_TOUCH or _installed_version(aid) is None:
                continue
            for dep in _addon_requires(aid):
                if dep in manifest_ids and dep not in NEVER_TOUCH \
                        and _installed_version(dep) is not None \
                        and not _is_enabled(dep):
                    targets.add(dep)
    except Exception as e:
        log('disabled-dep scan error: %s' % e, xbmc.LOGERROR)
        return []

    fixed = []
    for dep in sorted(targets):
        try:
            if dep in by_id:
                _apply_one(by_id[dep])          # restore files (harmless if intact)
        except Exception as e:
            log('reinstall of disabled dep %s failed: %s' % (dep, e), xbmc.LOGERROR)
        _enable_addon(dep)
        if _is_enabled(dep):
            fixed.append(dep)
            log('re-enabled disabled dependency: %s' % dep)
    return fixed


def compute_updates(manifest, force=False):
    """Return the list of addon entries that need (re)installing.

    force=True (repair / resync) returns EVERY manifest addon that applies to
    this device -- regardless of version/sha -- so a broken or partial build is
    fully reinstalled. Optional skins the user never installed are still skipped,
    and NEVER_TOUCH ids are still left alone."""
    state = _load_state()
    by_id = {a.get('id'): a for a in manifest.get('addons', [])}

    # Repair: any manifest dep that is missing while an addon requiring it IS
    # installed (the zip-install bypasses Kodi's own dependency resolution).
    needed = _missing_deps_of_installed(manifest)

    updates = []
    for a in manifest.get('addons', []):
        aid = a.get('id')
        if not aid or aid in NEVER_TOUCH:
            continue
        installed = _installed_version(aid)
        if a.get('channel') == 'optional' and installed is None and aid not in needed:
            continue  # don't force-install heavy optional skins (but DO repair a
                      # missing companion dep of an installed parent)
        if force:
            updates.append(a)                       # repair -> reinstall all
            continue
        if installed is None:
            updates.append(a)                       # missing core addon (or needed dep) -> install
        elif version_newer(a['version'], installed):
            updates.append(a)                       # manifest is newer -> upgrade
        elif a['version'] == installed and state.get(aid) != a['sha256']:
            updates.append(a)                       # same version, content changed (e.g. Hebrew overlay)
        # else: installed is same-or-newer than manifest -> never downgrade
    return updates


# --------------------------------------------------------------------------- #
# apply
# --------------------------------------------------------------------------- #
# Addons WE ship MODDED -- an upstream/official-repo copy would overwrite our
# work (Hebrew skins, patched gears, PIL-stripped skinhelper). These must never
# be auto-updated by Kodi; the wizard manifest is their only updater. Everything
# else we ship (themoviedb.helper, skinvariables, resource.images.*, the script
# modules, metadata scrapers, ...) is VANILLA upstream and SHOULD keep
# auto-updating from its repo -- so it is deliberately absent here. The wizard
# and repo self-update from our own repo, so they're excluded too.
MODDED_ADDONS = {
    'plugin.video.gears',
    'skin.estuary',            # in Kodi's official repo -> real clobber risk
    'skin.nimbus',             # in Kodi's official repo -> real clobber risk
    'skin.arctic.fuse.3',
    'skin.arctic.zephyr.2.resurrection.mod',
    'script.skinhelper',       # we removed its PIL requirement
    'script.module.gearsscrapers',
    'service.subtitles.gearsai',
    'service.masterkodi.skipintro',
    'service.kodi.il.firstrun',
}


def _disable_kodi_autoupdate(aid):
    """Pin a MODDED addon so Kodi never auto-updates it -- WE are its only updater.

    Global auto-update is ON. Our modded addons install by extraction (origin='')
    so Kodi normally leaves them alone, but estuary/nimbus/gears are also provided
    by repos that are always installed (official Kodi repo / chainsrepo), so Kodi
    COULD replace our modded copy with a vanilla upstream release and wipe the
    work. Writing USER_DISABLED_AUTO_UPDATE (=1) into Kodi's update_rules table is
    exactly the per-addon "disable auto-update" toggle from the Kodi UI. Only
    applied to MODDED_ADDONS -- vanilla deps are left to auto-update normally.
    Fail-open: any DB/schema problem is ignored.
    """
    if aid not in MODDED_ADDONS:
        return
    import sqlite3
    try:
        dbdir = xbmcvfs.translatePath('special://database/')
        for f in os.listdir(dbdir):
            if not (f.startswith('Addons') and f.endswith('.db')):
                continue
            try:
                c = sqlite3.connect(os.path.join(dbdir, f))
                c.execute('DELETE FROM update_rules WHERE addonID=?', (aid,))
                c.execute('INSERT INTO update_rules (addonID, updateRule) VALUES (?, 1)', (aid,))
                c.commit(); c.close()
            except Exception:
                pass
    except Exception:
        pass


def _pin_all_modded_once(state):
    """One-time retrofit: pin every already-installed modded addon.

    firstrun pins these on a fresh install, and _apply_one pins on update -- but
    an EXISTING install that booted before this feature shipped would otherwise
    leave estuary/nimbus/gears unpinned until they next change. Run once (guarded
    by a state flag) so existing boxes get protected on the first update cycle
    after the wizard reaches the version that added this.
    """
    if state.get('__pinned_v1__'):
        return
    for aid in MODDED_ADDONS:
        try:
            if _installed_version(aid) is not None:
                _disable_kodi_autoupdate(aid)
        except Exception:
            pass
    state['__pinned_v1__'] = True


def _apply_one(entry):
    """Download, verify sha256, extract. Raises on any mismatch/failure."""
    data = _download(entry['url'])
    got = hashlib.sha256(data).hexdigest()
    if got != entry['sha256']:
        raise Exception('sha256 mismatch for %s (%s != %s)' % (entry['id'], got[:12], entry['sha256'][:12]))
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        bad = z.testzip()
        if bad is not None:
            raise Exception('corrupt zip for %s (%s)' % (entry['id'], bad))
        z.extractall(ADDONS_PATH)
    # If this is one of OUR modded addons, stop Kodi auto-replacing it from a
    # repo (no-op for vanilla deps, which keep auto-updating normally).
    _disable_kodi_autoupdate(entry['id'])
    if entry['id'] == 'plugin.video.gears':
        # Kodi's texture cache keys images by URL; replaced media files with
        # unchanged filenames (e.g. network_icons logo refresh) would keep
        # showing the OLD cached art forever. Purge those entries so the new
        # icons render after this update.
        _purge_texture_cache('%network_icons%')
    return True


def _purge_texture_cache(like_pattern):
    """Delete matching rows from Textures*.db + their cached thumb files, so
    Kodi re-caches the images from disk. Fail-open."""
    try:
        import sqlite3
        dbdir = xbmcvfs.translatePath('special://database/')
        thumbs = xbmcvfs.translatePath('special://thumbnails/')
        purged = 0
        for f in os.listdir(dbdir):
            if not (f.startswith('Textures') and f.endswith('.db')):
                continue
            con = sqlite3.connect(os.path.join(dbdir, f))
            try:
                rows = list(con.execute(
                    'SELECT id, cachedurl FROM texture WHERE url LIKE ?', (like_pattern,)))
                for tid, cached in rows:
                    try:
                        os.remove(os.path.join(thumbs, cached.replace('/', os.sep)))
                    except Exception:
                        pass
                    con.execute('DELETE FROM sizes WHERE idtexture=?', (tid,))
                    con.execute('DELETE FROM texture WHERE id=?', (tid,))
                    purged += 1
                con.commit()
            finally:
                con.close()
        if purged:
            log('purged %d stale texture-cache entries (%s)' % (purged, like_pattern))
    except Exception as e:
        log('texture cache purge failed (non-fatal): %s' % e)


# never uninstall these even if a glitched manifest omits them
NEVER_REMOVE = {
    'plugin.program.masterkodi.il.wizard', 'service.kodi.il.firstrun',
    'repository.masterkodi.il', 'xbmc.python',
}


def _apply_removals(manifest, state):
    """Uninstall addons WE previously installed that are no longer in the build.

    Only touches ids present in our state (so user-installed addons are never
    removed), that are absent from the current manifest, and not protected.
    Deletes the folder + its Addons33.db rows and drops it from state.
    """
    manifest_ids = {a['id'] for a in manifest.get('addons', [])}
    tracked = [k for k in list(state.keys()) if not k.startswith('__')]
    # SANITY GATE: a manifest is trusted for REMOVALS only if it looks like a
    # real build manifest. A truncated-but-valid-JSON publish (half-written
    # addons array, missing config) must never strip the fleet -- installs are
    # additive and safe, removals are not. Real removals arrive one or two at
    # a time; a burst is a glitch, not a decision.
    would_remove = [aid for aid in tracked
                    if aid not in manifest_ids and aid not in NEVER_REMOVE]
    if would_remove:
        if len(manifest_ids) < 30 or not manifest.get('config'):
            log('removals SKIPPED: manifest looks implausible (%d addons, config=%s)'
                % (len(manifest_ids), bool(manifest.get('config'))), xbmc.LOGWARNING)
            return []
        if len(would_remove) > 5:
            log('removals SKIPPED: %d tracked addons vanished at once (%s...) -- '
                'refusing mass removal' % (len(would_remove), would_remove[:5]),
                xbmc.LOGWARNING)
            return []
    removed = []
    for aid in tracked:
        if aid in manifest_ids or aid in NEVER_REMOVE:
            continue
        folder = os.path.join(ADDONS_PATH, aid)
        try:
            if os.path.isdir(folder):
                import shutil
                shutil.rmtree(folder, ignore_errors=True)
            _db_remove_addon(aid)
            state.pop(aid, None)
            removed.append(aid)
            log('removed addon (dropped from build): %s' % aid)
        except Exception as e:
            log('failed to remove %s: %s' % (aid, e), xbmc.LOGWARNING)
    return removed


# Legacy third-party repos left behind by OLD builds users migrated from.
# All are broken on Kodi 21 (pre-<dir> schema / no 21.3 directory) so they can't
# serve addons -- but Kodi still scans them every boot/update pass, spawning
# python invokers and log spam at the worst moments (seen in the Android crash
# window). Exact ids only; anything not listed is never touched.
JUNK_REPOS = (
    'repository.burekasKodi',
    'repository.funstersplace',      # also plain-HTTP = insecure
    'repository.jenrepo',
    'repository.universalscrapers',
)


def remove_junk_repos():
    """Remove known-dead legacy repos (folder + DB rows). Idempotent, runs every
    update check; returns the list of ids actually removed this pass."""
    removed = []
    for aid in JUNK_REPOS:
        folder = os.path.join(ADDONS_PATH, aid)
        present = os.path.isdir(folder)
        if present:
            import shutil
            shutil.rmtree(folder, ignore_errors=True)
        # DB rows can outlive the folder (or exist for a packaged install) --
        # always clear them, folder or not (a folderless DB row still shows the
        # junk repo in Kodi's addon list).
        _db_remove_addon(aid)
        if present:
            removed.append(aid)
            log('removed junk legacy repo: %s' % aid)
    return removed


def _db_remove_addon(aid):
    import sqlite3
    dbdir = xbmcvfs.translatePath('special://database/')
    try:
        for f in os.listdir(dbdir):
            if f.startswith('Addons') and f.endswith('.db'):
                c = sqlite3.connect(os.path.join(dbdir, f))
                for t in ('installed', 'addons', 'repo'):
                    try: c.execute('DELETE FROM %s WHERE addonID=?' % t, (aid,))
                    except Exception: pass
                c.commit(); c.close()
    except Exception:
        pass


def _active_skin():
    """Addon id of the skin Kodi is currently running, or None."""
    try:
        return xbmc.getSkinDir() or None
    except Exception:
        return None


def _read_text(path):
    try:
        return open(path, 'r', encoding='utf-8', errors='replace').read()
    except Exception:
        return ''


def _write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(text)


def _menu_is_broken(inc_path, good_inc_path):
    """The skinshortcuts home menu is broken if:
      - the generated includes file is missing, OR
      - it exists but is far smaller than our known-good copy (an empty on-device
        build writes a stub that still *names* the includes but has no items -- so
        a string check is unreliable; size is not), OR
      - the userdata menu SOURCE (mainmenu.DATA.xml) has no <shortcut> entries,
        which is the actual root cause: buildxml keeps regenerating empty from it."""
    if not os.path.isfile(inc_path):
        return True
    try:
        if os.path.isfile(good_inc_path) and \
                os.path.getsize(inc_path) < os.path.getsize(good_inc_path) * 0.5:
            return True
    except Exception:
        pass
    src = xbmcvfs.translatePath(
        'special://profile/addon_data/script.skinshortcuts/mainmenu.DATA.xml')
    try:
        if not os.path.isfile(src):
            return True
        if '<shortcut>' not in open(src, 'r', encoding='utf-8', errors='replace').read():
            return True
    except Exception:
        return True
    return False


def repair_skin_menu(no_reload=False):
    """Restore a known-good script.skinshortcuts home menu when the active skin's
    generated menu is missing OR empty. Arctic Zephyr uses classic skinshortcuts,
    which builds <res>/script-skinshortcuts-includes.xml from menu DATA in
    userdata. On a fresh wizard install that on-device build caches an EMPTY menu
    (its userdata DATA was never seeded), so the home shows category labels with
    no items and dead navigation ('Control 301 ... asked to focus, but it can't').
    buildxml then keeps regenerating empty from the poisoned cache.

    Rather than depend on that build, we SHIP a known-good menu at
    resources/menu_defaults/<skin>/ and lay it down: the skinshortcuts userdata
    (the menu source, so any later rebuild reproduces it) plus the generated
    includes into the skin. We re-lay when the on-device menu is broken/empty OR
    when the bundle VERSION changed (so a cleaned-up menu replaces an older dirty
    one already on the box). A matching, healthy menu is left untouched."""
    restored = []
    if KODI_MAJOR >= 22:
        # Piers: menu/widget defaults live in the skin itself (skinshortcuts v3
        # shortcuts/menus.xml); the bundled OLD-format DATA would poison the v3
        # userdata. Never relay it there.
        return restored
    try:
        skin = _active_skin()
        if not skin:
            return restored
        bundle = os.path.join(ADDON_PATH, 'resources', 'menu_defaults', skin)
        inc_src = os.path.join(bundle, 'includes')
        ss_src = os.path.join(bundle, 'skinshortcuts')
        if not os.path.isdir(inc_src):
            return restored                     # no bundled menu for this skin
        sdir = os.path.join(ADDONS_PATH, skin)
        res = None
        for d in sorted(os.listdir(sdir)):
            if os.path.isfile(os.path.join(sdir, d, 'Home.xml')):
                res = d
                break
        if not res:
            return restored
        inc_disk = os.path.join(sdir, res, 'script-skinshortcuts-includes.xml')
        good_inc = os.path.join(inc_src, 'script-skinshortcuts-includes.xml')
        bver = _read_text(os.path.join(bundle, 'VERSION')).strip()
        marker = os.path.join(ADDON_DATA, 'menu_ver_%s.txt' % skin)
        applied = _read_text(marker).strip()
        broken = _menu_is_broken(inc_disk, good_inc)
        stale = bool(bver) and applied != bver
        if not broken and not stale:
            return restored                     # menu already good AND current
        log('re-laying %s home menu (broken=%s stale=%s bundle_ver=%s)'
            % (skin, broken, stale, bver))
        ss_dst = xbmcvfs.translatePath('special://profile/addon_data/script.skinshortcuts/')
        # 1) replace the skinshortcuts userdata cleanly: remove the box's stale
        #    menu state (so old orphan/dup .DATA files don't linger), then copy ours
        try:
            os.makedirs(ss_dst, exist_ok=True)
            for f in os.listdir(ss_dst):
                low = f.lower()
                if low.endswith('.data.xml') or '.bak' in low \
                        or f.startswith(skin):          # <skin>.hash / <skin>.properties
                    try: os.remove(os.path.join(ss_dst, f))
                    except Exception: pass
            for f in os.listdir(ss_src):
                shutil.copy2(os.path.join(ss_src, f), os.path.join(ss_dst, f))
            restored.append('skinshortcuts-data')
        except Exception as e:
            log('menu userdata restore failed: %s' % e, xbmc.LOGERROR)
        # 2) generated includes into the skin
        try:
            for f in os.listdir(inc_src):
                shutil.copy2(os.path.join(inc_src, f), os.path.join(sdir, res, f))
            restored.append('skin-includes')
        except Exception as e:
            log('menu includes restore failed: %s' % e, xbmc.LOGERROR)
        if restored:
            try: _write_text(marker, bver)
            except Exception: pass
            if not no_reload:
                xbmc.sleep(500)
                xbmc.executebuiltin('ReloadSkin()')
    except Exception as e:
        log('skin-menu repair error: %s' % e, xbmc.LOGERROR)
    return restored


class _Progress(object):
    """Unified progress UI. Service/silent runs get a NON-modal background bar
    (Kodi stays usable, but the user SEES the update happening); interactive
    runs get a modal cancelable dialog."""
    def __init__(self, silent):
        self.bg = None
        self.dp = None
        try:
            if silent:
                self.bg = xbmcgui.DialogProgressBG()
                self.bg.create('MasterKodi IL', 'מוריד עדכונים...')
            else:
                self.dp = xbmcgui.DialogProgress()
                self.dp.create('MasterKodi IL', 'מוריד עדכונים...')
        except Exception:
            pass

    def update(self, pct, msg):
        try:
            if self.bg:
                self.bg.update(pct, 'MasterKodi IL', msg)
            elif self.dp:
                self.dp.update(pct, msg)
        except Exception:
            pass

    def iscanceled(self):
        try:
            return bool(self.dp and self.dp.iscanceled())
        except Exception:
            return False

    def close(self):
        try:
            if self.bg:
                self.bg.close()
            elif self.dp:
                self.dp.close()
        except Exception:
            pass


def _restart_kodi():
    """Full app restart (needed to reload the wizard's own code).

    Windows: RestartApp rides Kodi's native shutdown, which waits 5s PER python
    service that doesn't stop -- the countdown ends and the window then hangs
    frozen for 15-25s (or forever). Instead: spawn a detached relauncher that
    waits for THIS PID to die (force-killing it if the teardown hangs past the
    grace), then starts kodi.exe fresh; trigger the normal Quit so the settings
    save + DB vacuum land; hard-exit after 3.5s. Relaunch is guaranteed even if
    the teardown wedges. Other platforms keep RestartApp (Android's lifecycle
    is managed by the OS)."""
    import sys
    if sys.platform.startswith('win'):
        try:
            import subprocess, time
            pid = os.getpid()
            exe = sys.executable if str(sys.executable).lower().endswith('kodi.exe') \
                else os.path.join(xbmcvfs.translatePath('special://xbmc/'), 'kodi.exe')
            # Relaunch with the ORIGINAL command line -- the portable install
            # runs `kodi.exe -p`; relaunching the bare exe started an EMPTY
            # default-profile Kodi (%APPDATA%) instead of portable_data.
            launch = None
            try:
                import ctypes
                ctypes.windll.kernel32.GetCommandLineW.restype = ctypes.c_wchar_p
                launch = (ctypes.windll.kernel32.GetCommandLineW() or '').strip()
            except Exception:
                pass
            if not launch or 'kodi' not in launch.lower():
                # fallback: rebuild it -- add -p when running in portable mode
                # (special://home lives under the app dir in portable installs)
                portable = xbmcvfs.translatePath('special://home/').lower().startswith(
                    xbmcvfs.translatePath('special://xbmc/').lower())
                launch = '"%s"%s' % (exe, ' -p' if portable else '')
            if os.path.isfile(exe):
                # relauncher: ~8s grace for a clean death; force-kill ONLY if
                # that PID is still kodi.exe (PID-reuse guard); then relaunch
                cmd = ('ping -n 9 127.0.0.1 >nul & '
                       'tasklist /FI "PID eq %d" /FI "IMAGENAME eq kodi.exe" 2>nul | '
                       'findstr /I kodi.exe >nul && taskkill /F /PID %d /T >nul 2>&1 & '
                       'start "" %s' % (pid, pid, launch))
                # CREATE_NO_WINDOW alone: a hidden console the whole chain
                # (ping/tasklist/findstr) inherits. DETACHED_PROCESS must NOT be
                # combined with it -- detached cmd has no console, so each child
                # allocated its own VISIBLE console window on the desktop.
                subprocess.Popen(cmd, shell=True, creationflags=0x08000000)
                log('fast restart: relauncher armed (pid %d -> %s)' % (pid, exe))
                xbmc.executebuiltin('Quit')
                time.sleep(3.5)
                os._exit(0)
        except Exception as e:
            log('fast restart failed, falling back to RestartApp: %s' % e, xbmc.LOGWARNING)
    try:
        xbmc.executebuiltin('RestartApp')
        return
    except Exception:
        pass
    try:
        xbmc.restart()
    except Exception:
        pass


def _finalize_reload(summary):
    """Auto reload/restart when an applied update needs it -- no confirmation.

      * active skin updated  -> ReloadSkin() (seamless, visible).
      * wizard itself updated -> a running script can't reload itself, so we
        show a short visible countdown and restart Kodi.

    Never interrupts playback: if something is playing we just notify and let the
    update take effect on the next launch (it's already on disk)."""
    need_restart = summary.get('wizard_changed')
    need_skin = summary.get('skin_changed')
    # A config change can rewrite skin files (e.g. skinvariables nodes, home menu),
    # which the running skin won't show until it reloads.
    need_config_reload = summary.get('config_applied')
    if not (need_restart or need_skin or need_config_reload):
        return
    try:
        playing = xbmc.Player().isPlaying()
    except Exception:
        playing = False
    if playing:
        xbmcgui.Dialog().notification(
            'MasterKodi IL', 'העדכון יוחל בהפעלה הבאה',
            xbmcgui.NOTIFICATION_INFO, 6000)
        return

    if need_restart:
        try:
            dlg = xbmcgui.DialogProgress()
            dlg.create('MasterKodi IL', 'העדכון הותקן. מפעיל מחדש את Kodi...')
            for s in (3, 2, 1):
                if dlg.iscanceled():
                    break
                dlg.update(int((3 - s) / 3.0 * 100), 'מפעיל מחדש בעוד %d...' % s)
                xbmc.sleep(1000)
            dlg.close()
        except Exception:
            pass
        _restart_kodi()
    elif need_skin:
        xbmcgui.Dialog().notification(
            'MasterKodi IL', 'הסקין עודכן, טוען מחדש...',
            xbmcgui.NOTIFICATION_INFO, 4000)
        xbmc.sleep(800)
        try:
            xbmc.executebuiltin('ReloadSkin()')
        except Exception:
            pass
    elif need_config_reload:
        # config-only change: NO mid-session reload (crash window). Skin-visual
        # parts are deferred to the boot-time rebuild flow; everything else is
        # already live on disk.
        pass


def run_update(silent=False, notify=None, force=False, no_reload=False):
    """Check + apply. Returns dict summary.

    notify:    optional callable(message) for user-facing status text.
    force:     repair mode -- reinstall every applicable addon + re-apply config.
    no_reload: skip the post-config ReloadSkin (used when a full app restart
               follows anyway, e.g. from the build installer -- avoids reloading
               the skin out from under the install UI).
    """
    def _say(msg):
        if notify:
            try: notify(msg)
            except Exception: pass

    # Unknown Kodi major (BuildVersion parse failed) would silently fall back
    # to the OMEGA manifest + skip every kodi_min/kodi_max gate -- on a Piers
    # box that's the wrong fleet. Do nothing this pass; the next boot resolves.
    if KODI_MAJOR == 0:
        log('run_update skipped: could not determine Kodi major version', xbmc.LOGWARNING)
        return {'ok': False, 'skipped': 'unknown kodi version',
                'applied': [], 'failed': [], 'removed': []}

    try:
        manifest = fetch_manifest()
    except Exception as e:
        log('manifest fetch failed: %s' % e, xbmc.LOGERROR)
        return {'ok': False, 'error': str(e), 'applied': [], 'failed': []}

    state = _load_state()
    # One-time: pin modded addons on existing installs (new installs get pinned
    # by firstrun; updates get pinned by _apply_one).
    _pin_all_modded_once(state)
    # Uninstall addons we previously installed that are no longer in the build
    # (e.g. DarkSubs removed) - runs even when everything else is up to date.
    removed = _apply_removals(manifest, state)
    # Purge broken legacy repos old builds left behind (they can't serve addons
    # on 21 but Kodi scans them every pass -- python-invoker noise + insecure).
    junk = remove_junk_repos()
    if removed or junk:
        _save_state(state)
        xbmc.executebuiltin('UpdateLocalAddons')

    # Re-enable any dependency Kodi disabled out from under an installed parent
    # (e.g. jurialmunkey disabled after a skin removal -> TMDbHelper crashes).
    # Runs every check, independent of version updates.
    enabled = repair_disabled_deps(manifest)
    if enabled:
        xbmc.executebuiltin('UpdateLocalAddons')

    updates = compute_updates(manifest, force=force)
    if not updates:
        if not removed and not enabled:
            _say('הבילד מעודכן')
        # still apply config on a version bump even if no addon changed
        cfg_bumped = _config_version_changed(manifest, state)
        cfg_applied = _maybe_apply_config(manifest, state, force=force)
        _save_state(state)
        # self-heal an empty skinshortcuts home menu (nothing was re-extracted
        # here, so a missing includes file means the boot build never landed)
        menu_repaired = repair_skin_menu(no_reload=no_reload)
        # NOTE (2026-07-17): the old post-config ReloadSkin here was REMOVED --
        # a mid-session reload is the CPythonInvoker crash window (it killed the
        # Xiaomi mid-stop tonight the moment config 29 landed). Skin-visual
        # changes are now deferred+stashed by _maybe_apply_config and applied by
        # the boot-time pending_view_rebuild flow with ONE safe reload.
        return {'ok': True, 'applied': [], 'failed': [], 'removed': removed,
                'enabled': enabled, 'menu_repaired': menu_repaired,
                'config_applied': cfg_applied,
                'up_to_date': not removed and not enabled and not menu_repaired,
                'manifest_generated': manifest.get('generated_utc')}

    applied, failed = [], []
    wizard_changed = False
    skin_changed = False
    active_skin = _active_skin()

    dp = _Progress(silent)

    total = len(updates)
    for i, entry in enumerate(updates):
        if dp.iscanceled():
            break
        pct = int((i / float(total)) * 100)
        aid = entry['id']
        dp.update(pct, 'מעדכן: %s (%d/%d)' % (aid, i + 1, total))
        try:
            _apply_one(entry)
            state[aid] = entry['sha256']
            applied.append(aid)
            if aid == ADDON_ID:
                wizard_changed = True
            if active_skin and aid == active_skin:
                skin_changed = True
        except Exception as e:
            log('update failed for %s: %s' % (aid, e), xbmc.LOGERROR)
            failed.append(aid)

    _save_state(state)
    dp.update(100, 'מרענן רשימת תוספים...')
    xbmc.executebuiltin('UpdateLocalAddons')
    xbmc.sleep(500)
    dp.close()

    # config payload (default userdata) - applied on version bump only
    cfg_bumped = _config_version_changed(manifest, state)
    cfg_applied = _maybe_apply_config(manifest, state, force=force)
    _save_state(state)

    # self-heal an empty skinshortcuts home menu. Runs AFTER any skin re-extract
    # above, so the rebuilt includes are not clobbered.
    menu_repaired = repair_skin_menu(no_reload=no_reload)
    # NOTE (2026-07-17): post-config ReloadSkin removed here too (crash window;
    # see the note in the no-updates path above). Deferred to boot.

    summary = {
        'ok': not failed,
        'applied': applied,
        'enabled': enabled,
        'menu_repaired': menu_repaired,
        'failed': failed,
        'wizard_changed': wizard_changed,
        'skin_changed': skin_changed,
        'active_skin': active_skin,
        'config_applied': cfg_applied,
        'up_to_date': False,
        'manifest_generated': manifest.get('generated_utc'),
    }
    log('update summary: %s' % summary)
    return summary


def _config_version_changed(manifest, state):
    """True if the manifest's config version differs from the one last applied on
    this device. Lets callers reload the skin only on a real config bump, not on
    every wizard update (config also re-applies when the wizard version changes)."""
    cfg = manifest.get('config') or {}
    return state.get('__config__') != ('config:%s' % cfg.get('version'))


def _maybe_apply_config(manifest, state, force=False):
    """Apply the shipped build-config using config_policy.json (if present).

    Instead of blindly extracting the whole config zip over userdata/ (which
    clobbers the user's own settings and logins), a declarative policy decides
    per file HOW to apply it: replace / seed_if_absent / merge_id (per <setting
    id> - build value wins, other user settings kept) / merge_name (per
    <source><name>). exclude_ids protect every credential so a config update
    never wipes Real-Debrid/TorBox/Trakt/Gemini logins.

    Our extension over the upstream idea: a `gears_settings` block enforces
    critical Gears values that live in a binary settings.db (not an XML) - this
    is what makes a settings change (e.g. faster search defaults) actually reach
    an existing device, and fixes a fresh install seeding stale values.
    """
    cfg = manifest.get('config')
    if not cfg:
        return False
    key = 'config:%s' % cfg['version']
    fresh = '__config__' not in state          # first ever config apply on this device
    # Re-apply the config whenever the WIZARD version changed, even if the config
    # version didn't: config is applied by the currently-running wizard code, so a
    # config feature added in a new wizard (e.g. gears_shortcuts) would otherwise
    # never run on a device where the config was already applied by the old wizard.
    try:
        wiz_ver = ADDON.getAddonInfo('version')
    except Exception:
        wiz_ver = ''
    already = state.get('__config__') == key and state.get('__config_wizard__') == wiz_ver
    if already and not force:
        return False                           # already applied by THIS wizard version
    try:
        data = _download(cfg['url'])
        if hashlib.sha256(data).hexdigest() != cfg['sha256']:
            log('config sha mismatch, skipping', xbmc.LOGWARNING)
            return False
    except Exception as e:
        log('config download failed: %s' % e, xbmc.LOGWARNING)
        return False

    home = xbmcvfs.translatePath('special://home/')
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = z.namelist()
            policy = None
            if 'config_policy.json' in names:
                try:
                    policy = json.loads(z.read('config_policy.json').decode('utf-8-sig'))
                except Exception as e:
                    log('bad config_policy.json: %s' % e, xbmc.LOGWARNING)
            if policy:
                _apply_policy(z, policy, home, fresh)
            else:
                z.extractall(home)   # no policy -> legacy behaviour
                log('applied config %s (no policy, full extract)' % cfg['version'])
        state['__config__'] = key
        state['__config_wizard__'] = wiz_ver
        return True
    except Exception as e:
        log('config apply failed: %s' % e, xbmc.LOGWARNING)
        return False


def _apply_policy(zf, policy, home, fresh):
    import tempfile, shutil
    mode_key = 'fresh' if fresh else 'update'
    applied = []
    _defer = {'viewtypes': False, 'skin_settings': False}
    for entry in policy.get('files', []):
        src = entry.get('src'); dest_rel = entry.get('dest')
        if not src or not dest_rel:
            continue
        mode = entry.get(mode_key, 'replace')
        if mode in (None, '', 'skip'):
            continue
        # declarative Kodi-version gate (e.g. kodi_max: 21 for Omega-only files)
        kmin, kmax = entry.get('kodi_min'), entry.get('kodi_max')
        if KODI_MAJOR and ((kmin and KODI_MAJOR < int(kmin)) or (kmax and KODI_MAJOR > int(kmax))):
            continue
        try:
            src_bytes = zf.read(src)
        except KeyError:
            continue
        dest = os.path.join(home, dest_rel.replace('/', os.sep))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        exclude = set(entry.get('exclude_ids', []))
        prev_bytes = None
        try:
            with open(dest, 'rb') as fh:
                prev_bytes = fh.read()
        except Exception:
            pass
        if mode == 'replace':
            with open(dest, 'wb') as fh:
                fh.write(src_bytes)
        elif mode == 'replace_on_change':
            # Replace ONLY when the SHIPPED file changed since our last
            # delivery (baseline copy in addon_data). Files like the skin
            # viewtypes json hold BOTH our curated defaults and the user's
            # manual choices -- plain replace would reset user customization
            # on every config bump. With a baseline: user changes survive
            # every update, until we deliberately push a change to this
            # file -- then ours wins (whole file) and the baseline advances.
            bdir = os.path.join(ADDON_DATA, 'config_baseline')
            bpath = os.path.join(bdir, dest_rel.replace('/', '_'))
            prev_src = None
            try:
                with open(bpath, 'rb') as fh:
                    prev_src = fh.read()
            except Exception:
                pass
            if prev_src != src_bytes or not os.path.exists(dest):
                with open(dest, 'wb') as fh:
                    fh.write(src_bytes)
            try:
                os.makedirs(bdir, exist_ok=True)
                with open(bpath, 'wb') as fh:
                    fh.write(src_bytes)
            except Exception:
                pass
        elif mode == 'seed_if_absent':
            if not os.path.exists(dest):
                with open(dest, 'wb') as fh:
                    fh.write(src_bytes)
        elif mode == 'merge_id':
            _merge_settings_xml(src_bytes, dest, exclude)
        elif mode == 'merge_seed':
            _seed_settings_xml(src_bytes, dest, exclude, set(entry.get('force_ids', [])))
        elif mode == 'merge_name':
            _merge_named_xml(src_bytes, dest)
        applied.append('%s(%s)' % (dest_rel, mode))
        # --- detect skin-VISUAL config changes for deferred handling. A
        # mid-session ReloadSkin is the CPythonInvoker crash window (16 dumps,
        # lab-proven: any reload during widget/plugin activity can kill Kodi
        # 21.3), and viewtypes changes need a forced includes-rebuild the
        # normal flow never does. Both are handled at NEXT BOOT by the
        # pending_view_rebuild flow instead. ---
        if not fresh:
            try:
                cur_bytes = None
                try:
                    with open(dest, 'rb') as fh:
                        cur_bytes = fh.read()
                except Exception:
                    pass
                changed = (cur_bytes is not None and cur_bytes != prev_bytes)
                if changed and dest_rel.endswith('-viewtypes.json') \
                        and 'script.skinvariables' in dest_rel:
                    _defer['viewtypes'] = True
                askin = _active_skin()
                if changed and askin \
                        and dest_rel == 'userdata/addon_data/%s/settings.xml' % askin:
                    # Kodi's exit-save clobbers this file from memory; stash the
                    # post-merge result so the boot flow can re-apply it and do
                    # ONE safe boot-time reload.
                    sdir = os.path.join(ADDON_DATA, 'pending_skin_config')
                    os.makedirs(sdir, exist_ok=True)
                    shutil.copy2(dest, os.path.join(sdir, 'settings.xml'))
                    _write_text(os.path.join(sdir, 'target.txt'), dest)
                    _defer['skin_settings'] = True
            except Exception as e:
                log('skin-visual defer detect failed: %s' % e, xbmc.LOGWARNING)
    if (not fresh) and (_defer['viewtypes'] or _defer['skin_settings']):
        try:
            _write_text(os.path.join(ADDON_DATA, 'pending_view_rebuild'),
                        _active_skin() or '')
            # config-driven viewtypes need the hash-clear on EVERY skinvariables
            # skin (not just skinshortcuts ones) -- flag it for the boot flow
            _write_text(os.path.join(ADDON_DATA, 'pending_view_rebuild_force'), '1')
            log('deferred skin-visual config to next boot (%s)' %
                ','.join(k for k, v in _defer.items() if v))
        except Exception as e:
            log('could not arm view-rebuild marker: %s' % e, xbmc.LOGWARNING)
    # Gears settings.db enforcement (our extension)
    gs = policy.get('gears_settings')
    if gs:
        _enforce_gears_settings(home, gs, set(policy.get('gears_settings_exclude', [])))
    # Gears navigator.db shortcut-folder enforcement (delete/replace debrid folders)
    gsc = policy.get('gears_shortcuts')
    if gsc:
        _enforce_gears_shortcuts(home, gsc)
    # Kodi 21 stream-buffer settings (GUI filecache.* replaced the old
    # advancedsettings <cache> block, which Kodi now IGNORES)
    fc = policy.get('filecache')
    if fc:
        _enforce_filecache(fc)
    log('config policy applied: %d files%s%s%s' % (len(applied),
        ' + gears_settings' if gs else '', ' + gears_shortcuts' if gsc else '',
        ' + filecache' if fc else ''))


def _enforce_filecache(spec):
    """Set Kodi 21 GUI stream-buffer settings (filecache.*) via JSON-RPC.

    The stall problem this tunes for was only ever seen on desktop; low-RAM
    Android boxes are SKIPPED (spec['skip_android']) -- a bigger buffer there
    only adds memory pressure (Kodi can use ~3x memorysize at peak). Fail-open.
    """
    try:
        if spec.get('skip_android') and xbmc.getCondVisibility('System.Platform.Android'):
            log('filecache enforcement skipped on Android (by policy)')
            return
        import json as _json
        for sid in ('buffermode', 'memorysize', 'readfactor'):
            if sid not in spec:
                continue
            xbmc.executeJSONRPC(_json.dumps({
                'jsonrpc': '2.0', 'id': 1, 'method': 'Settings.SetSettingValue',
                'params': {'setting': 'filecache.%s' % sid, 'value': int(spec[sid])}}))
        log('filecache enforced: %s' % {k: spec[k] for k in
            ('buffermode', 'memorysize', 'readfactor') if k in spec})
    except Exception as e:
        log('filecache enforcement failed (non-fatal): %s' % e, xbmc.LOGWARNING)


def _merge_settings_xml(src_bytes, dest, exclude_ids):
    """Per <setting id=X>: build value wins, other user settings untouched."""
    import re
    src_txt = src_bytes.decode('utf-8', 'replace')
    build_vals = dict(re.findall(r'<setting id="([^"]+)"[^>]*>([^<]*)</setting>', src_txt))
    if not os.path.exists(dest):
        with open(dest, 'wb') as fh:
            fh.write(src_bytes)
        return
    with open(dest, 'r', encoding='utf-8', errors='replace') as fh:
        dst_txt = fh.read()
    dst_ids = set(re.findall(r'<setting id="([^"]+)"', dst_txt))
    for sid, val in build_vals.items():
        if sid in exclude_ids:
            continue
        if sid in dst_ids:
            dst_txt = re.sub(r'(<setting id="%s"[^>]*>)[^<]*(</setting>)' % re.escape(sid),
                             lambda m: m.group(1) + val + m.group(2), dst_txt, count=1)
        else:
            dst_txt = dst_txt.replace('</settings>', '    <setting id="%s">%s</setting>\n</settings>' % (sid, val), 1)
    with open(dest, 'w', encoding='utf-8') as fh:
        fh.write(dst_txt)


def _seed_settings_xml(src_bytes, dest, exclude_ids, force_ids=()):
    """Per <setting id=X>: add ONLY ids the user doesn't already have; NEVER
    overwrite an existing value -- so our values are the DEFAULT while a user's own
    change sticks across updates (unlike merge_id, which clobbers on every apply).

    ESCAPE HATCH: ids listed in `force_ids` ARE overwritten (build value wins), even
    if the user set them. That's how we deliberately PUSH a changed default to every
    device: add the id to the file's "force_ids" in config_policy.json and bump
    config_version. exclude_ids always wins over force_ids (credentials stay safe)."""
    import re
    src_txt = src_bytes.decode('utf-8', 'replace')
    build_vals = dict(re.findall(r'<setting id="([^"]+)"[^>]*>([^<]*)</setting>', src_txt))
    if not os.path.exists(dest):
        with open(dest, 'wb') as fh:
            fh.write(src_bytes)
        return
    with open(dest, 'r', encoding='utf-8', errors='replace') as fh:
        dst_txt = fh.read()
    dst_ids = set(re.findall(r'<setting id="([^"]+)"', dst_txt))
    for sid, val in build_vals.items():
        if sid in exclude_ids:
            continue
        if sid in force_ids and sid in dst_ids:
            dst_txt = re.sub(r'(<setting id="%s"[^>]*>)[^<]*(</setting>)' % re.escape(sid),
                             lambda m: m.group(1) + val + m.group(2), dst_txt, count=1)
        elif sid not in dst_ids:
            dst_txt = dst_txt.replace('</settings>',
                                      '    <setting id="%s">%s</setting>\n</settings>' % (sid, val), 1)
        # else: user already has it and it's not forced -> leave it
    with open(dest, 'w', encoding='utf-8') as fh:
        fh.write(dst_txt)


def _merge_named_xml(src_bytes, dest):
    """sources.xml style: add build's <source> entries the user doesn't have."""
    import re
    if not os.path.exists(dest):
        with open(dest, 'wb') as fh:
            fh.write(src_bytes)
        return
    src_txt = src_bytes.decode('utf-8', 'replace')
    with open(dest, 'r', encoding='utf-8', errors='replace') as fh:
        dst_txt = fh.read()
    have = set(re.findall(r'<name>([^<]+)</name>', dst_txt))
    add = [m for m in re.findall(r'<source>.*?</source>', src_txt, re.S)
           if (re.search(r'<name>([^<]+)</name>', m) or [None]) and
              re.search(r'<name>([^<]+)</name>', m).group(1) not in have]
    if add and '</video>' in dst_txt:
        dst_txt = dst_txt.replace('</video>', '\n'.join(add) + '\n    </video>', 1)
        with open(dest, 'w', encoding='utf-8') as fh:
            fh.write(dst_txt)


# Per-skin Gears view IDs. Gears' use_viewtypes forces Container.SetViewMode
# from these settings, which OVERRIDES Kodi's per-path ViewModes (that's why a
# ViewMode we set flashes then reverts). The settings are GLOBAL (one Gears
# settings.db), and view id 500 (Estuary Wall) means something different in each
# skin -- so the wizard writes the ACTIVE skin's view ids on install + skin
# switch, and Gears then forces the correct view per skin. IDs verified from each
# skin's own config/live settings (Estuary confirmed on-device by Asaf).
GEARS_SKIN_VIEWS = {
    # Estuary: movies/tvshows Poster confirmed on-device by Asaf; seasons/episodes
    # from the config ViewModes (55 = WideList); main/premium/single = 55.
    'skin.estuary': {
        'view.main': '55', 'view.movies': '51', 'view.tvshows': '51',
        'view.seasons': '55', 'view.episodes': '55',
        'view.episodes_single': '55', 'view.premium': '55'},
    # Zephyr/AF3: movies/tvshows/seasons/episodes are EXACTLY the config
    # skinvariables plugin.video.gears viewtypes; single=episodes; main/premium
    # = that skin's skinvariables 'none' (fallback) view (50 / 506).
    'skin.arctic.zephyr.2.resurrection.mod': {
        'view.main': '50', 'view.movies': '53', 'view.tvshows': '53',
        'view.seasons': '52', 'view.episodes': '529',
        'view.episodes_single': '529', 'view.premium': '50'},
    'skin.arctic.fuse.3': {
        'view.main': '506', 'view.movies': '505', 'view.tvshows': '505',
        'view.seasons': '509', 'view.episodes': '509',
        'view.episodes_single': '509', 'view.premium': '506'},
    # Nimbus: Asaf's on-device choices (its native Skin.ForcedView names,
    # mapped to view ids): movies/tvshows = Flix (54), seasons = List (50),
    # episodes = FlixScape (55). Kept in lock-step with Gears use_viewtypes
    # so the two view mechanisms reinforce instead of fight (flash-revert).
    'skin.nimbus': {
        'view.main': '50', 'view.movies': '54', 'view.tvshows': '54',
        'view.seasons': '50', 'view.episodes': '55',
        'view.episodes_single': '55', 'view.premium': '50'},
}


def apply_gears_views_for_skin(skin_id=None):
    """Write the active (or given) skin's preferred Gears view ids into Gears'
    settings.db so use_viewtypes forces the right view per skin. No-op for a skin
    we don't have a map for (Gears keeps whatever it had)."""
    import sqlite3
    skin_id = skin_id or (xbmc.getSkinDir() or '')
    views = GEARS_SKIN_VIEWS.get(skin_id)
    if not views:
        return
    db = xbmcvfs.translatePath(
        'special://profile/addon_data/plugin.video.gears/databases/settings.db')
    if not os.path.isfile(db):
        return
    try:
        c = sqlite3.connect(db)
        # use_viewtypes must be on for Gears to force the view at all
        c.execute("UPDATE settings SET setting_value='true' WHERE setting_id='use_viewtypes'")
        for sid, val in views.items():
            c.execute('UPDATE settings SET setting_value=? WHERE setting_id=?', (str(val), sid))
        c.commit()
        c.close()
        log('applied gears views for %s (%d ids)' % (skin_id, len(views)))
    except Exception as e:
        log('gears views apply failed: %s' % e, xbmc.LOGWARNING)


def _enforce_gears_settings(home, gears_settings, exclude):
    """Write critical Gears values into its settings.db without touching creds."""
    import sqlite3
    db = os.path.join(home, 'userdata', 'addon_data', 'plugin.video.gears',
                      'databases', 'settings.db')
    if not os.path.isfile(db):
        return
    try:
        c = sqlite3.connect(db)
        for sid, val in gears_settings.items():
            if sid in exclude:
                continue
            c.execute('UPDATE settings SET setting_value=? WHERE setting_id=?', (str(val), sid))
        c.commit(); c.close()
        log('enforced %d gears settings' % len(gears_settings))
    except Exception as e:
        log('gears settings enforce failed: %s' % e, xbmc.LOGWARNING)


def _enforce_gears_shortcuts(home, spec):
    """Rewrite Gears' navigator.db shortcut folders. `spec` may hold:
      delete_folders: [names]   -> remove those shortcut_folder rows
      set_folders: {name: [items]} -> INSERT OR REPLACE the folder contents
    list_contents is stored as a Python repr() of a list of dicts (Gears reads it
    with eval). Takes effect after a Kodi restart (Gears caches folders in window
    properties, cleared on restart -- which our config update triggers)."""
    import sqlite3
    db = os.path.join(home, 'userdata', 'addon_data', 'plugin.video.gears',
                      'databases', 'navigator.db')
    if not os.path.isfile(db):
        return
    try:
        c = sqlite3.connect(db)
        for name in spec.get('delete_folders', []):
            c.execute("DELETE FROM navigator WHERE list_name=? AND list_type='shortcut_folder'", (name,))
        for name, items in (spec.get('set_folders') or {}).items():
            # DELETE + INSERT (not INSERT OR REPLACE) so it's correct whether or not
            # the navigator table has a unique key on (list_name, list_type).
            c.execute("DELETE FROM navigator WHERE list_name=? AND list_type='shortcut_folder'", (name,))
            c.execute("INSERT INTO navigator VALUES (?, 'shortcut_folder', ?)", (name, repr(items)))
        c.commit(); c.close()
        log('enforced gears shortcuts (del %d, set %d)' % (
            len(spec.get('delete_folders', [])), len(spec.get('set_folders') or {})))
    except Exception as e:
        log('gears shortcuts enforce failed: %s' % e, xbmc.LOGWARNING)


# --------------------------------------------------------------------------- #
# convenience entry points for the menu / service
# --------------------------------------------------------------------------- #
def check_and_prompt():
    """Interactive: check, show what's available, apply on confirm."""
    dlg = xbmcgui.Dialog()
    try:
        manifest = fetch_manifest()
    except Exception as e:
        dlg.ok('MasterKodi IL', 'שגיאה בבדיקת עדכונים:\n%s' % e)
        return
    updates = compute_updates(manifest)
    if not updates:
        # No addon changed, but config (or a removal) still might have -- apply it.
        # Without this, a config-only update (e.g. home menu / widgets / favourites)
        # would never reach the user from the manual check.
        summary = run_update(silent=True)
        dlg.notification('MasterKodi IL', 'הבילד מעודכן', xbmcgui.NOTIFICATION_INFO, 4000)
        _finalize_reload(summary)
        return
    names = '\n'.join('- %s (%s)' % (u['id'], u['version']) for u in updates[:15])
    more = '' if len(updates) <= 15 else '\n(ועוד %d)' % (len(updates) - 15)
    if not dlg.yesno('MasterKodi IL', 'נמצאו %d עדכונים:\n%s%s\n\nלהתקין עכשיו?' % (len(updates), names, more)):
        return
    summary = run_update(silent=False)
    if summary.get('failed'):
        dlg.ok('MasterKodi IL', 'הותקנו %d, נכשלו %d:\n%s' % (
            len(summary['applied']), len(summary['failed']), ', '.join(summary['failed'])))
        return
    msg = 'עודכנו %d תוספים' % len(summary['applied'])
    if summary.get('wizard_changed'):
        msg += '\n(האשף עודכן - Kodi יופעל מחדש)'
    elif summary.get('skin_changed'):
        msg += '\n(הסקין ייטען מחדש)'
    dlg.ok('MasterKodi IL', msg)
    # auto reload/restart if needed (no further confirmation)
    _finalize_reload(summary)


def repair_build():
    """Interactive repair/resync: reinstall EVERY applicable addon from the
    manifest (regardless of version), plus re-apply the build config. Settings
    and credentials are preserved (config_policy exclude_ids). Fixes a broken,
    partial, or corrupted build."""
    dlg = xbmcgui.Dialog()
    if not dlg.yesno(
            'MasterKodi IL',
            'תיקון / רענון בילד\n\nכל תוספי הבילד יורדו ויותקנו מחדש מהמאניפסט '
            '(ההגדרות והמפתחות נשמרים).\nזה עשוי לקחת כמה דקות.\n\nלהמשיך?',
            yeslabel='תקן', nolabel='ביטול'):
        return
    summary = run_update(silent=False, force=True)
    if summary.get('error'):
        dlg.ok('MasterKodi IL', 'שגיאה: %s' % summary['error'])
        return
    if summary.get('failed'):
        dlg.ok('MasterKodi IL', 'תוקנו %d, נכשלו %d:\n%s' % (
            len(summary['applied']), len(summary['failed']), ', '.join(summary['failed'])))
    else:
        dlg.ok('MasterKodi IL', 'הבילד רוענן: %d תוספים הותקנו מחדש' % len(summary.get('applied', [])))
    _finalize_reload(summary)


def silent_check():
    """For the service: apply quietly, notify only if something changed."""
    summary = run_update(silent=True)
    if summary.get('applied'):
        xbmcgui.Dialog().notification(
            'MasterKodi IL', 'עודכנו %d תוספים' % len(summary['applied']),
            xbmcgui.NOTIFICATION_INFO, 5000)
    # auto reload/restart if the wizard, skin, OR config changed (config-only
    # updates still need a skin reload to show, and produce no notification)
    _finalize_reload(summary)
    return summary
