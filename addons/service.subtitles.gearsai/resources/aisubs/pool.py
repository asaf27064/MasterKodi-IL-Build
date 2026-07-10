# -*- coding: utf-8 -*-
# Community subtitle pool client.
#
# Translating a movie costs real Gemini quota and ~1-2 minutes of the
# user's time. There's no reason every user should re-translate the same
# title. The pool lets one person's translation serve everyone:
#
#   * Before translating, we ASK the pool (lookup by imdb+S+E). If a good
#     release-match exists, we download it -- instant, free, zero quota.
#   * After we DO translate something new, we PUSH it back so the next
#     person gets it for free.
#
# The pool is a Cloudflare Worker (see cloudflare/ in this addon) backed
# by R2 (the .srt blobs) + D1 (the index). The Worker enforces quality
# server-side (valid SRT, actually Hebrew, sane entry count) so the pool
# can't be poisoned. We store no user identity -- only the subtitle, the
# media key, and the model that produced it.
#
# Everything here is best-effort and fail-open: any pool error just falls
# back to translating locally as before. Gated by the `pool_enabled`
# setting (default OFF until the user has deployed their Worker).

import json
import re

try:
    import urllib.request
    import urllib.parse
    import urllib.error
except ImportError:
    urllib = None

from . import kodi_utils
from . import match

TIMEOUT = 12
LANG = 'he'
# Don't bother the pool with a sub so short it's probably broken, and
# refuse to consider giant blobs.
MIN_ENTRIES = 5

# Built-in community pool baked into the build. Filled in once the official
# MasterKodi Cloudflare Worker is deployed, so every user shares one pool
# with zero setup. A user can still override both in settings (e.g. to run
# their own pool). Leave empty to ship with no default pool.
DEFAULT_POOL_URL = 'https://masterkodi-subpool.asaf27064.workers.dev'
DEFAULT_POOL_TOKEN = 'mk-76ed711408c449eda0c5a2d868720b0438e36309'

# A real User-Agent is REQUIRED: Cloudflare's bot protection 403s the
# default 'Python-urllib/x.y' that urllib sends, which silently broke every
# pool call from inside Kodi (lookups + contributes all returned 403).
USER_AGENT = 'MasterKodiAI/0.2 (+https://masterkodi-subpool.workers.dev)'


# ---------- config ----------
def enabled():
    # On by default; harmless when no URL is configured (calls no-op).
    return kodi_utils.get_bool('pool_enabled', True)


def contribute_enabled():
    return kodi_utils.get_bool('pool_contribute', True)


def _base():
    # User setting wins; otherwise fall back to the baked-in default.
    url = kodi_utils.get_setting('pool_url', '').strip() or DEFAULT_POOL_URL
    return url.rstrip('/') if url else ''


def _token():
    return kodi_utils.get_setting('pool_token', '').strip() or DEFAULT_POOL_TOKEN


def configured():
    return bool(_base())


# ---------- media key ----------
def media_key(imdb='', tmdb='', title='', year='', season='', episode=''):
    """Stable identity for a piece of media, independent of release/rip.
    Prefer imdb, then tmdb, then a title|year slug. Season/episode pin a
    specific episode. MUST match the Worker's normalisation."""
    season = _num(season)
    episode = _num(episode)
    if imdb:
        base = 'imdb:' + str(imdb).strip().lower()
    elif tmdb:
        base = 'tmdb:' + str(tmdb).strip().lower()
    else:
        slug = re.sub(r'[^a-z0-9]+', '-', (title or '').strip().lower()).strip('-')
        base = 'title:{0}:{1}'.format(slug, year or '')
    return '{0}|{1}|{2}'.format(base, season, episode)


def _num(v):
    try:
        return int(str(v).strip())
    except (ValueError, TypeError):
        return 0


