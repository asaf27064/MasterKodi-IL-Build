# -*- coding: utf-8 -*-
"""מה רואים הערב? -- one screen that answers the evening question.

Three lanes, all built from what this box already knows, no account needed:
  * המשך        -- what the engine itself would resume (POV's merged
                   continue-watching route, Gears' in-progress + next-episode
                   routes), fetched through JSON-RPC so every item carries the
                   engine's own play path.
  * בשבילך      -- TMDb discover on the genres the viewer actually watches
                   (weighted by recency, from the engine's watched/progress/
                   favourites tables), minus everything already seen or hidden.
  * חם השבוע    -- TMDb trending, same filters.
Every pick in the last two lanes is checked for HEBREW SUBTITLES (Wizdom's
by-imdb lookup, cached a week) and the ones that have them come first with a
badge -- the one promise no other build can make up front.

Engine-agnostic (POV or Gears), skin-agnostic (its own WindowXMLDialog with
generic fonts), Hebrew-first (TMDb in he-IL). Nothing here is required for
the build to work: every step fails open to an emptier screen, never to an
error dialog.
"""
import json
import os
import sqlite3
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo('id')
_WIZ_PATH = xbmcvfs.translatePath(ADDON.getAddonInfo('path'))
_PROFILE = xbmcvfs.translatePath(ADDON.getAddonInfo('profile'))
_ADDON_DATA = xbmcvfs.translatePath('special://profile/addon_data/')

TMDB = 'https://api.themoviedb.org/3'
IMG = 'https://image.tmdb.org/t/p/'
WIZDOM = 'https://wizdom.xyz/api/search?action=by_id&imdb=%s'
LANG = 'he-IL'
REGION = 'IL'
UA = 'MasterKodiIL'

CANDIDATE_TTL = 24 * 3600
DETAIL_TTL = 30 * 24 * 3600
SUBS_TTL = 7 * 24 * 3600
LANE_SIZE = 12
TASTE_ITEMS = 40

LANES = (('continue', 'המשך'), ('foryou', 'בשבילך'), ('hot', 'חם השבוע'))


def _log(msg, level=xbmc.LOGINFO):
    xbmc.log('[%s] whats-on: %s' % (ADDON_ID, msg), level)


# ------------------------------------------------------------------ engine

def engine():
    if xbmc.getCondVisibility('System.HasAddon(plugin.video.pov)'):
        return 'plugin.video.pov'
    if xbmc.getCondVisibility('System.HasAddon(plugin.video.gears)'):
        return 'plugin.video.gears'
    return ''


def _engine_db(eng, name):
    return os.path.join(_ADDON_DATA, eng, name)


def _rows(db, sql, args=()):
    try:
        if not os.path.isfile(db):
            return []
        con = sqlite3.connect(db, timeout=5)
        try:
            return con.execute(sql, args).fetchall()
        finally:
            con.close()
    except Exception as e:
        _log('db read failed %s: %s' % (os.path.basename(db), e), xbmc.LOGDEBUG)
        return []


def history(eng):
    """[(media_type, tmdb_id, last_played, weight)] newest first, from the
    engine's own tables. Episodes collapse to their show. Favourites count
    as strong signals with no date."""
    out = {}
    watched = _engine_db(eng, 'watched.db')
    tbl = 'watched_status' if eng == 'plugin.video.pov' else 'watched'
    for db_type, media_id, last in _rows(watched, 'select db_type, media_id, last_played from %s' % tbl):
        mt = 'movie' if db_type == 'movie' else 'tv'
        key = (mt, str(media_id))
        if last and last > out.get(key, ('',))[0]:
            out[key] = (last, 1.0)
        elif key not in out:
            out[key] = (last or '', 1.0)
    for db_type, media_id, last in _rows(watched, 'select db_type, media_id, last_played from progress'):
        mt = 'movie' if db_type == 'movie' else 'tv'
        key = (mt, str(media_id))
        if key not in out or (last or '') > out[key][0]:
            out[key] = (last or '', 1.2)
    fav_db = watched if eng == 'plugin.video.pov' else _engine_db(eng, 'favourites.db')
    fav_tbl = 'favorites' if eng == 'plugin.video.pov' else 'favourites'
    for db_type, tmdb_id in _rows(fav_db, 'select db_type, tmdb_id from %s' % fav_tbl):
        mt = 'movie' if db_type == 'movie' else 'tv'
        key = (mt, str(tmdb_id))
        last, w = out.get(key, ('', 0))
        out[key] = (last, max(w, 1.5))
    rows = [(mt, tid, last, w) for (mt, tid), (last, w) in out.items()]
    rows.sort(key=lambda r: r[2], reverse=True)
    return rows


