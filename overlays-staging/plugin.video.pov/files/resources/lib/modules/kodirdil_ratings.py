# -*- coding: utf-8 -*-
########### KODIRDIL - per-item extra ratings (IMDb / Metacritic / RT / TMDb) ###########
# Port of the Gears overlay's ratings patch to POV. The Hebrew skins (Estuary,
# Arctic Fuse) read ListItem.Property(gears.<key>_rating) + gears.<key>_icon to
# draw the rating flags on list items -- the property names stay 'gears.*' ON
# PURPOSE so the same skin XML works over both content addons.
#
# Data comes from OMDb (same mapping/thresholds as the Gears overlay's
# omdb_api.fetch_ratings_info), cached in a local sqlite DB (7-day TTL) so a
# list build costs one HTTP call per NEW title only; POV builds items in
# parallel threads, so cache misses overlap. Fail-open by contract: any
# problem returns {} and the item renders without flags.
import io
import os
import json
import time
import sqlite3
from xml.dom.minidom import parseString as md_parse

from modules import kodi_utils

_TTL = 7 * 24 * 3600
_TIMEOUT = 8
_DB = None


def _db_path():
    return os.path.join(
        kodi_utils.translate_path('special://profile/addon_data/plugin.video.pov'),
        'kodirdil_ratings.db')


def _connect():
    dbcon = sqlite3.connect(_db_path(), timeout=20)
    dbcon.execute('CREATE TABLE IF NOT EXISTS ratings '
                  '(imdb_id TEXT PRIMARY KEY, data TEXT, ts INTEGER)')
    return dbcon


def _cache_get(imdb_id):
    try:
        dbcon = _connect()
        row = dbcon.execute('SELECT data, ts FROM ratings WHERE imdb_id = ?',
                            (imdb_id,)).fetchone()
        dbcon.close()
        if row and (time.time() - row[1]) < _TTL:
            return json.loads(row[0])
    except Exception:
        pass
    return None


def _cache_set(imdb_id, data):
    try:
        dbcon = _connect()
        dbcon.execute('INSERT OR REPLACE INTO ratings VALUES (?, ?, ?)',
                      (imdb_id, json.dumps(data), int(time.time())))
        dbcon.commit()
        dbcon.close()
    except Exception:
        pass


def _fetch_omdb(imdb_id, api_key):
    """OMDb XML fetch, mapped exactly like the Gears overlay (icons are bare
    filenames the skin resolves under .../gears_flags/ratings/)."""
    try:
        import requests
        result = requests.get(
            'http://www.omdbapi.com/?apikey=%s&i=%s&tomatoes=True&r=xml'
            % (api_key, imdb_id), timeout=_TIMEOUT).text
        root = dict(md_parse(result).getElementsByTagName('root')[0].attributes.items())
        if root.get('response', 'False') != 'True':
            return {}
        res = dict(md_parse(result).getElementsByTagName('movie')[0].attributes.items())
    except Exception:
        return {}

    def val(name):
        return (res.get(name, '') or '').replace('N/A', '')

    metascore, tomatometer = val('metascore'), val('tomatoMeter')
    tomatousermeter, imdb_rating = val('tomatoUserMeter'), val('imdbRating')
    tomato_image = val('tomatoImage')
    if tomato_image:
        tomatometer_icon = ('rtcertified.png' if tomato_image == 'certified'
                            else 'rtfresh.png' if tomato_image == 'fresh'
                            else 'rtrotten.png')
    elif tomatometer:
        tomatometer_icon = 'rtfresh.png' if int(tomatometer) > 59 else 'rtrotten.png'
    else:
        tomatometer_icon = 'rtrotten.png'
    if tomatousermeter:
        tomatousermeter_icon = ('popcorn.png' if int(tomatousermeter) > 59
                                else 'popcorn_spilt.png')
    else:
        tomatousermeter_icon = 'popcorn_spilt.png'
    return {
        'metascore':       {'rating': '%s%%' % metascore, 'icon': 'metacritic.png'},
        'tomatometer':     {'rating': '%s%%' % tomatometer, 'icon': tomatometer_icon},
        'tomatousermeter': {'rating': '%s%%' % tomatousermeter, 'icon': tomatousermeter_icon},
        'imdb':            {'rating': imdb_rating, 'icon': 'imdb.png'},
    }


def rating_props(tmdb_rating, imdb_id):
    """Return the gears.* rating properties for one list item (or {})."""
    props = {}
    try:
        if tmdb_rating:
            try:
                props['gears.tmdb_rating'] = str(round(float(tmdb_rating), 1))
            except Exception:
                pass
        if kodi_utils.get_setting('kodirdil.extra_ratings', 'true') != 'true':
            return props
        api_key = kodi_utils.get_setting('kodirdil.omdb_api_key', '8e4dcdac')
        if not imdb_id or not api_key:
            return props
        data = _cache_get(imdb_id)
        if data is None:
            data = _fetch_omdb(imdb_id, api_key)
            _cache_set(imdb_id, data)   # cache empties too (bad ids stop retrying)
        for key in ('metascore', 'tomatometer', 'tomatousermeter', 'imdb'):
            rd = data.get(key) or {}
            rating = rd.get('rating')
            if rating and rating not in ('', '%'):
                props['gears.%s_rating' % key] = rating
                props['gears.%s_icon' % key] = rd.get('icon', '')
    except Exception:
        pass
    return props
#########################################################################################
