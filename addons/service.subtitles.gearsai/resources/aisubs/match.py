# -*- coding: utf-8 -*-
# Release-name match scoring, ported from the build's DarkSubs
# (service.subtitles.All_Subs, resources/modules/engine.py
# `calculate_sync_percentage`). We deliberately use the SAME method so the
# "% התאמה" we show lines up with the number users already see in DarkSubs.
#
# How it works (faithful to DarkSubs):
#   1. Clean each name into an ORDERED token list: drop the extension,
#      turn _ + / - and spaces into dots, split on dots, lowercase.
#   2. Boost signal:
#        - if a quality token (1080p/2160p/...) is in the subtitle name but
#          missing from the video name, add it to the video name;
#        - for each known release type (bluray/web-dl/hdtv/cam/...) present
#          in BOTH, repeat that token x3 in both lists (so a matching source
#          type weighs heavily -- it's the strongest sync signal).
#   3. Score = difflib.SequenceMatcher(...).ratio() * 100 over the two token
#      LISTS (order- and duplicate-aware, unlike a plain set overlap).

import os
import re
from difflib import SequenceMatcher

try:
    import xbmc
    import xbmcgui
except ImportError:  # off-device
    xbmc = xbmcgui = None

# Release/source types, exactly as DarkSubs lists them.
RELEASE_NAMES = [
    'blueray', 'bluray', 'blu-ray', 'bdrip', 'brrip', 'brip',
    'hdtv', 'hdtvrip', 'pdtv', 'tvrip', 'hdrip', 'hd-rip',
    'web', 'web-dl', 'web dl', 'web-dlrip', 'webrip', 'web-rip',
    'dvdr', 'dvd-r', 'dvd-rip', 'dvdrip', 'cam', 'hdcam', 'cam-rip',
    'camrip', 'screener', 'dvdscr', 'dvd-full', 'telecine', 'hdts', 'telesync',
]

_VIDEO_EXT = {'mkv', 'mp4', 'm4p', 'avi', 'mov', 'mpeg', 'mpg', 'flv', 'wmv',
              'm4v', 'webm', '3gp', 'ogg', 'ogv', 'rmvb', 'divx', 'vob', 'dat',
              'mts', 'm2ts', 'ts', 'yuv'}
_SUB_EXT = {'srt', 'str', 'sub', 'sup', 'idx', 'ass', 'ssa', 'vtt', 'smi'}

_QUALITY_RE = re.compile(r'^\d{3,4}p$')


def tokens(name):
    """Clean a file/release name into an ordered, lowercased token list."""
    if not name:
        return []
    base, ext = os.path.splitext(name)
    if ext.lstrip('.').lower() in _VIDEO_EXT or ext.lstrip('.').lower() in _SUB_EXT:
        name = base
    name = (name.strip()
            .replace('_', '.').replace(' ', '.').replace('+', '.')
            .replace('/', '.').replace('-', '.'))
    return [x.strip().lower() for x in name.split('.') if x.strip()]


def _quality_of(toks):
    """Detect a resolution token (e.g. '1080p', '2160p') in a token list."""
    for t in toks:
        if _QUALITY_RE.match(t) or t in ('2160p', '4k', '4kp'):
            return '2160p' if t in ('4k', '4kp') else t
    return None


def _similar(a, b):
    return int(round(SequenceMatcher(None, a, b).ratio() * 100))


def _sync_percentage(video_tokens, sub_tokens, quality=None):
    v = list(video_tokens)
    s = list(sub_tokens)
    if quality and quality not in v and quality in s:
        v.append(quality)
    for rn in RELEASE_NAMES:
        if rn in v and rn in s:
            v.extend([rn] * 3)
            s.extend([rn] * 3)
    return _similar(v, s)


def score(video_name, sub_name, quality=None):
    """0-100 release-match score between the video file name and a subtitle
    release name, using DarkSubs' algorithm. `quality` (e.g. '2160p') is
    auto-detected from the video name when not supplied."""
    vt = tokens(video_name)
    st = tokens(sub_name)
    if not vt or not st:
        return 0
    if quality is None:
        quality = _quality_of(vt)
    return _sync_percentage(vt, st, quality)


def rank_candidates(candidates, release, is_episode=False, season=0, episode=0):
    """Score + sort English candidates in place. Sets c['match'] (the real
    release sync %) and orders by a rank that, for a SERIES EPISODE, strongly
    prefers the EXACT episode and pushes SEASON PACKS to the bottom (a season
    pack zip holds many episodes and we can't reliably pick the right one).
    SDH gets a tiny bonus (better gender). Returns the list."""
    try:
        s, e = int(season or 0), int(episode or 0)
    except (ValueError, TypeError):
        s = e = 0
    for c in candidates:
        base = score(release, c.get('name', ''))
        rank = base
        if is_episode and s and e:
            nm = re.sub(r'[^a-z0-9]+', '', (c.get('name') or '').lower())
            has_ep = any(p in nm for p in (
                's%02de%02d' % (s, e), 's%de%d' % (s, e), '%dx%02d' % (s, e)))
            season_tagged = ('s%02d' % s in nm) or ('season%d' % s in nm)
            is_pack = bool(c.get('full_season')) or (season_tagged and not has_ep)
            if has_ep:
                rank += 30          # exact episode -> strongly preferred
            if is_pack:
                rank -= 70          # season pack -> wrong granularity, avoid
        if c.get('hi'):
            rank += 8               # SDH: speaker tags = much better Hebrew gender
        c['match'] = base
        c['_rank'] = rank
    candidates.sort(key=lambda c: (c.get('_rank', 0), c.get('downloads', 0)), reverse=True)
    return candidates


def player_release():
    """The real release/file name of the playing item -- even for debrid
    streams whose getPlayingFile() is a UUID with no release info. Mirrors
    DarkSubs: a window property the source addon sets, else the tagline."""
    if not xbmcgui:
        return ''
    try:
        rn = xbmcgui.Window(10000).getProperty('subs.player_filename') or ''
        if not rn and xbmc:
            rn = xbmc.getInfoLabel('VideoPlayer.Tagline') or ''
        return rn
    except Exception:
        return ''