# ------------------------------------------------------------------ cache

def _cache_path():
    try:
        os.makedirs(_PROFILE, exist_ok=True)
    except Exception:
        pass
    return os.path.join(_PROFILE, 'whats_on.db')


def _cache():
    con = sqlite3.connect(_cache_path(), timeout=10)
    con.execute('create table if not exists kv (k text primary key, v text, ts integer)')
    con.execute('create table if not exists hidden (k text primary key, ts integer)')
    return con


def _kv_get(key, ttl):
    try:
        con = _cache()
        try:
            row = con.execute('select v, ts from kv where k=?', (key,)).fetchone()
        finally:
            con.close()
        if row and time.time() - row[1] < ttl:
            return json.loads(row[0])
    except Exception:
        pass
    return None


def _kv_set(key, value):
    try:
        con = _cache()
        try:
            con.execute('insert or replace into kv (k, v, ts) values (?,?,?)',
                        (key, json.dumps(value, ensure_ascii=False), int(time.time())))
            con.commit()
        finally:
            con.close()
    except Exception as e:
        _log('cache write failed: %s' % e, xbmc.LOGDEBUG)


def hidden_keys():
    try:
        con = _cache()
        try:
            return {r[0] for r in con.execute('select k from hidden')}
        finally:
            con.close()
    except Exception:
        return set()


def hide(key):
    try:
        con = _cache()
        try:
            con.execute('insert or replace into hidden (k, ts) values (?,?)', (key, int(time.time())))
            con.commit()
        finally:
            con.close()
    except Exception:
        pass


# ------------------------------------------------------------------ TMDb

def _tmdb_auth():
    """(bearer_token, v3_key). POV ships a read token in its settings; Gears
    a v3 key; the GearsAI subtitle addon carries a public v3 fallback. The
    first one found wins; none is ever logged."""
    try:
        if xbmc.getCondVisibility('System.HasAddon(plugin.video.pov)'):
            tok = xbmcaddon.Addon('plugin.video.pov').getSetting('tmdb_read_token')
            if tok and tok.count('.') == 2:
                return tok, ''
    except Exception:
        pass
    try:
        if xbmc.getCondVisibility('System.HasAddon(plugin.video.gears)'):
            key = xbmcaddon.Addon('plugin.video.gears').getSetting('tmdb_api')
            if key and len(key) == 32:
                return '', key
    except Exception:
        pass
    try:
        import re
        p = xbmcvfs.translatePath('special://home/addons/service.subtitles.gearsai/resources/lib/kodirdil/websites/ktuvit.py')
        if not os.path.isfile(p):
            p = xbmcvfs.translatePath('special://home/addons/plugin.video.pov/resources/lib/kodirdil/websites/ktuvit.py')
        with open(p, encoding='utf-8', errors='replace') as fh:
            m = re.search(r"'([0-9a-f]{32})'", fh.read())
        if m:
            return '', m.group(1)
    except Exception:
        pass
    return '', ''


