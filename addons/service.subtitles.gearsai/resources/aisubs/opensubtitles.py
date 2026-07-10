# -*- coding: utf-8 -*-
# Source the English subtitle we'll translate, from the legacy
# keyless OpenSubtitles REST endpoint (rest.opensubtitles.org).
# No API key, no login -- only a User-Agent. Results include a
# gzipped SubDownloadLink we fetch + gunzip to get the raw SRT.
#
# We pick English here because it's by far the best-covered source
# language and Gemini translates EN->HE best. (Other source langs
# are a later enhancement; the orchestrator can pass any langid.)

import gzip
import io

try:
    import requests
except ImportError:
    requests = None

from . import kodi_utils

SEARCH_URL = 'https://rest.opensubtitles.org/search'
TIMEOUT = 15

# OpenSubtitles requires a registered UA; this generic one works for
# the public REST search. Overridable via setting if it ever changes.
DEFAULT_UA = 'GearsAISubs/0.1'


def _ua():
    return kodi_utils.get_setting('opensubtitles_ua', '') or DEFAULT_UA


def _search(url):
    if not requests:
        return []
    try:
        r = requests.get(url, headers={'User-Agent': _ua(),
                                       'Content-Type': 'application/json'},
                         timeout=TIMEOUT)
        if r.status_code != 200:
            kodi_utils.log('OpenSubtitles search HTTP {0}'.format(r.status_code))
            return []
        data = r.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        kodi_utils.log('OpenSubtitles search error: {0}'.format(e))
        return []


def search_english(imdb_id='', title='', media_type='movie', season=0, episode=0, **kw):
    """English candidates to translate. See _search_lang."""
    return _search_lang('eng', 'en', imdb_id, title, media_type, season, episode)


def search_hebrew(imdb_id='', title='', media_type='movie', season=0, episode=0, **kw):
    """Existing HUMAN Hebrew subs -- used by the auto-service to avoid
    spending Gemini quota when a real Hebrew sub already exists."""
    return _search_lang('heb', 'he', imdb_id, title, media_type, season, episode)


def _search_lang(lang, short, imdb_id='', title='', media_type='movie', season=0, episode=0):
    """Return a ranked list of candidate dicts:
       {name, download_link, downloads, format, lang}
    Best (most-downloaded, srt) first."""
    urls = []
    if imdb_id:
        clean = str(imdb_id).replace('tt', '')
        if media_type == 'movie':
            urls.append('{0}/imdbid-{1}/sublanguageid-{2}'.format(SEARCH_URL, clean, lang))
        else:
            urls.append('{0}/episode-{1}/imdbid-{2}/season-{3}/sublanguageid-{4}'.format(
                SEARCH_URL, episode, clean, season, lang))
    if title:
        q = title.replace(' ', '%20')
        if media_type == 'movie':
            urls.append('{0}/query-{1}/sublanguageid-{2}'.format(SEARCH_URL, q, lang))
        else:
            urls.append('{0}/query-{1}/season-{2}/episode-{3}/sublanguageid-{4}'.format(
                SEARCH_URL, q, season, episode, lang))

    seen = set()
    candidates = []
    for idx, url in enumerate(urls):
        # urls[0] is the precise imdb match; the rest are fuzzy title
        # queries. Only fall through to a fuzzy query if the precise
        # match was sparse -- this widens coverage for niche titles
        # (the "OpenSubtitles is sparse" complaint) without drowning
        # popular titles in loosely-matched query noise.
        if idx > 0 and len(candidates) >= 3:
            break
        for item in _search(url):
            if not isinstance(item, dict):
                continue
            link = item.get('SubDownloadLink')
            if not link or link in seen:
                continue
            fmt = (item.get('SubFormat') or '').lower()
            if fmt and fmt != 'srt':
                continue  # we only handle srt for now
            seen.add(link)
            # Popularity field is "SubDownloadsCnt" on this endpoint
            # (NOT SubDownloadsCount). Keep a fallback just in case.
            downloads = item.get('SubDownloadsCnt') or item.get('SubDownloadsCount') or 0
            try:
                downloads = int(downloads)
            except (ValueError, TypeError):
                downloads = 0
            candidates.append({
                'name': item.get('MovieReleaseName') or item.get('SubFileName') or '',
                'download_link': link,
                'downloads': downloads,
                'format': fmt or 'srt',
                'lang': short,
                # Hearing-impaired (SDH): has speaker tags -> better gender.
                'hi': str(item.get('SubHearingImpaired') or '0').strip() == '1',
            })
    candidates.sort(key=lambda c: c['downloads'], reverse=True)
    kodi_utils.log('OpenSubtitles: {0} {1} candidates'.format(len(candidates), short))
    return candidates


def _decode_srt_bytes(raw):
    """Auto-detect gzip / zip / raw and decode to SRT text.

    Shared by all providers (OpenSubtitles serves gzip, subdl serves
    zip, some serve raw .srt) so one downloader handles every source --
    no per-provider routing needed.
    """
    if not raw:
        return None
    # gzip magic: 1f 8b
    if raw[:2] == b'\x1f\x8b':
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gz:
                raw = gz.read()
        except (OSError, IOError):
            pass
    # zip magic: PK\x03\x04 -- pull the first .srt member (largest, to
    # skip tiny readme/nfo files some packagers bundle).
    elif raw[:2] == b'PK':
        try:
            import zipfile
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                srts = [n for n in zf.namelist() if n.lower().endswith('.srt')]
                if not srts:
                    return None
                srts.sort(key=lambda n: zf.getinfo(n).file_size, reverse=True)
                raw = zf.read(srts[0])
        except Exception:
            return None
    # Decode: try utf-8, then cp1255 (Windows Hebrew/Latin), then latin-1.
    for enc in ('utf-8', 'cp1255', 'latin-1'):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, AttributeError):
            continue
    return raw.decode('utf-8', errors='replace')


def download_srt(download_link):
    """Fetch a subtitle link (gzip/zip/raw) and return SRT text or None."""
    if not requests or not download_link:
        return None
    try:
        r = requests.get(download_link, headers={'User-Agent': _ua()}, timeout=TIMEOUT)
        if r.status_code != 200:
            kodi_utils.log('OpenSubtitles download HTTP {0}'.format(r.status_code))
            return None
        return _decode_srt_bytes(r.content)
    except Exception as e:
        kodi_utils.log('OpenSubtitles download error: {0}'.format(e))
        return None
