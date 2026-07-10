# -*- coding: utf-8 -*-
# Fetch cast + per-actor gender from TMDb, to feed the translation
# prompt. Hebrew is heavily gendered; knowing who's male/female lets
# the model pick correct verb/adjective/pronoun forms.
#
# API key resolution, in order:
#   1. Gears' own TMDb key (the user already configured it in the gears
#      addon -- read it straight from gears' settings.db, read-only).
#   2. A bundled public TMDb v3 key (jurialmunkey's, shipped openly in
#      script.module.tmdbhelper and shared by tens of thousands of
#      installs) so gender works out of the box with zero setup.
#
# TMDb gender codes: 1 = female, 2 = male, 0/3 = unknown/non-binary.

import os
import sqlite3

try:
    import requests
except ImportError:
    requests = None

from . import kodi_utils

API_BASE = 'https://api.themoviedb.org/3'
TIMEOUT = 12

# Public fallback key from jurialmunkey/plugin.video.themoviedb.helper.
BUNDLED_TMDB_KEY = 'a07324c669cac4d96789197134ce272b'

_GEARS_SETTINGS_DB = 'special://profile/addon_data/plugin.video.gears/databases/settings.db'

_MAX_CAST = 15  # plenty for gender context; keeps the prompt compact


def _gears_tmdb_key():
    path = kodi_utils.translate_path(_GEARS_SETTINGS_DB)
    if not os.path.isfile(path):
        return ''
    try:
        con = sqlite3.connect(path, timeout=5)
        row = con.execute(
            "SELECT setting_value FROM settings WHERE setting_id='tmdb_api'").fetchone()
        con.close()
        v = (row[0] if row else '') or ''
        return v if v not in ('', 'empty_setting') else ''
    except sqlite3.Error:
        return ''


def api_key():
    return _gears_tmdb_key() or BUNDLED_TMDB_KEY


def _get(url):
    if not requests:
        return None
    try:
        r = requests.get(url, timeout=TIMEOUT)
        if r.status_code != 200:
            kodi_utils.log('TMDb HTTP {0}'.format(r.status_code))
            return None
        return r.json()
    except Exception as e:
        kodi_utils.log('TMDb error: {0}'.format(e))
        return None


def resolve_tmdb_id(imdb_id, media_type='movie'):
    """imdb tt... -> tmdb id via /find. Returns '' on failure."""
    if not imdb_id:
        return ''
    key = api_key()
    data = _get('{0}/find/{1}?api_key={2}&external_source=imdb_id'.format(
        API_BASE, imdb_id, key))
    if not data:
        return ''
    bucket = 'tv_results' if media_type != 'movie' else 'movie_results'
    results = data.get(bucket) or data.get('movie_results') or data.get('tv_results') or []
    if results:
        return str(results[0].get('id') or '')
    return ''


def get_cast(tmdb_id, media_type='movie'):
    """Return [{name, character, gender}] for the top cast, or [].
    gender is 'male' / 'female' / 'unknown'."""
    if not tmdb_id:
        return []
    key = api_key()
    kind = 'tv' if media_type != 'movie' else 'movie'
    data = _get('{0}/{1}/{2}/credits?api_key={3}'.format(API_BASE, kind, tmdb_id, key))
    if not data:
        return []
    out = []
    for c in (data.get('cast') or [])[:_MAX_CAST]:
        g = c.get('gender')
        gender = 'female' if g == 1 else 'male' if g == 2 else 'unknown'
        out.append({
            'name': (c.get('name') or '').strip(),
            'character': (c.get('character') or '').strip(),
            'gender': gender,
        })
    kodi_utils.log('TMDb cast: {0} members for {1} {2}'.format(len(out), kind, tmdb_id))
    return out


def cast_for(imdb_id='', tmdb_id='', media_type='movie'):
    """Convenience: resolve tmdb_id if needed, then fetch cast."""
    tid = str(tmdb_id or '').strip()
    if not tid and imdb_id:
        tid = resolve_tmdb_id(imdb_id, media_type)
    if not tid:
        return []
    return get_cast(tid, media_type)
