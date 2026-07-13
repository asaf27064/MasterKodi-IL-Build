# -*- coding: utf-8 -*-
# Intro/recap timestamp lookup. Multi-source for best coverage:
#   1. TheIntroDB  (api.theintrodb.org/v3) -- purpose-built, large coverage
#   2. SkipDB      (api.skipdb.tv)          -- fills gaps
# Returns {'intro': (start, end), 'recap': (start, end)} in SECONDS. Fail-open.

import json

try:
    import urllib.request as _req
    import urllib.parse as _parse
except ImportError:                       # pragma: no cover
    import urllib2 as _req
    import urllib as _parse

TIDB_URL = 'https://api.introdb.app/segments'
SKIPDB_URL = 'https://api.skipdb.tv/api/segments'
TIMEOUT = 10
MIN_CONFIDENCE = 0.4
SKIPDB_MAX_OFFSET = 300

# IntroDB API key (idb_...). The authenticated /segments endpoint returns correct
# per-episode intro + recap + outro (the old public /v3/media endpoint had none of
# that -- intro-only, and an imdb->episode-1 fallback bug). Shared public build
# key (Asaf's call, like the mdblist/omdb keys); free at introdb.app.
INTRODB_API_KEY = 'idb_2klbzcuvxEkrBxhsRWW7Le8y8HZ36wPt'


def _get_json(url, ua, auth=False):
    headers = {'Accept': 'application/json', 'User-Agent': ua}
    if auth and INTRODB_API_KEY:
        headers['Authorization'] = 'Bearer ' + INTRODB_API_KEY
    req = _req.Request(url, headers=headers)
    raw = _req.urlopen(req, timeout=TIMEOUT).read()
    return json.loads(raw.decode('utf-8', 'replace'))


def _tidb(imdb_id, season, episode, duration_ms=None, tmdb_id=None):
    # api.introdb.app/segments: keyed by imdb_id (+ season/episode), returns
    # correct per-episode intro/recap/outro as single objects with start_sec/end_sec.
    if not imdb_id:
        return {}
    q = {'imdb_id': imdb_id}
    try:
        if season:
            q['season'] = int(season)
        if episode:
            q['episode'] = int(episode)
    except (TypeError, ValueError):
        pass
    try:
        data = _get_json(TIDB_URL + '?' + _parse.urlencode(q), 'MasterKodiSkipIntro/1.3', auth=True)
    except Exception:
        return {}
    # Safety: reject if the API answered for a different episode.
    try:
        if episode is not None and data.get('episode') is not None \
                and int(data['episode']) != int(episode):
            return {}
        if season is not None and data.get('season') is not None \
                and int(data['season']) != int(season):
            return {}
    except (TypeError, ValueError, AttributeError):
        pass
    out = {}
    for kind in ('intro', 'recap', 'outro'):
        s = data.get(kind)
        if not isinstance(s, dict):
            continue
        st, en = s.get('start_sec'), s.get('end_sec')
        if st is None and s.get('start_ms') is not None:
            st = s['start_ms'] / 1000.0
        if en is None and s.get('end_ms') is not None:
            en = s['end_ms'] / 1000.0
        try:
            st, en = float(st if st is not None else 0), float(en)
        except (TypeError, ValueError):
            continue
        if en <= st:
            continue
        c = s.get('confidence')
        if c is not None:
            try:
                if float(c) < MIN_CONFIDENCE:
                    continue
            except (TypeError, ValueError):
                pass
        out[kind] = (st, en)
    return out


def _skipdb(imdb_id, season, episode, duration):
    q = {'imdb_id': imdb_id}
    try:
        if season:
            q['season'] = int(season)
        if episode:
            q['episode'] = int(episode)
        if duration:
            q['duration'] = int(float(duration))
    except (TypeError, ValueError):
        pass
    try:
        data = _get_json(SKIPDB_URL + '?' + _parse.urlencode(q), 'MasterKodiSkipIntro/1.2')
    except Exception:
        return {}
    segs = (data or {}).get('segments') or {}
    out = {}
    for kind in ('intro', 'recap'):
        s = segs.get(kind)
        if not s:
            continue
        try:
            st, en = float(s.get('start_sec')), float(s.get('end_sec'))
        except (TypeError, ValueError):
            continue
        if en <= st:
            continue
        if (s.get('match') or '') == 'out-of-range':
            try:
                if abs(float(s.get('offset_sec') or 0)) > SKIPDB_MAX_OFFSET:
                    continue
            except (TypeError, ValueError):
                pass
        c = s.get('confidence')
        if c is not None:
            try:
                if float(c) < MIN_CONFIDENCE:
                    continue
            except (TypeError, ValueError):
                pass
        out[kind] = (st, en)
    return out


def get_segments(imdb_id, season=None, episode=None, duration=None, tmdb_id=None):
    has_imdb = imdb_id and str(imdb_id).startswith('tt')
    if not has_imdb:
        return {}                                             # introdb /segments needs imdb
    out = _tidb(imdb_id, season, episode)                     # IntroDB: intro + recap
    if 'intro' not in out:                                    # fill gaps from SkipDB
        for k, v in _skipdb(imdb_id, season, episode, duration).items():
            out.setdefault(k, v)
    return out
