# -*- coding: utf-8 -*-
"""פרופילים ומצב ילדים -- Kodi profiles made usable in this build.

Kodi already has profiles: its own picker at start-up, lock codes, locked
settings/add-ons/files. What it lacks, and what this adds:

1. PREPARATION. A profile Kodi creates is not an empty home -- it is worse:
   with its own databases (Kodi's default) it gets a FRESH addon database,
   and a fresh addon database registers every user add-on DISABLED. Entered
   as-is, the profile boots Estuary, "Unable to find plugin plugin.video.pov",
   and no wizard service (measured 2026-10-22). So the MASTER prepares a new
   profile before anyone enters it: a consistent copy of the master's addon
   database (sqlite backup -- the master's is open), the GUI settings (skin,
   language, web server), the home menus (skinshortcuts / skinvariables), the
   skin settings, the engine settings (debrid and account tokens stripped
   unless the parent chose to share them), favourites, the AI-subtitles
   settings (personal keys stripped). The master service does this for any
   unprepared profile on boot and from its idle loop; the menu does it at
   once. A profile that somehow gets entered unprepared but with the wizard
   running completes itself the same way (complete_if_needed).

2. KIDS MODE. The parent picks a profile and an age. A policy file is written
   into THAT profile's own data; both engines consult it where they already
   hold a title's rating (kodirdil/kids.py): titles above the age, and titles
   with NO rating, are left out of every list and refused on the play path.
   The parent can approve single titles by name. Kodi's lock code keeps the
   child out of the other profiles and the settings -- set in Kodi's own
   profile manager, which this menu opens with instructions.

Profile creation itself stays with Kodi's manager: it is the only way that
works on every platform (Android has no restart, and profiles.xml is owned
by the running Kodi).
"""
import io
import json
import os
import re
import shutil
import sqlite3
import xml.etree.ElementTree as ET

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo('id')
MASTER = xbmcvfs.translatePath('special://masterprofile/')
CURRENT = xbmcvfs.translatePath('special://profile/')
POLICY_REL = os.path.join('addon_data', ADDON_ID, 'kids.json')
DONE_REL = os.path.join('addon_data', ADDON_ID, '.profile_prepared')
ADDONS_DB = 'Addons33.db'

AGES = [(3, 'עד 3 -- רק תוכן לכל המשפחה (G / TV-Y)'),
        (7, 'עד 7 -- כולל TV-Y7'),
        (10, 'עד 10 -- כולל PG / TV-PG'),
        (13, 'עד 13 -- כולל PG-13'),
        (14, 'עד 14 -- כולל TV-14'),
        (16, 'עד 16')]

# secrets never copied into a new profile unless the parent shares them
_TOKEN_IDS = re.compile(r'(token|api_key|apikey|client_id|client_secret|refresh|secret|password|\.key$|_key$)', re.I)
_ENGINE_IDS = ('plugin.video.pov', 'plugin.video.gears')
_MENU_DATA = ('addon_data/script.skinshortcuts', 'addon_data/script.skinvariables',
              'addon_data/script.nimbus.helper', 'addon_data/script.fentastic.helper',
              'favourites.xml')


def _log(msg, level=xbmc.LOGINFO):
    xbmc.log('[%s] profiles: %s' % (ADDON_ID, msg), level)


def is_master():
    return os.path.normcase(os.path.normpath(CURRENT)) == os.path.normcase(os.path.normpath(MASTER))


def profiles():
    """[(name, abs_dir, is_master)] from profiles.xml (master first)."""
    out = []
    try:
        root = ET.parse(os.path.join(MASTER, 'profiles.xml')).getroot()
        for p in root.findall('profile'):
            name = (p.findtext('name') or '').strip()
            d = (p.findtext('directory') or '').strip()
            if d.startswith('special://'):
                path = xbmcvfs.translatePath(d)
            else:
                path = os.path.join(MASTER, d)
            out.append((name, os.path.normpath(path), d.startswith('special://masterprofile')))
    except Exception as e:
        _log('profiles.xml unreadable: %s' % e, xbmc.LOGWARNING)
    return out


def _policy_path(profile_dir):
    return os.path.join(profile_dir, POLICY_REL)


def read_policy(profile_dir):
    try:
        with io.open(_policy_path(profile_dir), encoding='utf-8') as fh:
            return json.load(fh)
    except Exception:
        return None


