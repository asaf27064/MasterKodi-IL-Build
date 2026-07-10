# -*- coding: utf-8 -*-
# Report GROUND-TRUTH embedded-Hebrew detection to the MasterKodi community
# taglines list. gearsai (during playback) is the only component that actually
# reads the file's real subtitle streams -- so when Kodi confirms an embedded
# Hebrew track, we record this release name. That feeds the Gears source-window
# "מוטמע" indicator, which can only PREDICT (from a list) before playback.
#
# Fully fail-open: any problem is swallowed, never affects playback. Sends only
# a release name + movie/tv flag -- no identity, no file path.

from resources.modules import log

WORKER = 'https://masterkodi-subpool.asaf27064.workers.dev'
TOKEN = 'mk-76ed711408c449eda0c5a2d868720b0438e36309'
HEADERS = {'User-Agent': 'MasterKodiGears/1.0', 'X-Gears-Key': TOKEN}

# De-dupe within a session so replays don't spam the worker.
_seen = set()


def _clean_release(tagline):
    """Strip a trailing container extension from the release name."""
    t = (tagline or '').strip()
    for ext in ('.mkv', '.mp4', '.avi', '.m2ts', '.ts', '.mov'):
        if t.lower().endswith(ext):
            t = t[:-len(ext)]
            break
    return t


def report(tagline, media_type):
    """Fire-and-forget: mark this release as having embedded Hebrew. Separated
    into movie vs. tv on the worker via media_type."""
    try:
        tag = _clean_release(tagline)
        if len(tag) < 8:
            return
        key = tag.lower()
        if key in _seen:
            return
        _seen.add(key)
        mt = 'tvshow' if str(media_type) in ('tv', 'episode', 'tvshow', 'show') else 'movie'
        import threading
        threading.Thread(target=_post, args=(tag, mt), daemon=True).start()
    except Exception:
        pass


def _post(tag, mt):
    try:
        import requests
        requests.post(WORKER + '/v1/taglines',
                      json={'tagline': tag, 'media_type': mt},
                      headers=HEADERS, timeout=6)
        log.warning('[embedded] shared with community list: %s (%s)' % (tag[:60], mt))
    except Exception as e:
        log.warning('[embedded] community report failed: %s' % e)
