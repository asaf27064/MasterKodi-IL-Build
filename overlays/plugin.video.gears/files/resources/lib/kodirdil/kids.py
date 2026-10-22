# -*- coding: utf-8 -*-
########### KODIRDIL - Kids profile content gate ###########
# A Kodi PROFILE becomes a kids profile when the MasterKodi wizard writes a
# policy file into that profile's own data:
#   special://profile/addon_data/plugin.program.masterkodi.il.wizard/kids.json
#   {"age": 10, "approved": ["movie:12345", "tv:67890"], "shared_debrid": true}
# This module answers one question for the engine: may THIS title be listed /
# played in the running profile? It is consulted where the engine already
# holds the title's metadata (the list builders and the play path), so it
# costs nothing beyond one cached stat on the policy file.
#
# Rules (the parent chose the age; everything else errs on the strict side):
#   * no policy file -> everything is allowed (every non-kids profile).
#   * a title the parent explicitly approved is allowed whatever its rating.
#   * a rating is mapped to a minimum age (US MPAA / TV parental guidelines,
#     plus bare-number ratings such as IL/EU "12", "16", "18"); allowed when
#     minimum age <= the profile's age.
#   * NO rating, or an unknown rating -> blocked. Unrated is not "fine for
#     kids", it is "nobody checked".
# The same file is shipped in both engines' kodirdil packages.
import json
import os
import re
import time

try:
    import xbmc
    import xbmcgui
    import xbmcvfs
except ImportError:          # unit tests outside Kodi
    xbmc = xbmcgui = xbmcvfs = None

POLICY_REL = 'addon_data/plugin.program.masterkodi.il.wizard/kids.json'
_cache = {'at': 0.0, 'policy': None, 'path': ''}
_CACHE_SECONDS = 5

# minimum age per rating label (upper-cased, "RATED "/"TV-" prefixes tolerated)
_AGE = {
    'G': 0, 'TV-Y': 0, 'TV-G': 0, 'U': 0, 'ALL': 0, '0': 0, '0+': 0,
    'TV-Y7': 7, 'TV-Y7-FV': 7, '6': 6, '6+': 6, '7': 7, '7+': 7,
    'PG': 10, 'TV-PG': 10, '10': 10, '10+': 10,
    '12': 12, '12+': 12, '12A': 12,
    'PG-13': 13, 'TV-14': 14, '13': 13, '13+': 13, '14': 14, '14+': 14, '15': 15, '15+': 15,
    '16': 16, '16+': 16,
    'R': 17, 'NC-17': 18, 'TV-MA': 17, '17': 17, '18': 18, '18+': 18, 'X': 18,
}


def policy_path():
    if xbmcvfs is None:
        return os.environ.get('KODIRDIL_KIDS_POLICY', '')
    return xbmcvfs.translatePath('special://profile/' + POLICY_REL)


def policy():
    """The running profile's policy dict, or None. Re-read at most every few
    seconds; a missing file is the normal case and costs one stat."""
    now = time.time()
    if now - _cache['at'] < _CACHE_SECONDS:
        return _cache['policy']
    p = policy_path()
    pol = None
    try:
        if p and os.path.isfile(p):
            with open(p, encoding='utf-8') as fh:
                pol = json.load(fh)
            if not isinstance(pol, dict) or 'age' not in pol:
                pol = None
    except Exception:
        pol = None
    _cache.update(at=now, policy=pol, path=p)
    return pol


def is_kids_profile():
    return policy() is not None


def rating_age(label):
    """Minimum age for a rating label, or None when unknown/empty."""
    if not label:
        return None
    s = str(label).strip().upper()
    s = re.sub(r'^(RATED|RATING)\s*[: ]\s*', '', s)
    s = s.replace('TV ', 'TV-').strip()
    if s in _AGE:
        return _AGE[s]
    m = re.match(r'^(\d{1,2})\s*\+?$', s)
    if m:
        return int(m.group(1))
    # "PG-13 (something)" style
    head = s.split(' ')[0].split('/')[0]
    return _AGE.get(head)


def allowed(meta):
    """True when the title may be shown/played in this profile."""
    pol = policy()
    if pol is None:
        return True
    try:
        mt = (meta.get('mediatype') or meta.get('media_type') or '').lower()
        if mt == 'episode' or mt == 'season':
            mt = 'tv'
        elif mt not in ('movie', 'tv', 'tvshow'):
            mt = 'movie' if meta.get('title') and not meta.get('tvshowtitle') else 'tv'
        mt = 'tv' if mt == 'tvshow' else mt
        key = '%s:%s' % (mt, meta.get('tmdb_id') or meta.get('id') or '')
        if key in set(pol.get('approved') or []):
            return True
        age = rating_age(meta.get('mpaa'))
        if age is None:
            return False
        return age <= int(pol.get('age') or 0)
    except Exception:
        return False


def block_notice():
    """Tell the viewer why nothing happened (the play path)."""
    try:
        if xbmcgui is not None:
            xbmcgui.Dialog().notification('MasterKodi IL', 'התוכן הזה לא מאושר בפרופיל הזה',
                                          xbmcgui.NOTIFICATION_WARNING, 3500, False)
        if xbmc is not None:
            xbmc.log('[kodirdil-kids] blocked playback in kids profile', xbmc.LOGINFO)
    except Exception:
        pass