def tmdb(path, params=None, ttl=CANDIDATE_TTL):
    params = dict(params or {})
    params.setdefault('language', LANG)
    key = 'tmdb:%s?%s' % (path, urllib.parse.urlencode(sorted(params.items())))
    hit = _kv_get(key, ttl)
    if hit is not None:
        return hit
    bearer, v3 = _tmdb_auth()
    if not bearer and not v3:
        _log('no TMDb credentials found')
        return None
    if v3:
        params['api_key'] = v3
    url = '%s/%s?%s' % (TMDB, path, urllib.parse.urlencode(params))
    headers = {'User-Agent': UA, 'Accept': 'application/json'}
    if bearer:
        headers['Authorization'] = 'Bearer ' + bearer
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.load(resp)
        _kv_set(key, data)
        return data
    except Exception as e:
        _log('tmdb %s failed: %s' % (path, e), xbmc.LOGDEBUG)
        return None


def details(media_type, tmdb_id):
    return tmdb('%s/%s' % (media_type, tmdb_id), {'append_to_response': 'external_ids'}, DETAIL_TTL)


def genre_names(media_type):
    d = tmdb('genre/%s/list' % media_type, {}, DETAIL_TTL) or {}
    return {g['id']: g['name'] for g in d.get('genres', [])}


# ------------------------------------------------------------------ Hebrew subs

def has_hebrew_subs(imdb_id):
    """Wizdom's by-imdb lookup: a non-empty list means at least one Hebrew
    subtitle exists for the title (any episode, for a show). Cached a week;
    a network failure is cached one hour as 'unknown' (None)."""
    if not imdb_id:
        return None
    key = 'subs:' + imdb_id
    hit = _kv_get(key, SUBS_TTL)
    if hit is not None:
        return None if hit == 'unknown' else bool(hit)
    try:
        req = urllib.request.Request(WIZDOM % imdb_id, headers={'User-Agent': UA})
        with urllib.request.urlopen(req, timeout=8) as resp:
            arr = json.load(resp)
        val = bool(isinstance(arr, list) and arr)
        _kv_set(key, 1 if val else 0)
        return val
    except Exception as e:
        _log('wizdom %s: %s' % (imdb_id, e), xbmc.LOGDEBUG)
        _kv_set(key, 'unknown')
        return None


# ------------------------------------------------------------------ taste

def taste(eng):
    """{media_type: {genre_id: weight}} from the viewer's history, recency
    weighted, plus the set of (media_type, tmdb_id) already seen."""
    hist = history(eng)
    seen = {(mt, tid) for mt, tid, _l, _w in hist}
    weights = {'movie': {}, 'tv': {}}
    sample = hist[:TASTE_ITEMS]

    def one(row):
        mt, tid, _last, w = row
        d = details(mt, tid)
        return mt, w, [g['id'] for g in (d or {}).get('genres', [])]

    if sample:
        with ThreadPoolExecutor(max_workers=4) as ex:
            for i, (mt, w, gids) in enumerate(ex.map(one, sample)):
                rec = 1.0 - 0.6 * (i / max(1, len(sample)))   # newest counts most
                for g in gids:
                    weights[mt][g] = weights[mt].get(g, 0) + w * rec
    return weights, seen, len(hist)


def _item(media_type, d, gnames):
    title = d.get('title') or d.get('name') or ''
    date = d.get('release_date') or d.get('first_air_date') or ''
    gids = d.get('genre_ids') or [g['id'] for g in d.get('genres', [])]
    return {
        'key': '%s:%s' % (media_type, d['id']),
        'media_type': media_type, 'tmdb_id': str(d['id']),
        'title': title, 'year': date[:4],
        'rating': round(float(d.get('vote_average') or 0), 1),
        'votes': int(d.get('vote_count') or 0),
        'popularity': float(d.get('popularity') or 0),
        'genre_ids': gids,
        'genres': ' · '.join(gnames.get(g, '') for g in gids[:3] if gnames.get(g)),
        'plot': d.get('overview') or '',
        'poster': (IMG + 'w342' + d['poster_path']) if d.get('poster_path') else '',
        'fanart': (IMG + 'w1280' + d['backdrop_path']) if d.get('backdrop_path') else '',
        'subs': None, 'imdb': '',
    }


