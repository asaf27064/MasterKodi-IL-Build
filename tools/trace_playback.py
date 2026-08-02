#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Capture the anatomy of ONE "click a title -> source list -> play" flow.

Built to compare the SAME title across the two content engines (Gears vs POV)
line by line. The point is a like-for-like trace: same title, same skin, same
box, only the engine differs.

Usage
-----
  python tools/trace_playback.py mark                 # right BEFORE you click
  python tools/trace_playback.py show [--raw]         # right AFTER the sources
                                                      # appear (or after playback)
  python tools/trace_playback.py save gears           # write a baseline doc
  python tools/trace_playback.py diff gears pov       # compare two baselines

`mark` records the current kodi.log size; `show` reads only what was appended
since, so nothing older pollutes the trace.

Why a script and not ad-hoc greps: kodi.log rotates (a restart moves it to
kodi.old.log), so a trace that is not saved is gone -- exactly what happened to
the 2026-08-01 POV trace we wanted to compare against.
"""
import io
import json
import os
import re
import sys

LOG = r'C:\MasterKodi IL\portable_data\kodi.log'
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
STATE = os.path.join(REPO, '.playback_trace_mark')
DOCS = os.path.join(REPO, 'docs', 'playback-traces')

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

# (label, regex) -- ordered by where they appear in a normal flow. Anything that
# marks a STAGE of the click; noise is deliberately excluded.
STAGES = [
    ('tmdbhelper: resolving dummy',   r'resolving dummy path to url'),
    ('kodi: opens the dummy',         r'VideoPlayer::OpenFile.*themoviedb\.helper'),
    ('gearsai: placeholder skipped',  r'Skipping placeholder playback'),
    ('tmdbhelper: dummy finished',    r'successfully resolved dummy file'),
    ('engine: scrape starts',         r'Starting Hebrew subtitles search thread'),
    ('gearsai: prefetch notified',    r'GearsAI prefetch notified'),
    ('engine: scraping window',       r'sources_playback\.xml|SourceResults'),
    ('hebrew subs: per-site results', r'\[(WIZDOM|OPENSUBTITLES|KTUVIT|KT|WIZ|OPS)\][^\n]*(Found|_subtitles_list)'),
    ('hebrew subs: matched sources',  r'Sources with matched subtitles'),
    ('engine: SOURCE LIST shown',     r'sources_results\.xml'),
    ('gearsai: prefetch ready',       r'PREFETCH \| ready'),
    ('play: chosen source',           r'Player ONONON|playing_addon'),
    # only the PLAYBACK snapshot -- the prefetch one also carries these keys
    ('play: release name published',  r"get_video_data \| FINAL.*Tagline_From_Fen"),
    ('subs: auto-picked',             r'place_sub \| selected_sub AFTER'),
    ('subs: active hebrew recorded',  r'remember_active_heb_sub'),
]


def _mark():
    size = os.path.getsize(LOG)
    with io.open(STATE, 'w', encoding='utf-8') as fh:
        fh.write(str(size))
    print('marked at %d bytes -- click the title now, then run: show' % size)


def _tail():
    try:
        with io.open(STATE, encoding='utf-8') as fh:
            mark = int(fh.read().strip())
    except Exception:
        mark = 0
    size = os.path.getsize(LOG)
    if size < mark:                       # log rotated between mark and show
        print('NOTE: kodi.log rotated since the mark (restart?) -- reading from 0')
        mark = 0
    with io.open(LOG, encoding='utf-8', errors='replace') as fh:
        fh.seek(mark)
        return fh.read().splitlines()


def _stamp(line):
    m = re.match(r'\d{4}-\d\d-\d\d (\d\d:\d\d:\d\d\.\d{3})', line)
    return m.group(1) if m else ''


def _secs(hms):
    if not hms:
        return None
    h, m, s = hms.split(':')
    return int(h) * 3600 + int(m) * 60 + float(s)


def _events(lines):
    """Collect stage hits. Carries the last seen timestamp forward: tmdbhelper
    logs MULTI-LINE entries, so the line that actually matches (e.g. "resolving
    dummy path to url") often has no stamp of its own."""
    out, last = [], ''
    for line in lines:
        st = _stamp(line)
        if st:
            last = st
        for label, pat in STAGES:
            if re.search(pat, line, re.I):
                msg = re.sub(r'^.*?<general>:\s*', '', line).strip()
                out.append((st or last, label, msg[:200]))
                break
    return out


def _show(raw=False):
    lines = _tail()
    ev = _events(lines)
    if not ev:
        print('no playback events since the mark -- did you run `mark` first?')
        return ev
    t0 = _secs(ev[0][0])
    print('%-9s %-8s %-32s %s' % ('time', '+secs', 'stage', 'detail'))
    print('-' * 110)
    for stamp, label, msg in ev:
        d = _secs(stamp)
        print('%-9s %-8s %-32s %s'
              % (stamp[:8], ('+%.1f' % (d - t0)) if d and t0 else '', label,
                 (msg if raw else msg[:70])))
    print('-' * 110)
    last = _secs(ev[-1][0])
    if t0 and last:
        print('total: %.1fs across %d events' % (last - t0, len(ev)))
    return ev


def _save(engine):
    ev = _show()
    if not ev:
        return
    os.makedirs(DOCS, exist_ok=True)
    p = os.path.join(DOCS, '%s.json' % engine)
    with io.open(p, 'w', encoding='utf-8') as fh:
        json.dump([{'t': s, 'stage': l, 'msg': m} for s, l, m in ev],
                  fh, ensure_ascii=False, indent=1)
    print('\nbaseline saved -> %s' % p)


def _diff(a, b):
    def load(name):
        with io.open(os.path.join(DOCS, '%s.json' % name), encoding='utf-8') as fh:
            return json.load(fh)
    A, B = load(a), load(b)

    def rel(ev):
        t0 = _secs(ev[0]['t'])
        return [(e['stage'], round((_secs(e['t']) or 0) - t0, 1), e['msg']) for e in ev]
    ra, rb = rel(A), rel(B)
    stages = []
    for s, _, _ in ra + rb:
        if s not in stages:
            stages.append(s)
    print('%-34s %10s %10s   %s' % ('stage', a, b, 'delta'))
    print('-' * 78)
    for s in stages:
        ta = next((t for st, t, _ in ra if st == s), None)
        tb = next((t for st, t, _ in rb if st == s), None)
        d = ('%+.1fs' % (tb - ta)) if (ta is not None and tb is not None) else \
            ('only in %s' % (a if ta is not None else b))
        print('%-34s %10s %10s   %s'
              % (s, '-' if ta is None else '+%.1f' % ta,
                 '-' if tb is None else '+%.1f' % tb, d))


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'show'
    if cmd == 'mark':
        _mark()
    elif cmd == 'show':
        _show('--raw' in sys.argv)
    elif cmd == 'save':
        _save(sys.argv[2])
    elif cmd == 'diff':
        _diff(sys.argv[2], sys.argv[3])
    else:
        print(__doc__)
