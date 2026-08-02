#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Snapshot everything KEEP promises to preserve, so a reinstall can be VERIFIED.

Engine-aware: reads Gears' settings.db / databases and POV's settings.xml /
flat dbs, whichever exist. Secrets are recorded as sha256 prefixes + lengths --
never plaintext, so a snapshot is safe to keep and to paste.

  python tools/keep_snapshot.py save before
  #  ... reinstall with KEEP ...
  python tools/keep_snapshot.py save after
  python tools/keep_snapshot.py diff before after
"""
import hashlib
import io
import json
import os
import re
import sqlite3
import sys

BASE = r'C:\MasterKodi IL\portable_data'
UD = os.path.join(BASE, 'userdata')
AD = os.path.join(UD, 'addon_data')
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), '.keep_snapshots')

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

CRED_IDS = ['tb.token', 'tb.enabled', 'rd.token', 'rd.refresh', 'pm.token',
            'ad.token', 'oc.token', 'trakt.token', 'trakt.refresh', 'trakt.user']


def fp(v):
    if v in (None, ''):
        return ''
    v = str(v)
    return 'sha256:%s/len%d' % (hashlib.sha256(v.encode()).hexdigest()[:10], len(v))


def _db_settings(path, ids):
    out = {}
    if not os.path.isfile(path):
        return out
    try:
        con = sqlite3.connect('file:%s?mode=ro' % path.replace('\\', '/'), uri=True)
        for i in ids:
            r = con.execute("SELECT setting_value FROM settings WHERE setting_id=?",
                            (i,)).fetchone()
            if r:
                out[i] = fp(r[0])
        con.close()
    except Exception as e:
        out['_err'] = str(e)[:60]
    return out


def _xml_settings(path, ids):
    """Read <setting id=..> values.

    Kodi writes UNSET settings self-closing: `<setting id="x" default="true" />`.
    A naive `id="x"[^>]*>([^<]*)<` matches the '/' as part of [^>]*, then the
    '>', then captures the WHITESPACE before the next tag -- reporting an empty
    setting as "set". That made this tool claim Trakt credentials survived a
    reinstall when they had not (2026-08-02). Match the closing form explicitly
    and treat whitespace-only as unset.
    """
    out = {}
    try:
        s = io.open(path, encoding='utf-8').read()
    except Exception:
        return out
    for i in ids:
        m = re.search(r'<setting id="%s"(?:\s[^>]*?)?\s*/>' % re.escape(i), s)
        if m:                                  # self-closing == no value
            out[i] = ''
            continue
        m = re.search(r'<setting id="%s"(?:\s[^>]*?)?>(.*?)</setting>'
                      % re.escape(i), s, re.S)
        if m:
            out[i] = fp(m.group(1).strip())
    return out


def _rows(path, table):
    if not os.path.isfile(path):
        return None
    try:
        con = sqlite3.connect('file:%s?mode=ro' % path.replace('\\', '/'), uri=True)
        n = con.execute('SELECT COUNT(*) FROM "%s"' % table).fetchone()[0]
        con.close()
        return n
    except Exception:
        return None


def snapshot():
    s = {}
    gdb = os.path.join(AD, 'plugin.video.gears', 'databases')
    pov = os.path.join(AD, 'plugin.video.pov')
    s['engine_on_disk'] = {
        'gears': os.path.isdir(os.path.join(BASE, 'addons', 'plugin.video.gears')),
        'pov': os.path.isdir(os.path.join(BASE, 'addons', 'plugin.video.pov')),
    }
    s['creds_gears'] = _db_settings(os.path.join(gdb, 'settings.db'), CRED_IDS)
    s['creds_pov'] = _xml_settings(os.path.join(pov, 'settings.xml'), CRED_IDS)
    s['creds_gemini'] = _xml_settings(
        os.path.join(AD, 'service.subtitles.gearsai', 'settings.xml'),
        ['api_key', 'extra_api_keys'])
    # viewing state -- what "continue watching" is made of
    s['gears_watched'] = {t: _rows(os.path.join(gdb, 'watched.db'), t)
                          for t in ('progress', 'watched_status', 'favorites')}
    s['gears_other'] = {f: os.path.isfile(os.path.join(gdb, f))
                        for f in ('personal_lists.db', 'lists.db', 'tmdb_lists.db',
                                  'favourites.db')}
    s['pov_watched'] = {t: _rows(os.path.join(pov, 'watched.db'), t)
                        for t in ('progress', 'watched_status', 'favorites')}
    fav = os.path.join(UD, 'favourites.xml')
    if os.path.isfile(fav):
        txt = io.open(fav, encoding='utf-8', errors='replace').read()
        s['favourites'] = {
            'count': txt.count('<favourite'),
            'engine': 'gears' if 'plugin.video.gears' in txt else
                      ('pov' if 'plugin.video.pov' in txt else '?'),
            'sha': hashlib.sha256(txt.encode()).hexdigest()[:10]}
    try:
        con = sqlite3.connect(os.path.join(UD, 'Database', 'Addons33.db'))
        rows = con.execute("SELECT addonID, enabled FROM installed").fetchall()
        con.close()
        s['addons'] = {'total': len(rows),
                       'disabled': sorted(a for a, e in rows if not e)}
    except Exception as e:
        s['addons'] = {'_err': str(e)[:60]}
    s['skins_on_disk'] = sorted(
        d for d in os.listdir(os.path.join(BASE, 'addons')) if d.startswith('skin.'))
    return s


def _save(name):
    os.makedirs(OUT, exist_ok=True)
    s = snapshot()
    with io.open(os.path.join(OUT, '%s.json' % name), 'w', encoding='utf-8') as fh:
        json.dump(s, fh, ensure_ascii=False, indent=1)
    _print(s)
    print('\nsaved -> %s' % os.path.join(OUT, '%s.json' % name))


def _print(s):
    print('engines on disk : %s' % s['engine_on_disk'])
    print('skins on disk   : %s' % ', '.join(s['skins_on_disk']))
    for k in ('creds_gears', 'creds_pov', 'creds_gemini'):
        setv = [i for i, v in (s.get(k) or {}).items() if v]
        print('%-16s: %d set  %s' % (k, len(setv), ', '.join(setv)))
    print('gears watched   : %s' % s['gears_watched'])
    print('pov watched     : %s' % s['pov_watched'])
    print('favourites      : %s' % s.get('favourites'))
    print('addons          : %s installed, %s disabled'
          % (s['addons'].get('total'), len(s['addons'].get('disabled') or [])))


def _diff(a, b):
    def load(n):
        with io.open(os.path.join(OUT, '%s.json' % n), encoding='utf-8') as fh:
            return json.load(fh)
    A, B = load(a), load(b)
    keys = sorted(set(A) | set(B))
    for k in keys:
        if A.get(k) != B.get(k):
            print('CHANGED %s' % k)
            print('   %-8s %s' % (a, json.dumps(A.get(k), ensure_ascii=False)[:220]))
            print('   %-8s %s' % (b, json.dumps(B.get(k), ensure_ascii=False)[:220]))
        else:
            print('same    %s' % k)


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'show'
    if cmd == 'save':
        _save(sys.argv[2])
    elif cmd == 'diff':
        _diff(sys.argv[2], sys.argv[3])
    else:
        _print(snapshot())