def _candidates(eng, lane, weights, seen, hidden, offset=0):
    gm, gt = genre_names('movie'), genre_names('tv')
    out = []
    today = time.strftime('%Y-%m-%d')
    for mt, gnames in (('movie', gm), ('tv', gt)):
        pages = []
        if lane == 'hot':
            pages.append(tmdb('trending/%s/week' % mt, {'page': 1 + offset}) or {})
        else:
            top = sorted(weights.get(mt, {}).items(), key=lambda kv: kv[1], reverse=True)[:3]
            if not top:
                pages.append(tmdb('%s/popular' % mt, {'page': 1 + offset, 'region': REGION}) or {})
            else:
                params = {'sort_by': 'popularity.desc', 'vote_count.gte': 150,
                          'with_genres': '|'.join(str(g) for g, _w in top), 'page': 1 + offset,
                          'include_adult': 'false'}
                if mt == 'movie':
                    params['primary_release_date.lte'] = today
                    params['region'] = REGION
                else:
                    params['first_air_date.lte'] = today
                pages.append(tmdb('discover/%s' % mt, params) or {})
        for d in (pages[0].get('results') or []):
            if not d.get('id') or not (d.get('poster_path')):
                continue
            it = _item(mt, d, gnames)
            if (mt, it['tmdb_id']) in seen or it['key'] in hidden:
                continue
            score = it['popularity'] / 100.0 + it['rating'] / 2.0
            if lane == 'foryou':
                score += sum(weights.get(mt, {}).get(g, 0) for g in it['genre_ids'])
            it['score'] = score
            out.append(it)
    out.sort(key=lambda i: i['score'], reverse=True)
    return out


def _attach_subs(items, limit):
    """Resolve imdb ids and the Hebrew-subs flag for the first `limit` items,
    in parallel; items that have subs float to the front."""
    def one(it):
        d = details(it['media_type'], it['tmdb_id']) or {}
        ext = d.get('external_ids') or {}
        it['imdb'] = ext.get('imdb_id') or d.get('imdb_id') or ''
        if d.get('runtime'):
            it['runtime'] = int(d['runtime'])
        elif d.get('episode_run_time'):
            it['runtime'] = int((d['episode_run_time'] or [0])[0] or 0)
        it['subs'] = has_hebrew_subs(it['imdb'])
        return it
    head = items[:limit]
    with ThreadPoolExecutor(max_workers=4) as ex:
        list(ex.map(one, head))
    head.sort(key=lambda i: (i['subs'] is True, i['score']), reverse=True)
    return head + items[limit:]


def lane_items(eng, lane, refresh=0):
    """The finished list for one lane (cached per lane for the day unless
    refresh asks for the next page)."""
    if lane == 'continue':
        return _continue_items(eng)
    ck = 'lane:%s:%s:%s' % (eng, lane, refresh)
    hit = _kv_get(ck, CANDIDATE_TTL)
    if hit is not None:
        hid = hidden_keys()
        return [i for i in hit if i['key'] not in hid]
    weights, seen, _n = taste(eng)
    items = _candidates(eng, lane, weights, seen, hidden_keys(), offset=refresh)
    items = _attach_subs(items, LANE_SIZE + 6)[:LANE_SIZE]
    _kv_set(ck, items)
    return items


