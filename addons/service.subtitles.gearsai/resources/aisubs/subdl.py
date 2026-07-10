# -*- coding: utf-8 -*-
# subdl.com English subtitle source. Clean single-call JSON API; the download
# is a .zip our shared downloader auto-detects. Requires a FREE api key from
# https://subdl.com/panel/api (paste it in the addon's "subdl API key"
# setting). With no key this provider is a silent no-op -> zero cost, so it
# never slows the search when unconfigured.

try:
    import requests
except ImportError:
    requests = None

from . import kodi_utils

API = 'https://api.subdl.com/api/v1/subtitles'
DL_BASE = 'https://dl.subdl.com'
# subdl's API is slow lately (8s read timeouts were killing every search).
TIMEOUT = 20
DEFAULT_UA = 'GearsAISubs/0.4'
# Baked community key so subdl works out of the box; users can override it
# with their own free key (subdl.com/panel/api) in settings.
DEFAULT_API_KEY = 'subdl_Bdr9RAsM7hojSMqbtAvsF8ZKKWH54QCT1PNtxMfCb0c'


def _ua():
    return kodi_utils.get_setting('opensubtitles_ua', '') or DEFAULT_UA


def _key():
    return (kodi_utils.get_setting('subdl_api_key', '') or '').strip() or DEFAULT_API_KEY


def search_english(imdb_id='', title='', media_type='movie', season=0, episode=0, year='', **kw):
    """English candidates from subdl.com."""
    if not requests:
        return []
    api_key = _key()
    if not api_key:
        return []

    params = {'api_key': api_key, 'languages': 'EN', 'subs_per_page': 30}
    if imdb_id:
        imdb = str(imdb_id).strip()
        params['imdb_id'] = imdb if imdb.startswith('tt') else 'tt' + imdb
    elif title:
        params['film_name'] = title

    try:
        is_ep = str(media_type) == 'episode' or (int(season or 0) and int(episode or 0))
    except (ValueError, TypeError):
        is_ep = str(media_type) == 'episode'
    if is_ep:
        params['type'] = 'tv'
        try:
            if int(season or 0):
                params['season_number'] = int(season)
            if int(episode or 0):
                params['episode_number'] = int(episode)
        except (ValueError, TypeError):
            pass
    else:
        params['type'] = 'movie'

    try:
        r = requests.get(API, params=params, headers={'User-Agent': _ua()}, timeout=TIMEOUT)
        if r.status_code != 200:
            kodi_utils.log('subdl HTTP {0}'.format(r.status_code))
            return []
        data = r.json()
    except Exception as e:
        kodi_utils.log('subdl error: {0}'.format(e))
        return []

    subs = data.get('subtitles') if isinstance(data, dict) else None
    if not isinstance(subs, list):
        return []

    out = []
    for it in subs:
        if not isinstance(it, dict):
            continue
        u = it.get('url') or ''
        if not u:
            continue
        link = u if u.startswith('http') else (DL_BASE + u)
        name = it.get('release_name') or it.get('name') or ''
        out.append({
            'name': name,
            'download_link': link,
            'downloads': 0,           # subdl exposes no download count
            'format': 'srt',
            'lang': 'en',
            'hi': bool(it.get('hi')),          # SDH/hearing-impaired flag
            'full_season': bool(it.get('full_season')),  # season pack (avoid for one episode)
            'episode': it.get('episode'),
            'season': it.get('season'),
        })
    kodi_utils.log('subdl: {0} en candidates'.format(len(out)))
    return out
