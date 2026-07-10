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
ADDON_DATA = xbmcvfs.translatePath('special://userdata/addon_data/%s' % ADDON_ID)
ADDONS_PATH = xbmcvfs.translatePath('special://home/addons/')
STATE_FILE = os.path.join(ADDON_DATA, 'applied_manifest.json')

MANIFEST_URL = 'https://raw.githubusercontent.com/asaf27064/MasterKodi-IL-Build/main/manifest.json'

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
# diff
# --------------------------------------------------------------------------- #
def compute_updates(manifest):
    """Return the list of addon entries that need (re)installing."""
    state = _load_state()
    updates = []
    for a in manifest.get('addons', []):
        aid = a.get('id')
        if not aid or aid in NEVER_TOUCH:
            continue
        installed = _installed_version(aid)
        if a.get('channel') == 'optional' and installed is None:
            continue  # don't force-install heavy optional skins
        if installed is None:
            updates.append(a)                       # missing core addon -> install
        elif version_newer(a['version'], installed):
            updates.append(a)                       # manifest is newer -> upgrade
        elif a['version'] == installed and state.get(aid) != a['sha256']:
            updates.append(a)                       # same version, content changed (e.g. Hebrew overlay)
        # else: installed is same-or-newer than manifest -> never downgrade
    return updates


# --------------------------------------------------------------------------- #
# apply
# --------------------------------------------------------------------------- #
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
    return True


def run_update(silent=False, notify=None):
    """Check + apply. Returns dict summary.

    notify: optional callable(message) for user-facing status text.
    """
    def _say(msg):
        if notify:
            try: notify(msg)
            except Exception: pass

    try:
        manifest = fetch_manifest()
    except Exception as e:
        log('manifest fetch failed: %s' % e, xbmc.LOGERROR)
        return {'ok': False, 'error': str(e), 'applied': [], 'failed': []}

    updates = compute_updates(manifest)
    if not updates:
        _say('הבילד מעודכן')
        return {'ok': True, 'applied': [], 'failed': [], 'up_to_date': True,
                'manifest_generated': manifest.get('generated_utc')}

    state = _load_state()
    applied, failed = [], []
    wizard_changed = False

    dp = None
    if not silent:
        dp = xbmcgui.DialogProgress()
        dp.create('MasterKodi IL', 'מוריד עדכונים...')

    total = len(updates)
    for i, entry in enumerate(updates):
        if dp and dp.iscanceled():
            break
        pct = int((i / float(total)) * 100)
        aid = entry['id']
        if dp:
            dp.update(pct, 'מעדכן: %s (%d/%d)' % (aid, i + 1, total))
        try:
            _apply_one(entry)
            state[aid] = entry['sha256']
            applied.append(aid)
            if aid == ADDON_ID:
                wizard_changed = True
        except Exception as e:
            log('update failed for %s: %s' % (aid, e), xbmc.LOGERROR)
            failed.append(aid)

    _save_state(state)
    if dp:
        dp.update(100, 'מרענן רשימת תוספים...')
    xbmc.executebuiltin('UpdateLocalAddons')
    xbmc.sleep(500)
    if dp:
        dp.close()

    # config payload (default userdata) - applied on version bump only
    _maybe_apply_config(manifest, state)
    _save_state(state)

    summary = {
        'ok': not failed,
        'applied': applied,
        'failed': failed,
        'wizard_changed': wizard_changed,
        'up_to_date': False,
        'manifest_generated': manifest.get('generated_utc'),
    }
    log('update summary: %s' % summary)
    return summary


def _maybe_apply_config(manifest, state):
    cfg = manifest.get('config')
    if not cfg:
        return
    key = 'config:%s' % cfg['version']
    if state.get('__config__') == key:
        return  # already applied this config version
    try:
        data = _download(cfg['url'])
        if hashlib.sha256(data).hexdigest() != cfg['sha256']:
            log('config sha mismatch, skipping', xbmc.LOGWARNING)
            return
        home = xbmcvfs.translatePath('special://home/')
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            z.extractall(home)
        state['__config__'] = key
        log('applied config %s' % cfg['version'])
    except Exception as e:
        log('config apply failed: %s' % e, xbmc.LOGWARNING)


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
        dlg.notification('MasterKodi IL', 'הבילד מעודכן ✓', xbmcgui.NOTIFICATION_INFO, 4000)
        return
    names = '\n'.join('• %s → %s' % (u['id'], u['version']) for u in updates[:15])
    more = '' if len(updates) <= 15 else '\n(ועוד %d)' % (len(updates) - 15)
    if not dlg.yesno('MasterKodi IL', 'נמצאו %d עדכונים:\n%s%s\n\nלהתקין עכשיו?' % (len(updates), names, more)):
        return
    summary = run_update(silent=False)
    if summary.get('failed'):
        dlg.ok('MasterKodi IL', 'הותקנו %d, נכשלו %d:\n%s' % (
            len(summary['applied']), len(summary['failed']), ', '.join(summary['failed'])))
    else:
        msg = 'עודכנו %d תוספים ✓' % len(summary['applied'])
        if summary.get('wizard_changed'):
            msg += '\n(האשף עודכן - מומלץ להפעיל מחדש)'
        dlg.ok('MasterKodi IL', msg)


def silent_check():
    """For the service: apply quietly, notify only if something changed."""
    summary = run_update(silent=True)
    if summary.get('applied'):
        xbmcgui.Dialog().notification(
            'MasterKodi IL', 'עודכנו %d תוספים ✓' % len(summary['applied']),
            xbmcgui.NOTIFICATION_INFO, 5000)
    return summary