def _continue_items(eng):
    if eng == 'plugin.video.pov':
        paths = ['plugin://plugin.video.pov/?name=Continue Watching&mode=build_continue_watching&random_support=true&iconImage=player']
    else:
        paths = ['plugin://plugin.video.gears/?mode=build_in_progress_episode',
                 'plugin://plugin.video.gears/?mode=build_next_episode']
    out, seen = [], set()
    for p in paths:
        try:
            r = json.loads(xbmc.executeJSONRPC(json.dumps({
                'jsonrpc': '2.0', 'id': 1, 'method': 'Files.GetDirectory',
                'params': {'directory': p, 'media': 'video',
                           'properties': ['title', 'art', 'plot', 'year', 'rating', 'season',
                                          'episode', 'showtitle', 'file', 'genre', 'runtime'],
                           'limits': {'start': 0, 'end': LANE_SIZE}}})))
            for f in (r.get('result') or {}).get('files') or []:
                if f.get('filetype') == 'directory' or not f.get('file'):
                    continue
                show = f.get('showtitle') or ''
                title = f.get('title') or f.get('label') or ''
                if f.get('season') and f.get('episode'):
                    title = '%s · עונה %s פרק %s' % (show or title, f['season'], f['episode'])
                key = f['file']
                if key in seen:
                    continue
                seen.add(key)
                art = f.get('art') or {}
                out.append({
                    'key': 'cw:' + key, 'media_type': 'episode' if f.get('episode') else 'movie',
                    'tmdb_id': '', 'title': title, 'year': str(f.get('year') or ''),
                    'rating': round(float(f.get('rating') or 0), 1), 'votes': 0, 'popularity': 0,
                    'genre_ids': [], 'genres': ' · '.join((f.get('genre') or [])[:3]),
                    'plot': f.get('plot') or '', 'runtime': int((f.get('runtime') or 0) / 60),
                    'poster': art.get('poster') or art.get('tvshow.poster') or f.get('thumbnail') or '',
                    'fanart': art.get('fanart') or art.get('tvshow.fanart') or '',
                    'subs': None, 'imdb': '', 'play': f['file'], 'score': 0,
                })
        except Exception as e:
            _log('continue lane failed: %s' % e, xbmc.LOGDEBUG)
    return out[:LANE_SIZE]


def warm(monitor=None):
    """Service hook: build the two network lanes in idle time so the screen
    opens instantly. Cheap when the day's lanes are already cached."""
    try:
        eng = engine()
        if not eng:
            return
        for lane in ('foryou', 'hot'):
            if monitor is not None and monitor.abortRequested():
                return
            lane_items(eng, lane)
    except Exception as e:
        _log('warm failed: %s' % e, xbmc.LOGDEBUG)


# ------------------------------------------------------------------ play

def play_action(eng, it):
    """The builtin that starts the pick: the engine's own path for a
    continue-watching row, play for a movie, the seasons list for a show."""
    if it.get('play'):
        return 'PlayMedia(%s)' % it['play']
    if it['media_type'] == 'movie':
        if eng == 'plugin.video.pov':
            return 'PlayMedia(plugin://plugin.video.pov/?mode=play_media&mediatype=movie&tmdb_id=%s)' % it['tmdb_id']
        return 'PlayMedia(plugin://plugin.video.gears/?mode=playback.meta&media_type=movie&tmdb_id=%s)' % it['tmdb_id']
    return 'ActivateWindow(Videos,"plugin://%s/?mode=build_season_list&tmdb_id=%s",return)' % (eng, it['tmdb_id'])


# ------------------------------------------------------------------ window