def write_policy(profile_dir, pol):
    p = _policy_path(profile_dir)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with io.open(p, 'w', encoding='utf-8') as fh:
        json.dump(pol, fh, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------- copying

def _strip_tokens(xml_path):
    """Blank every credential-shaped setting value in an addon settings.xml."""
    try:
        t = io.open(xml_path, encoding='utf-8').read()
    except Exception:
        return

    def sub(m):
        if _TOKEN_IDS.search(m.group(1)) and m.group(2):
            return m.group(0).replace('>' + m.group(2) + '<', '><')
        return m.group(0)
    t = re.sub(r'<setting id="([^"]+)"[^>]*>([^<]*)</setting>', sub, t)
    io.open(xml_path, 'w', encoding='utf-8').write(t)


def _copy(src, dst, overwrite=False):
    if not os.path.exists(src):
        return False
    if os.path.exists(dst) and not overwrite:
        return False
    if os.path.isdir(src):
        if os.path.isdir(dst):
            shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(src, dst)
    else:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
    return True


def _copy_addons_db(profile_dir):
    """A consistent copy of the master's addon database into the profile's
    own Database folder, so every add-on the build enabled is enabled there
    too. sqlite's backup API: the master's file is open by Kodi."""
    src = os.path.join(MASTER, 'Database', ADDONS_DB)
    dst_dir = os.path.join(profile_dir, 'Database')
    dst = os.path.join(dst_dir, ADDONS_DB)
    if not os.path.isfile(src) or os.path.isfile(dst):
        return False
    os.makedirs(dst_dir, exist_ok=True)
    s = sqlite3.connect(src, timeout=10)
    try:
        d = sqlite3.connect(dst)
        try:
            s.backup(d)
        finally:
            d.close()
    finally:
        s.close()
    return True


def _fill(profile_dir, share_tokens):
    """Everything a profile needs from the master, never overwriting what the
    profile already has. Returns the list of what was copied."""
    copied = []
    if _copy_addons_db(profile_dir):
        copied.append('Database/' + ADDONS_DB)
    for rel in ('guisettings.xml', 'sources.xml'):
        if _copy(os.path.join(MASTER, rel), os.path.join(profile_dir, rel)):
            copied.append(rel)
    for rel in _MENU_DATA:
        if _copy(os.path.join(MASTER, rel), os.path.join(profile_dir, rel)):
            copied.append(rel)
    try:
        for d in os.listdir(os.path.join(MASTER, 'addon_data')):
            if d.startswith('skin.') and _copy(os.path.join(MASTER, 'addon_data', d),
                                               os.path.join(profile_dir, 'addon_data', d)):
                copied.append('addon_data/' + d)
    except Exception:
        pass
    for eid in _ENGINE_IDS:
        dst = os.path.join(profile_dir, 'addon_data', eid, 'settings.xml')
        if _copy(os.path.join(MASTER, 'addon_data', eid, 'settings.xml'), dst):
            if not share_tokens:
                _strip_tokens(dst)
            copied.append('addon_data/%s/settings.xml%s' % (eid, '' if share_tokens else ' (tokens stripped)'))
    dst = os.path.join(profile_dir, 'addon_data', 'service.subtitles.gearsai', 'settings.xml')
    if _copy(os.path.join(MASTER, 'addon_data', 'service.subtitles.gearsai', 'settings.xml'), dst):
        _strip_tokens(dst)
        copied.append('addon_data/service.subtitles.gearsai/settings.xml (keys stripped)')
    return copied


def _mark_done(profile_dir):
    p = os.path.join(profile_dir, DONE_REL)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    io.open(p, 'w').write('1')


def prepare_profile(name, profile_dir, share_tokens=None):
    """Master-side: make a Kodi-created profile a working copy of the build.
    share_tokens=None keeps whatever the profile's kids policy says."""
    if not os.path.isdir(profile_dir):
        _log('"%s": directory not created yet, skipped' % name)
        return False
    if share_tokens is None:
        pol = read_policy(profile_dir)
        share_tokens = bool(pol and pol.get('shared_debrid'))
    copied = _fill(profile_dir, share_tokens)
    _mark_done(profile_dir)
    _log('prepared "%s": %s' % (name, ', '.join(copied) or 'nothing missing'))
    return True


def prepare_pending():
    """Master service hook: prepare every profile that is not yet prepared.
    Cheap when there is nothing to do (one stat per profile)."""
    if not is_master():
        return 0
    n = 0
    for name, d, master in profiles():
        if master or not os.path.isdir(d) or os.path.isfile(os.path.join(d, DONE_REL)):
            continue
        try:
            if prepare_profile(name, d):
                n += 1
        except Exception as e:
            _log('prepare "%s" failed: %s' % (name, e), xbmc.LOGWARNING)
    return n


def complete_if_needed(monitor=None):
    """In-profile fallback: an unprepared profile that nevertheless runs the
    wizard (its addon database was shared) fills itself from the master."""
    try:
        if is_master() or os.path.isfile(os.path.join(CURRENT, DONE_REL)):
            return False
        pol = read_policy(CURRENT)
        copied = _fill(CURRENT, bool(pol and pol.get('shared_debrid')))
        _mark_done(CURRENT)
        _log('completed from inside the profile: %s' % ', '.join(copied))
        if copied:
            xbmcgui.Dialog().notification('MasterKodi IL', 'הפרופיל הוכן מהמשתמש הראשי',
                                          ADDON.getAddonInfo('icon'), 4000, False)
            xbmc.executebuiltin('ReloadSkin()')
        return True
    except Exception as e:
        _log('completion failed: %s' % e, xbmc.LOGWARNING)
        return False


# ---------------------------------------------------------------- the menu

def _pick_profile(title):
    rows = [(n, d) for n, d, m in profiles() if not m]
    if not rows:
        xbmcgui.Dialog().ok('פרופילים', 'עדיין אין פרופילים נוספים.\n\nצרו פרופיל דרך "ניהול פרופילים של Kodi" ואז חזרו לכאן.')
        return None
    labels = []
    for n, d in rows:
        pol = read_policy(d)
        tag = ('  [COLOR FF27AE60]ילדים · עד %s[/COLOR]' % pol['age']) if pol else ''
        if not os.path.isfile(os.path.join(d, DONE_REL)):
            tag += '  [COLOR FFE5C25A]טרם הוכן[/COLOR]'
        labels.append(n + tag)
    i = xbmcgui.Dialog().select(title, labels)
    return rows[i] if i >= 0 else None


def open_kodi_profiles():
    xbmcgui.Dialog().ok('ניהול פרופילים של Kodi',
                        'עכשיו ייפתח מסך הפרופילים של Kodi.\n'
                        '[B]הוספה:[/B] "הוסף פרופיל" ← שם ← אישור. הבילד מכין את הפרופיל החדש לבד '
                        '(תפריטים, סקין, הגדרות) תוך דקות, או מיד דרך "הכן פרופיל" בתפריט הזה.\n'
                        '[B]קוד נעילה:[/B] בפרופיל הראשי: "נעילת מסך ראשי" וקוד, '
                        'כך שילד לא יכול לעבור לפרופיל אחר.\n'
                        '[B]מסך כניסה:[/B] "הצג מסך כניסה בעת ההפעלה".')
    xbmc.executebuiltin('ActivateWindow(profiles)')


def prepare_now():
    sel = _pick_profile('איזה פרופיל להכין?')
    if not sel:
        return
    name, d = sel
    if prepare_profile(name, d):
        xbmcgui.Dialog().ok('פרופילים', 'הפרופיל "%s" מוכן: תפריטים, סקין והגדרות הועתקו מהמשתמש הראשי.' % name)
    else:
        xbmcgui.Dialog().ok('פרופילים', 'Kodi עדיין לא יצר את התיקייה של "%s". כנסו אליו פעם אחת ב-Kodi ונסו שוב.' % name)


def set_kids_mode():
    sel = _pick_profile('על איזה פרופיל להפעיל מצב ילדים?')
    if not sel:
        return
    name, d = sel
    i = xbmcgui.Dialog().select('גיל מקסימלי לתוכן בפרופיל "%s"' % name, [a[1] for a in AGES])
    if i < 0:
        return
    age = AGES[i][0]
    share = xbmcgui.Dialog().yesno('שיתוף חיבורי דבריד',
                                   'לשתף את חיבורי הדבריד (TorBox / Real-Debrid וכו\') של המשתמש הראשי עם הפרופיל הזה?\n\n'
                                   'בלי שיתוף -- הפרופיל יצטרך חיבור משלו.',
                                   nolabel='לא לשתף', yeslabel='לשתף')
    old = read_policy(d) or {}
    pol = {'age': age, 'approved': old.get('approved', []), 'shared_debrid': bool(share)}
    write_policy(d, pol)
    prepare_profile(name, d, share_tokens=bool(share))
    if share:
        for eid in _ENGINE_IDS:
            _copy(os.path.join(MASTER, 'addon_data', eid, 'settings.xml'),
                  os.path.join(d, 'addon_data', eid, 'settings.xml'), overwrite=True)
    _log('kids mode on "%s": age %s, shared_debrid=%s' % (name, age, share))
    xbmcgui.Dialog().ok('מצב ילדים הופעל',
                        'הפרופיל "%s" מציג ומנגן רק תוכן עד גיל %s.\n'
                        'תוכן ללא דירוג גיל נחסם.\n\n'
                        'חשוב: קבעו קוד נעילה למשתמש הראשי ב"ניהול פרופילים של Kodi", '
                        'אחרת אפשר פשוט לעבור פרופיל.' % (name, age))


def approve_title():
    sel = _pick_profile('לאיזה פרופיל לאשר תוכן?')
    if not sel:
        return
    name, d = sel
    pol = read_policy(d)
    if not pol:
        xbmcgui.Dialog().ok('פרופילים', 'הפרופיל "%s" אינו במצב ילדים.' % name)
        return
    q = xbmcgui.Dialog().input('שם הסרט או הסדרה לאישור')
    if not q:
        return
    from resources.libs import whats_on
    res = whats_on.tmdb('search/multi', {'query': q, 'include_adult': 'false'}, 3600) or {}
    rows = [r for r in (res.get('results') or []) if r.get('media_type') in ('movie', 'tv')][:12]
    if not rows:
        xbmcgui.Dialog().ok('פרופילים', 'לא נמצא "%s".' % q)
        return
    labels = ['%s (%s) · %s' % (r.get('title') or r.get('name'), (r.get('release_date') or r.get('first_air_date') or '')[:4],
                                'סרט' if r['media_type'] == 'movie' else 'סדרה') for r in rows]
    i = xbmcgui.Dialog().select('לאשר עבור "%s":' % name, labels)
    if i < 0:
        return
    key = '%s:%s' % (rows[i]['media_type'], rows[i]['id'])
    approved = list(pol.get('approved') or [])
    if key not in approved:
        approved.append(key)
    pol['approved'] = approved
    write_policy(d, pol)
    xbmcgui.Dialog().notification('MasterKodi IL', 'אושר: %s' % labels[i].split(' · ')[0],
                                  ADDON.getAddonInfo('icon'), 3500, False)


def remove_kids_mode():
    sel = _pick_profile('מאיזה פרופיל להסיר מצב ילדים?')
    if not sel:
        return
    name, d = sel
    p = _policy_path(d)
    if os.path.isfile(p):
        os.remove(p)
        _log('kids mode removed from "%s"' % name)
    xbmcgui.Dialog().ok('פרופילים', 'מצב ילדים הוסר מהפרופיל "%s".' % name)


def menu():
    from resources.libs.ui import WizardMenu
    while True:
        rows = [('ניהול פרופילים של Kodi', 'הוספה / מחיקה / קוד נעילה / מסך כניסה', 'DefaultUser.png'),
                ('הכן פרופיל', 'פרופיל שנוצר ב-Kodi מקבל את התפריטים, הסקין וההגדרות של הבילד', 'DefaultAddonProgram.png'),
                ('הפעל מצב ילדים על פרופיל', 'גיל מקסימלי · תוכן ללא דירוג נחסם · שיתוף דבריד', 'DefaultAddonService.png'),
                ('אשר סרט או סדרה לפרופיל ילדים', 'חיפוש לפי שם · האישור חל גם על הפרקים', 'DefaultVideo.png'),
                ('הסר מצב ילדים', 'הפרופיל חוזר להציג הכל', 'DefaultAddonNone.png'),
                ('חזרה', '', 'DefaultFolderBack.png')]
        sel = WizardMenu.pick('פרופילים ומצב ילדים', rows)
        if sel in (-1, 5):
            return
        (open_kodi_profiles, prepare_now, set_kids_mode, approve_title, remove_kids_mode)[sel]()
