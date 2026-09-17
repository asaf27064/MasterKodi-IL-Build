#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mirror clean overlay bases into our own 'overlay-bases' GitHub release.

WHY: some upstreams keep only their latest zip. Nanomani deleted Rounded 1.3.00
(2026-09-05) and then 1.3.4 (2026-09-11); each time base_zip_url 404'd, every
build-and-release run failed, and nothing -- not even unrelated dep fixes --
reached the fleet until someone re-based by hand. Rounded's base is 73 MB, too
big to commit like the gears/POV bases.

So: for every base.json that declares `base_zip_mirror_url`, make sure the
mirror holds the CURRENT base_version. It is uploaded while upstream still
serves it (right after a base bump, in CI), and check_upstream.fetch_base falls
back to the mirror once upstream prunes it.

Idempotent and cheap when nothing is missing (one HEAD per overlay). Never
fails the caller: a mirror hiccup must not block a build that upstream can
still satisfy, so problems are reported and the exit code stays 0 unless
--strict.

Usage: mirror_bases.py [overlays_root ...] [--strict]
"""
import glob
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_upstream as cu  # noqa: E402

RELEASE = 'overlay-bases'


def _exists(name):
    """Ask the API, not the download URL: a freshly uploaded asset 404s on the
    CDN for a minute or so, and a HEAD check then re-uploads 73 MB for nothing."""
    r = _gh('release', 'view', RELEASE, '--json', 'assets', '-q', '.assets[].name')
    return r.returncode == 0 and name in r.stdout.split()


def _gh(*args):
    return subprocess.run(['gh'] + list(args), capture_output=True, text=True)


def _ensure_release():
    if _gh('release', 'view', RELEASE).returncode == 0:
        return True
    r = _gh('release', 'create', RELEASE, '--title', 'Overlay bases (mirror)',
            '--notes', 'Clean upstream base zips for overlays whose upstream deletes '
                       'old versions. Written by tools/mirror_bases.py; read by '
                       'check_upstream.fetch_base as a fallback. Do not edit by hand.')
    if r.returncode != 0:
        print('[mirror_bases] cannot create release %s: %s' % (RELEASE, r.stderr.strip()))
        return False
    return True


def mirror_one(base_json):
    overlay_dir = os.path.dirname(base_json)
    base = json.load(open(base_json, encoding='utf-8'))
    tmpl = base.get('base_zip_mirror_url')
    if not tmpl:
        return None
    ver = base['base_version']
    url = tmpl.format(version=ver)
    name = url.rsplit('/', 1)[-1]
    if _exists(name):
        print('[mirror_bases] %s: mirrored (%s)' % (base['addon_id'], name))
        return True
    local = base.get('base_zip_local')
    lp = os.path.join(overlay_dir, local) if local else None
    if lp and os.path.isfile(lp):
        data = open(lp, 'rb').read()
    else:
        # upstream ONLY -- never re-upload from the mirror we are filling
        data = cu._get(base['base_zip_url'].format(version=cu.url_version(base, ver)))
    if not _ensure_release():
        return False
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, name)
        open(p, 'wb').write(data)
        r = _gh('release', 'upload', RELEASE, p, '--clobber')
    if r.returncode != 0:
        print('[mirror_bases] %s: upload FAILED: %s' % (base['addon_id'], r.stderr.strip()))
        return False
    print('[mirror_bases] %s: uploaded %s (%d bytes)' % (base['addon_id'], name, len(data)))
    return True


def main(argv):
    strict = '--strict' in argv
    roots = [a for a in argv if not a.startswith('--')] or ['overlays', 'overlays-piers']
    bad = 0
    for root in roots:
        for bj in sorted(glob.glob(os.path.join(root, '*', 'base.json'))):
            try:
                if mirror_one(bj) is False:
                    bad += 1
            except Exception as e:
                bad += 1
                print('[mirror_bases] %s: ERROR %s' % (bj, e))
    return 1 if (strict and bad) else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