# ---------- HTTP ----------
def _req(path, method='GET', payload=None, qs=None):
    base = _base()
    if not base or not urllib:
        return None
    url = base + path
    if qs:
        url += '?' + urllib.parse.urlencode(qs)
    data = None
    headers = {'Accept': 'application/json', 'User-Agent': USER_AGENT}
    tok = _token()
    if tok:
        headers['X-Gears-Key'] = tok
    if payload is not None:
        data = json.dumps(payload).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    try:
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode('utf-8', 'replace')
            ctype = resp.headers.get('Content-Type', '')
            if 'application/json' in ctype:
                return json.loads(raw) if raw else {}
            return raw
    except urllib.error.HTTPError as e:
        # 404 on lookup is normal (nothing in the pool yet).
        if e.code != 404:
            kodi_utils.log('pool {0} {1} -> HTTP {2}'.format(method, path, e.code))
        return None
    except Exception as e:
        kodi_utils.log('pool {0} {1} failed: {2}'.format(method, path, e))
        return None


# ---------- API ----------
def lookup(imdb='', tmdb='', title='', year='', season='', episode=''):
    """Return a list of available pool subs for this media (metadata only):
    [{id, release, model, votes, entry_count}]. Empty list if none / off."""
    if not enabled():
        return []
    key = media_key(imdb, tmdb, title, year, season, episode)
    res = _req('/v1/lookup', 'GET', qs={'key': key, 'lang': LANG})
    if isinstance(res, dict) and res.get('subs'):
        return res['subs']
    return []


def fetch(sub_id, part='he'):
    """Download a pool sub's text. part='he' -> the Hebrew subtitle;
    part='en' -> the English anchor (for re-timing). Returns text or None."""
    if not sub_id:
        return None
    res = _req('/v1/fetch', 'GET', qs={'id': sub_id, 'part': part})
    if isinstance(res, str) and res.strip():
        return res
    return None


def best_anchor(candidates):
    """From pool candidates for a movie, pick the best one we can re-time
    FROM: it must carry an English anchor (has_anchor). Prefer the highest
    model quality, then votes. Returns the chosen dict or None.

    Used when no pooled sub matches the playing release well enough to use
    directly -- we bridge from any anchored translation of the same film."""
    anchored = [c for c in (candidates or []) if c.get('has_anchor')]
    if not anchored:
        return None
    try:
        from . import gemini
        q = gemini.quality
    except Exception:
        q = lambda m: 1
    anchored.sort(key=lambda c: (q(c.get('model', '')), c.get('votes', 0),
                                 c.get('entry_count', 0)), reverse=True)
    return anchored[0]


def best_match(candidates, release):
    """Pick the best pool candidate for the file being played.

    Ranking priority:
      1. Sync match  -- release-token overlap with the playing file. Wrong
         timing makes even a perfect translation useless, so this leads.
      2. Model quality -- among similarly-synced subs, prefer the one made
         by the stronger model (gemini-2.5-flash > the -lite tiers).
      3. Community votes, then line count, as final tie-breakers.

    To stop a slightly-better sync from always beating a much better model,
    the sync score is bucketed in steps of 10% before comparison.
    Returns the chosen dict or None.
    """
    if not candidates:
        return None
    try:
        from . import gemini
        q = gemini.quality
    except Exception:
        q = lambda m: 1
    for c in candidates:
        c['_match'] = match.score(release, c.get('release', ''))
    candidates.sort(key=lambda c: (
        c.get('_match', 0) // 10,         # sync bucket (0,10,20..100 -> 0..10)
        q(c.get('model', '')),            # model quality within the bucket
        c.get('votes', 0),
        c.get('entry_count', 0),
    ), reverse=True)
    return candidates[0]


