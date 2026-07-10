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

TIDB_URL = 'https://api.theintrodb.org/v3/media'
SKIPDB_URL = 'https://api.skipdb.tv/api/segments'
TIMEOUT = 10
MIN_CONFIDENCE = 0.4
SKIPDB_MAX_OFFSET = 300


def _get_json(url, ua):
    req = _req.Request(url, headers={'Accept': 'application/json', 'User-Agent': ua})
    raw = _req.urlopen(req, timeout=TIMEOUT).read()
    return json.loads(raw.decode('utf-8', 'replace'))


def _tidb(imdb_id, season, episode, duration_ms):
    q = {'imdb_id': imdb_id}
    try:
        if season:
            q['season'] = int(season)
        if episode:
            q['episode'] = int(episode)
        if duration_ms:
            q['duration_ms'] = int(duration_ms)
    except (TypeError, ValueError):
        pass
    try:
        data = _get_json(TIDB_URL + '?' + _parse.urlencode(q), 'TheIntroDB Kodi Addon/1.0')
    except Exception:
        return {}
    segs = (data or {}).get('segments') or data or {}
    out = {}
    for kind in ('intro', 'recap'):
        arr = segs.get(kind)
        if isinstance(arr, dict):
            arr = [arr]
        if not isinstance(arr, list):
            continue
        best, best_score = None, -1.0
        for s in arr:
            if not isinstance(s, dict):
                continue
            st, en = s.get('start_ms'), s.get('end_ms')
            if en is None:
                continue
            if st is None:
                st = 0
            if en <= st:
                continue
            conf = s.get('confidence')
            conf = 0.5 if conf is None else float(conf)
            score = conf + s.get('submission_count', 1) * 0.001
            if score > best_score:
                best_score = score
                best = (st / 1000.0, en / 1000.0)
        if best:
            out[kind] = best
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


def get_segments(imdb_id, season=None, episode=None, duration=None):
    if not imdb_id or not str(imdb_id).startswith('tt'):
        return {}
    duration_ms = int(float(duration) * 1000) if duration else None
    out = _tidb(imdb_id, season, episode, duration_ms)        # best source first
    if 'intro' not in out:                                     # fill gaps from SkipDB
        for k, v in _skipdb(imdb_id, season, episode, duration).items():
            out.setdefault(k, v)
    return out