class WhatsOnWindow(xbmcgui.WindowXMLDialog):
    LIST, LANE_BASE, PLAY, HIDE, MORE = 100, 200, 300, 301, 302

    def __init__(self, *args, **kwargs):
        self.eng = kwargs.pop('engine')
        self.lane = 'continue'
        self.items = []
        self.refresh = {'foryou': 0, 'hot': 0}
        self.action = None
        super().__init__(*args)

    def onInit(self):
        self.setProperty('heading', 'מה רואים הערב?')
        try:
            n = len(history(self.eng))
            self.setProperty('summary', 'לפי %d כותרים שראית · רק מה שעוד לא ראית' % n if n else 'עוד לא ראית כלום כאן -- נתחיל מהפופולרי')
        except Exception:
            self.setProperty('summary', '')
        for i, (_k, name) in enumerate(LANES):
            self.getControl(self.LANE_BASE + 1 + i).setLabel(name)
        self._load('continue')
        if not self.items:
            self._load('foryou')
        self.setFocusId(self.LIST if self.items else self.LANE_BASE + 2)

    def _load(self, lane):
        self.lane = lane
        self.setProperty('lane', lane)
        self.setProperty('status', 'טוען...')
        try:
            self.items = lane_items(self.eng, lane, self.refresh.get(lane, 0))
        except Exception as e:
            _log('lane %s failed: %s' % (lane, e), xbmc.LOGWARNING)
            self.items = []
        self._fill()

    def _fill(self):
        lst = self.getControl(self.LIST)
        lst.reset()
        for it in self.items:
            li = xbmcgui.ListItem(it['title'])
            li.setArt({'poster': it['poster'], 'thumb': it['poster'], 'fanart': it['fanart']})
            meta = ' · '.join(x for x in (it['year'], it['genres'], ('דירוג %.1f' % it['rating']) if it['rating'] else '',
                                          ('%d דק׳' % it['runtime']) if it.get('runtime') else '') if x)
            li.setProperty('meta', meta)
            li.setProperty('plot', it['plot'] or 'אין תקציר בעברית לכותר הזה.')
            li.setProperty('subs', 'yes' if it['subs'] else ('no' if it['subs'] is False else ''))
            li.setProperty('kind', it['media_type'])
            lst.addItem(li)
        with_subs = sum(1 for i in self.items if i['subs'])
        if self.lane == 'continue':
            self.setProperty('status', 'ממשיכים מאיפה שעצרת' if self.items else 'אין מה להמשיך -- תבחר משהו חדש')
        elif self.items:
            self.setProperty('status', '%d הצעות · %d עם כתוביות בעברית' % (len(self.items), with_subs))
        else:
            self.setProperty('status', 'לא נמצאו הצעות כרגע')
        play = self.getControl(self.PLAY)
        play.setLabel('פתיחת הסדרה' if (self.items and self.items[0]['media_type'] == 'tv') else 'צפייה')

    def _selected(self):
        try:
            i = self.getControl(self.LIST).getSelectedPosition()
            return self.items[i] if 0 <= i < len(self.items) else None
        except Exception:
            return None

    def onFocus(self, control_id):
        if control_id == self.LIST:
            it = self._selected()
            if it:
                self.getControl(self.PLAY).setLabel('פתיחת הסדרה' if it['media_type'] == 'tv' else 'צפייה')

    def onClick(self, control_id):
        if self.LANE_BASE < control_id <= self.LANE_BASE + len(LANES):
            self._load(LANES[control_id - self.LANE_BASE - 1][0])
            self.setFocusId(self.LIST if self.items else control_id)
            return
        if control_id in (self.LIST, self.PLAY):
            it = self._selected()
            if it:
                self.action = play_action(self.eng, it)
                self.close()
            return
        if control_id == self.HIDE:
            it = self._selected()
            if it and not it.get('play'):
                hide(it['key'])
                self.items = [i for i in self.items if i['key'] != it['key']]
                self._fill()
            return
        if control_id == self.MORE:
            if self.lane in self.refresh:
                self.refresh[self.lane] += 1
                self._load(self.lane)
            else:
                self._load('foryou')
            self.setFocusId(self.LIST if self.items else self.MORE)

    def onAction(self, action):
        if action.getId() in (9, 10, 92, 216, 247):
            self.close()


def show():
    eng = engine()
    if not eng:
        xbmcgui.Dialog().ok('MasterKodi IL', 'לא נמצא מנוע וידאו (POV או Gears).')
        return
    _log('open (%s)' % eng)
    w = WhatsOnWindow('whats-on.xml', _WIZ_PATH, 'Default', '1080i', engine=eng)
    w.doModal()
    action = w.action
    del w
    _log('closed, action=%s' % (action or '-'))
    if action:
        _log('action: %s' % action[:120])
        xbmc.executebuiltin(action)
