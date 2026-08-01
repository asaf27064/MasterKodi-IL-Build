# -*- coding: utf-8 -*-
"""MasterKodi community-pool admin tool.

Usage (run with the full python path):
  python pool_admin.py list                     show everything in the pool
  python pool_admin.py download <id|all> [dir]  download one sub / all subs (backup)
  python pool_admin.py delete <id> [<id> ...]   delete specific subs
  python pool_admin.py restore <bundle.json>    upload a backup back into the pool
  python pool_admin.py wipe                     delete EVERYTHING (asks to type WIPE)

The admin token is read from pool_admin_token.txt next to this script.
"""
import io
import json
import os
import sys
import time
import urllib.request

BASE = 'https://masterkodi-subpool.asaf27064.workers.dev'
HERE = os.path.dirname(os.path.abspath(__file__))


def _token():
    p = os.path.join(HERE, 'pool_admin_token.txt')
    try:
        with io.open(p, encoding='utf-8') as f:
            return f.read().strip()
    except Exception:
        sys.exit('missing pool_admin_token.txt next to this script')


def _req(path, payload=None, admin=True):
    url = BASE + path
    data = json.dumps(payload).encode('utf-8') if payload is not None else None
    req = urllib.request.Request(url, data=data,
                                 method='POST' if data is not None else 'GET')
    # Cloudflare rejects Python's default UA with 403.
    req.add_header('User-Agent', 'MasterKodiPoolAdmin/1.0')
    if admin:
        req.add_header('X-Admin-Key', _token())
    if data is not None:
        req.add_header('Content-Type', 'application/json')
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode('utf-8')


def cmd_list():
    subs = json.loads(_req('/v1/admin/list')).get('subs', [])
    if not subs:
        print('(the pool is empty)')
        return
    print('%-3s %-12s %-22s %-6s %-46s %-20s %3s %3s' % (
        '#', 'id', 'title', 's/e', 'release', 'model', 'dl', 'flg'))
    for i, s in enumerate(subs, 1):
        se = ''
        if s.get('season'):
            se = 'S%02d' % int(s['season'])
            if s.get('episode'):
                se += 'E%02d' % int(s['episode'])
        print('%-3d %-12s %-22s %-6s %-46s %-20s %3s %3s' % (
            i, s['id'][:12], (s.get('title') or '')[:22], se,
            (s.get('release') or '').strip()[:46], (s.get('model') or '')[:20],
            s.get('downloads', 0), s.get('flags', 0)))
    print('\ntotal: %d   (use the 12-char id prefix or the full id for delete/download)' % len(subs))


def _resolve(prefix, subs):
    hits = [s for s in subs if s['id'].startswith(prefix)]
    if len(hits) != 1:
        sys.exit('id %r matches %d subs -- use a longer prefix' % (prefix, len(hits)))
    return hits[0]


def _safe(s):
    return ''.join(c for c in s if c not in '\\/:*?"<>|\'').strip() or 'sub'


def cmd_download(target, outdir):
    os.makedirs(outdir, exist_ok=True)
    subs = json.loads(_req('/v1/admin/list')).get('subs', [])
    picked = subs if target == 'all' else [_resolve(target, subs)]
    n = 0
    bundle = []
    for s in picked:
        texts = {}
        for part, tag in (('he', 'he'), ('en', 'eng_anchor')):
            try:
                txt = _req('/v1/fetch?id={0}&part={1}'.format(s['id'], part), admin=False)
            except Exception:
                continue
            if not txt.strip() or txt.strip().startswith('{'):
                continue
            texts[part] = txt
            name = '{0}.{1}.{2}.{3}.srt'.format(
                _safe(s.get('title') or 'sub'),
                _safe((s.get('release') or '').strip())[:60] or s['id'][:12],
                s['id'][:8], tag)
            with io.open(os.path.join(outdir, name), 'w', encoding='utf-8', newline='') as f:
                f.write(txt)
            n += 1
            print('saved', name)
        if texts.get('he'):
            bundle.append({'sub': s, 'srt': texts['he'], 'eng': texts.get('en')})
    # Full-fidelity bundle: metadata + texts, restorable via `restore bundle.json`
    # (or via the /admin web page).
    if bundle:
        bpath = os.path.join(outdir, 'bundle.json')
        with io.open(bpath, 'w', encoding='utf-8') as f:
            json.dump({'exported': int(time.time()), 'subs': bundle}, f, ensure_ascii=False)
        print('bundle.json written (%d subs, restorable)' % len(bundle))
    print('\n%d files -> %s' % (n, outdir))


def cmd_restore(path):
    with io.open(path, encoding='utf-8') as f:
        d = json.load(f)
    subs = d.get('subs', [])
    ok = 0
    for i, b in enumerate(subs, 1):
        if not (b.get('sub') and b.get('srt')):
            continue
        print('restoring %d/%d: %s | %s' % (
            i, len(subs), (b['sub'].get('title') or '')[:25], (b['sub'].get('release') or '')[:40]))
        r = json.loads(_req('/v1/admin/restore', b))
        ok += 1 if r.get('ok') else 0
    print('\nrestored %d/%d' % (ok, len(subs)))


def cmd_delete(prefixes):
    subs = json.loads(_req('/v1/admin/list')).get('subs', [])
    for p in prefixes:
        s = _resolve(p, subs)
        print('deleting %s | %s | %s' % (s['id'][:12], s.get('title'), (s.get('release') or '')[:40]))
        print(' ->', _req('/v1/admin/delete', {'id': s['id']}))


def cmd_wipe():
    subs = json.loads(_req('/v1/admin/list')).get('subs', [])
    print('the pool holds %d subs. This DELETES EVERYTHING, forever.' % len(subs))
    if input('type WIPE to confirm: ').strip() != 'WIPE':
        sys.exit('aborted')
    # auto-backup before wiping
    backup = os.path.join(HERE, 'pool_backup_%s' % time.strftime('%Y%m%d_%H%M%S'))
    print('backing up first ->', backup)
    cmd_download('all', backup)
    print(_req('/v1/admin/wipe', {'confirm': 'WIPE'}))


def main():
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    cmd = args[0]
    if cmd == 'list':
        cmd_list()
    elif cmd == 'download' and len(args) >= 2:
        cmd_download(args[1], args[2] if len(args) > 2 else os.path.join(HERE, 'pool_backup'))
    elif cmd == 'delete' and len(args) >= 2:
        cmd_delete(args[1:])
    elif cmd == 'restore' and len(args) >= 2:
        cmd_restore(args[1])
    elif cmd == 'wipe':
        cmd_wipe()
    else:
        sys.exit(__doc__)


if __name__ == '__main__':
    main()