def contribute(srt_text, entry_count, release='', model='', imdb='', tmdb='',
               title='', year='', season='', episode='', eng=''):
    """Upload a freshly translated Hebrew SRT so others get it free.
    `eng` is the English source it was translated from -- storing it lets
    the pool re-time this Hebrew onto other releases for free. Best-effort;
    returns True on success. The Worker re-validates and de-duplicates by
    content hash, so re-uploading is harmless."""
    if not enabled() or not contribute_enabled():
        return ''
    if not srt_text or entry_count < MIN_ENTRIES:
        return ''
    # Never pollute the pool with a half-finished translation (e.g. some
    # chunks failed and kept their English text). Require most cues to
    # actually be Hebrew before uploading.
    if not _looks_complete(srt_text):
        kodi_utils.log('pool: translation looks incomplete -- not uploading')
        return ''
    payload = {
        'key': media_key(imdb, tmdb, title, year, season, episode),
        'lang': LANG,
        'srt': srt_text,
        'entry_count': int(entry_count),
        'release': release or '',
        'model': model or '',
        'imdb': imdb or '', 'tmdb': tmdb or '',
        'title': title or '', 'year': str(year or ''),
        'season': _num(season), 'episode': _num(episode),
    }
    if eng:
        payload['eng'] = eng
    res = _req('/v1/contribute', 'POST', payload=payload)
    if isinstance(res, dict) and res.get('ok'):
        deduped = res.get('deduped', False)
        kodi_utils.log('pool: contributed sub {0} (deduped={1})'.format(
            res.get('id', '?'), deduped))
        # 'exists' = it was already in the pool (someone uploaded it first);
        # 'uploaded' = we added it now.
        return 'exists' if deduped else 'uploaded'
    return ''


def _looks_complete(srt_text, min_fraction=0.85):
    """True if at least `min_fraction` of cues contain Hebrew text -- guards
    against uploading a partly-translated (English-gap) file. Fail-open."""
    try:
        from . import srt as _srt
        entries = _srt.parse(srt_text)
        if not entries:
            return False
        heb = 0
        for e in entries:
            if any('֐' <= ch <= '׿' for ch in ' '.join(e.lines)):
                heb += 1
        return (heb / float(len(entries))) >= min_fraction
    except Exception:
        return True


def vote(sub_id, direction):
    """direction: +1 (good) or -1 (bad). Best-effort."""
    if not enabled() or not sub_id:
        return
    _req('/v1/vote', 'POST', payload={'id': sub_id, 'dir': 1 if direction >= 0 else -1})


def flag(sub_id):
    """Report a bad/garbage pool sub. Best-effort."""
    if not enabled() or not sub_id:
        return
    _req('/v1/flag', 'POST', payload={'id': sub_id})


def report_failure(reason, imdb='', tmdb='', title='', year='', release='',
                   season='', episode='', model=''):
    """OPT-IN telemetry: tell the pool a translation could not be produced,
    so the maintainer can see which titles/models fail. Only fires when the
    user enabled 'report_failures' (default OFF). Never raises / blocks."""
    try:
        if not kodi_utils.get_bool('report_failures', False):
            return
        if not configured():
            return
        _req('/v1/telemetry/fail', 'POST', payload={
            'key': media_key(imdb=imdb, tmdb=tmdb, title=title, year=year,
                             season=season, episode=episode),
            'imdb': imdb or '', 'title': title or '', 'year': str(year or ''),
            'release': release or '', 'model': model or '',
            'reason': str(reason or 'unknown')[:40],
        })
    except Exception as e:
        kodi_utils.log('report_failure failed: {0}'.format(e))


def test():
    """Settings 'Test pool' action: ping the Worker health endpoint."""
    if not configured():
        return False, 'No pool URL set'
    res = _req('/v1/health', 'GET')
    if isinstance(res, dict) and res.get('ok'):
        return True, 'count={0}'.format(res.get('count', '?'))
    return False, 'No response (check URL / token)'


def stats():
    """Fetch /v1/stats (the same numbers as the web dashboard). Returns the
    dict or None."""
    if not configured():
        return None
    res = _req('/v1/stats', 'GET')
    return res if isinstance(res, dict) else None


# ---------- helpers (mirror service.py scoring) ----------
def _tokens(name):
    if not name:
        return set()
    return set(t for t in re.split(r'[^a-z0-9]+', name.lower()) if t)


def _match(a, b):
    if not a or not b:
        return 0
    return int(round(100.0 * len(a & b) / (min(len(a), len(b)) or 1)))
