# -*- coding: utf-8 -*-
"""Prove that a merged addon is EXACTLY clean upstream + our overlay.

Run this after every upstream re-merge (POV, Gears, or any other overlaid
addon). It does not sample or spot-check -- it accounts for every single file:

  1. every file in the mirror that we do NOT overlay is byte-identical to the
     clean upstream zip
  2. every file we DO overlay is byte-identical to our overlay copy
  3. no upstream file silently vanished from the mirror
  4. no file exists in the mirror that came from neither source
  5. every overlay file actually landed

That combination is what makes "the merge is clean" a fact rather than a
feeling: a lost upstream function, a half-applied patch, or a stray file all
show up as a named problem.

Usage:
    python tools/verify_overlay_merge.py                 # every overlays/ addon
    python tools/verify_overlay_merge.py plugin.video.pov
    python tools/verify_overlay_merge.py --piers         # overlays-piers/ only
Exit code 1 if anything is unaccounted for.

NOTE on --piers: `overlays-piers/` applies on top of `overlays/` into the SAME
addons/ dir, but only inside the Piers (Kodi 22) workflow -- see
build-and-release-piers.yml, which runs apply_overlay twice. A normal working
tree therefore holds the Omega build, where a Piers overlay legitimately does
not match (Omega ships Estuary 4.0.0.2, Piers 4.1.0). Checking them here would
report hundreds of phantom problems, so they are skipped unless asked for, and
only mean anything against a tree built the Piers way.
"""

import io
import json
import os
import sys
import zipfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, 'tools'))
from common import PER_ADDON_EXCLUDES  # noqa: E402

SKIP_EXT = {'.pyc', '.pyo', '.pyd'}
SKIP_NAME = {'.DS_Store', 'Thumbs.db', 'desktop.ini', '.gitignore',
             '.gitattributes', '.gitmodules'}


def _skip(rel):
    name = rel.split('/')[-1]
    return (name in SKIP_NAME or os.path.splitext(name)[1] in SKIP_EXT
            or '__pycache__' in rel.split('/'))


def _upstream(zip_path, addon_id):
    out = {}
    top = addon_id + '/'
    with zipfile.ZipFile(zip_path) as zf:
        for n in zf.namelist():
            if n.endswith('/') or not n.startswith(top):
                continue
            rel = n[len(top):]
            if not _skip(rel):
                out[rel] = zf.read(n)
    return out


def _tree(root):
    out = {}
    if not os.path.isdir(root):
        return out
    for dp, dn, fns in os.walk(root):
        dn[:] = [d for d in dn if d != '__pycache__']
        for fn in fns:
            p = os.path.join(dp, fn)
            rel = os.path.relpath(p, root).replace(os.sep, '/')
            if not _skip(rel):
                out[rel] = io.open(p, 'rb').read()
    return out


def verify(overlay_dir):
    base = json.load(io.open(os.path.join(overlay_dir, 'base.json'), encoding='utf-8'))
    addon_id = base['addon_id']
    local = base.get('base_zip_local')
    print('\n' + '=' * 68)
    print('  %s   base %s + overlay %s'
          % (addon_id, base.get('base_version'), base.get('overlay_version')))
    print('=' * 68)
    if not local:
        print('  SKIP: no base_zip_local committed (base is downloaded per build)')
        return []
    zip_path = os.path.join(overlay_dir, local.replace('/', os.sep))
    if not os.path.exists(zip_path):
        print('  SKIP: base zip missing: %s' % local)
        return []

    up = _upstream(zip_path, addon_id)
    mirror = _tree(os.path.join(REPO, 'addons', addon_id))
    overlay = _tree(os.path.join(overlay_dir, 'files'))
    drops = set(PER_ADDON_EXCLUDES.get(addon_id, []))

    problems = []
    identical = patched = 0
    for rel, ub in up.items():
        if rel.split('/')[0] in drops:
            if rel in mirror:
                problems.append('intentionally-dropped file still in mirror: %s' % rel)
            continue
        if rel not in mirror:
            problems.append('UPSTREAM FILE LOST FROM MIRROR: %s' % rel)
            continue
        if rel in overlay:
            if mirror[rel] != overlay[rel]:
                problems.append('overlay not applied cleanly: %s' % rel)
            else:
                patched += 1
        elif mirror[rel] != ub:
            problems.append('NON-OVERLAID FILE DIFFERS FROM UPSTREAM: %s' % rel)
        else:
            identical += 1

    for rel in mirror:
        if rel not in up and rel not in overlay:
            problems.append('file in mirror from neither upstream nor overlay: %s' % rel)
    for rel, ob in overlay.items():
        if rel not in mirror:
            problems.append('overlay file never reached mirror: %s' % rel)
        elif mirror[rel] != ob:
            problems.append('overlay file mismatched in mirror: %s' % rel)

    new_files = sorted(r for r in overlay if r not in up)
    print('  upstream %d | mirror %d | identical to upstream %d'
          % (len(up), len(mirror), identical))
    print('  overlay %d file(s): %d patch upstream, %d ours alone'
          % (len(overlay), patched, len(new_files)))
    if drops:
        print('  intentionally dropped: %s' % ', '.join(sorted(drops)))
    print('  files we PATCH (review these on every re-merge):')
    for r in sorted(r for r in overlay if r in up):
        print('     %s' % r)

    if problems:
        print('\n  *** %d PROBLEM(S) ***' % len(problems))
        for p in problems[:40]:
            print('     %s' % p)
    else:
        print('\n  CLEAN: mirror == clean upstream + our overlay, exactly')
    return problems


def main():
    args = sys.argv[1:]
    piers = '--piers' in args
    wanted = [a for a in args if not a.startswith('--')]
    roots = ('overlays-piers',) if piers else ('overlays',)
    overlays = []
    for root in roots:
        d = os.path.join(REPO, root)
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            p = os.path.join(d, name)
            if os.path.exists(os.path.join(p, 'base.json')):
                if not wanted or name in wanted:
                    overlays.append(p)
    if not overlays:
        print('no matching overlay found')
        return 1
    bad = []
    for o in overlays:
        bad += verify(o)
    print('\n' + '=' * 68)
    print('TOTAL PROBLEMS: %d' % len(bad))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
