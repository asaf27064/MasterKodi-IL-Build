# -*- coding: utf-8 -*-
# Aggregates English subtitle candidates across all free providers, so the
# orchestrator has a single entry point. Adding a new source = add one
# function to PROVIDERS below; everything else (ranking, dedup, download)
# is provider-agnostic because the downloader auto-detects gzip/zip/raw.
#
# Hebrew lookups stay OpenSubtitles-only (used just to avoid spending
# Gemini quota when a real human Hebrew sub already exists).

import re
import time
from concurrent.futures import ThreadPoolExecutor

from . import opensubtitles, kodi_utils

try:
    from . import subdl
except Exception:  # pragma: no cover - never let an import break search
    subdl = None

# (label, search_fn). Each fn(imdb_id, title, media_type, season, episode,
# year=, **kw) -> [ {name, download_link, downloads, format, lang} ].
# OpenSubtitles is listed FIRST -> it wins dedup ties + is the reliable base.
# subdl is a clean keyed source (no-op until the user sets a subdl API key);
# it runs in parallel with a short grace so it can never stall the search.
PROVIDERS = [('opensubtitles', opensubtitles.search_english)]
if subdl is not None:
    PROVIDERS.append(('subdl', subdl.search_english))

# Kodi KILLS a subtitle search that runs too long (we saw "Error getting"
# ~3.5s when a slow 2nd provider ran AFTER OpenSubtitles). Design:
#   * OpenSubtitles (the reliable primary, PROVIDERS[0]) is awaited fully --
#     same as the long-working OS-only behavior.
#   * Every EXTRA provider runs in PARALLEL and is given only a short GRACE
#     beyond that, so a slow/unreachable source can ADD results when quick but
#     can NEVER delay the search or get it killed.
PRIMARY_TIMEOUT = 15.0   # OpenSubtitles' own request timeout is the real cap
EXTRA_GRACE = 2.0        # max extra wall-time a secondary source may add
                         # (subdl is ~1.5-3s; 2.0 lets it usually make the
                         # window while keeping total search well under Kodi's
                         # ~5s subtitle-search kill).


def _norm(name):
    return re.sub(r'[^a-z0-9]+', '', (name or '').lower())


def search_english(imdb_id='', title='', media_type='movie', season=0, episode=0, year=''):
    """Merge English candidates from every provider, dedup by release name
    (keeping the most-downloaded), tag each with its source provider.

    OpenSubtitles is awaited fully; extra providers run concurrently with only
    a short grace -- so a slow source can never stall or kill the search."""
    out = {}

    def _run(label, fn):
        try:
            out[label] = fn(imdb_id=imdb_id, title=title, media_type=media_type,
                            season=season, episode=episode, year=year) or []
        except Exception as e:
            kodi_utils.log('source {0} failed: {1}'.format(label, e))
            out[label] = []

    ex = ThreadPoolExecutor(max_workers=max(1, len(PROVIDERS)))
    fut = {label: ex.submit(_run, label, fn) for label, fn in PROVIDERS}
    # Wait for the primary fully (reliable base = old behavior).
    primary = PROVIDERS[0][0]
    try:
        fut[primary].result(timeout=PRIMARY_TIMEOUT)
    except Exception:
        pass
    # Give the extras only a short grace beyond the primary.
    grace_deadline = time.time() + EXTRA_GRACE
    for label, _ in PROVIDERS[1:]:
        try:
            fut[label].result(timeout=max(0.0, grace_deadline - time.time()))
        except Exception:
            pass  # too slow this round -> just skip it
    ex.shutdown(wait=False)  # don't block on a straggler; its HTTP has its own timeout

    merged = []
    by_name = {}
    for label, _ in PROVIDERS:  # OpenSubtitles first -> preferred on ties
        for c in out.get(label, []):
            c['provider'] = label
            key = _norm(c.get('name', '')) or c.get('download_link', '')
            prev = by_name.get(key)
            if prev is None:
                by_name[key] = c
                merged.append(c)
            elif c.get('downloads', 0) > prev.get('downloads', 0):
                merged[merged.index(prev)] = c
                by_name[key] = c
    merged.sort(key=lambda c: c.get('downloads', 0), reverse=True)
    kodi_utils.log('sources: {0} english candidates ({1})'.format(
        len(merged), {k: len(v) for k, v in out.items()}))
    return merged


def search_hebrew(imdb_id='', title='', media_type='movie', season=0, episode=0, year=''):
    """Existing human Hebrew subs (OpenSubtitles only)."""
    return opensubtitles.search_hebrew(
        imdb_id=imdb_id, title=title, media_type=media_type,
        season=season, episode=episode)


def download(download_link):
    """Provider-agnostic fetch -> SRT text (auto-detects gzip/zip/raw)."""
    return opensubtitles.download_srt(download_link)
