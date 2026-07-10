# -*- coding: utf-8 -*-
# On-disk cache of finished Hebrew translations, keyed by a hash of
# (imdb_id|tmdb_id|S|E|source-release-name|model). A translated file
# costs real Gemini quota, so we never re-spend it for the same media
# + release. Stored as .srt files under the addon profile.

import hashlib
import os
import time

from . import kodi_utils


def _cache_dir():
    d = os.path.join(kodi_utils.profile_dir(), 'translated')
    try:
        if not os.path.isdir(d):
            os.makedirs(d)
    except Exception:
        pass
    return d


def make_key(imdb_id='', tmdb_id='', season='', episode='', release='', model=''):
    raw = '|'.join(str(x) for x in (imdb_id, tmdb_id, season, episode, release, model))
    return hashlib.md5(raw.encode('utf-8', 'replace')).hexdigest()


def get(key):
    """Return the cached SRT text for `key`, or None."""
    if not key:
        return None
    path = os.path.join(_cache_dir(), key + '.srt')
    try:
        if os.path.isfile(path):
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                return f.read()
    except Exception as e:
        kodi_utils.log('cache.get failed: {0}'.format(e))
    return None


def get_path(key):
    """Return the cached file PATH if present (Kodi wants a path to
    hand back as the subtitle), else None."""
    if not key:
        return None
    path = os.path.join(_cache_dir(), key + '.srt')
    return path if os.path.isfile(path) else None


def put(key, srt_text, eng=None):
    """Write SRT text to cache, return the file path (or None). When
    `eng` (the English source) is given, store it alongside as a sidecar so
    a later pool upload can carry the re-sync anchor."""
    if not key or not srt_text:
        return None
    path = os.path.join(_cache_dir(), key + '.srt')
    try:
        _atomic_write(path, srt_text)
        if eng:
            try:
                _atomic_write(os.path.join(_cache_dir(), key + '.en.srt'), eng)
            except Exception:
                pass
        return path
    except Exception as e:
        kodi_utils.log('cache.put failed: {0}'.format(e))
        return None


def _atomic_write(path, text):
    """Write via a temp file + rename so a crash mid-write can never leave a
    truncated, half-valid cache file behind (which would later be served and
    even uploaded). os.replace is atomic on the same filesystem."""
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='') as f:
        f.write(text)
        f.flush()
        try:
            os.fsync(f.fileno())
        except Exception:
            pass
    os.replace(tmp, path)


def get_eng(key):
    """Return the cached English source (re-sync anchor) for `key`, or None."""
    if not key:
        return None
    path = os.path.join(_cache_dir(), key + '.en.srt')
    try:
        if os.path.isfile(path):
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                return f.read()
    except Exception:
        pass
    return None


def prune(max_files=200, max_age_days=60):
    """Best-effort cap on cache size/age. Safe to call anytime."""
    d = _cache_dir()
    try:
        files = [os.path.join(d, f) for f in os.listdir(d) if f.endswith('.srt')]
    except Exception:
        return
    now = time.time()
    # age-based
    for f in files:
        try:
            if (now - os.path.getmtime(f)) > max_age_days * 86400:
                os.remove(f)
        except Exception:
            pass
    # count-based (keep newest)
    try:
        files = [os.path.join(d, f) for f in os.listdir(d) if f.endswith('.srt')]
        if len(files) > max_files:
            files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
            for f in files[max_files:]:
                try:
                    os.remove(f)
                except Exception:
                    pass
    except Exception:
        pass
