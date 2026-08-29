#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Refresh VANILLA dependencies from their official repos.

Kodi auto-updates vanilla deps on user boxes (they keep their repo origin), but
our repo's committed copies go stale -- fresh installs then get an old version
until Kodi catches it up. This tool re-vendors the CLEAN upstream zip (exactly
as a user's Kodi would download it) into addons/<id>.

STRICTLY refuses to touch modded addons (the Hebrew work lives there; those are
updated only via their overlays). MODDED_ADDONS below is intentionally a SUPERSET
of modular_update.py's set -- it adds the wizard + repo (they self-update from
our own repo, so they need no Kodi auto-update pin, but must never be re-vendored
from upstream either). Never shrink it below modular_update.py's list.

Usage:
  refresh_vanilla_deps.py            # report: ours vs upstream-latest
  refresh_vanilla_deps.py --apply    # download clean zips + replace addons/<id>
  refresh_vanilla_deps.py --apply script.skinvariables   # only these ids
"""
import io
import os
import re
import shutil
import sys
import zipfile
from urllib.request import urlopen, Request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDONS = os.path.join(ROOT, 'addons')

# NEVER refresh these -- they carry our modifications (overlay-managed or ours).
MODDED_ADDONS = {
    'plugin.video.gears', 'plugin.video.pov',
    'skin.estuary', 'skin.nimbus', 'skin.arctic.fuse.3',
    'skin.arctic.zephyr.2.resurrection.mod',
    # overlaid 2026-08-29 (upstream's duplicate buildxml call that killed every
    # widget, + the NotoSans font pin). It WAS in VANILLA_DEPS below; leaving it
    # there would have rmtree'd the addon and extracted clean upstream over the
    # overlay on the next --apply.
    'skin.arctic.zephyr.rounded',
    'script.skinhelper',
    'script.module.gearsscrapers', 'service.subtitles.gearsai',
    'service.masterkodi.skipintro', 'service.kodi.il.firstrun',
    'plugin.program.masterkodi.il.wizard', 'repository.masterkodi.il',
}

# his repo has two datadirs; skinvariables/tmdbhelper/etc live in nexusrepo
JURIAL_DIRS = (
    'https://raw.githubusercontent.com/jurialmunkey/repository.jurialmunkey/master/nexusrepo/zips',
    'https://raw.githubusercontent.com/jurialmunkey/repository.jurialmunkey/master/repo/zips',
)
KODI = 'https://mirrors.kodi.tv/addons/omega'
# Third-party skin repos. skin.bingie is vendored UNMODIFIED, so it is an
# ordinary vanilla dep: we re-vendor the clean upstream zip and ship it
# through our own manifest, as the wizard-controls-updates invariant
# requires. skin.arctic.zephyr.rounded is NO LONGER vanilla (overlaid
# 2026-08-29) -- it moved to MODDED_ADDONS above and is updated by a
# 3-way overlay re-merge instead. NANOMANI stays: it is still the base
# zip source in overlays/skin.arctic.zephyr.rounded/base.json.
NANOMANI = 'https://raw.githubusercontent.com/Nanomani/repository.omega.nanomani/main'
BINGIE = 'https://raw.githubusercontent.com/matke-84/repository.bingie/main/omega'

# id -> source. 'jurialmunkey' = his repo datadir; 'kodi' = official Kodi mirror.
VANILLA_DEPS = {
    'script.skinvariables': 'jurialmunkey',
    'plugin.video.themoviedb.helper': 'jurialmunkey',
    'script.module.jurialmunkey': 'jurialmunkey',
    'script.module.infotagger': 'jurialmunkey',
    'script.skinshortcuts': 'kodi',
    'script.module.simplejson': 'kodi',
    'script.module.unidecode': 'kodi',
    'script.module.simpleeval': 'kodi',
    'resource.images.studios.white': 'kodi',
    'resource.images.studios.coloured': 'kodi',
    'resource.images.moviegenreicons.transparent': 'kodi',
    'resource.images.moviecountryicons.maps': 'kodi',
    'resource.images.weathericons.white': 'kodi',
    'skin.bingie': 'bingie',
}


def log(msg):
    print('[refresh_deps] %s' % msg)


def http(url):
    req = Request(url, headers={'User-Agent': 'MasterKodi-IL-build'})
    with urlopen(req, timeout=60) as r:
        return r.read()


def local_version(aid):
    p = os.path.join(ADDONS, aid, 'addon.xml')
    if not os.path.exists(p):
        return None
    t = io.open(p, encoding='utf-8', errors='replace').read()
    m = re.search(r'<addon[^>]*\sversion="([^"]+)"', t, re.S)
    return m.group(1) if m else None


_jurial_index = {}


def jurial_latest(aid):
    for base in JURIAL_DIRS:
        if base not in _jurial_index:
            _jurial_index[base] = http(base + '/addons.xml').decode('utf-8', 'replace')
        # attribute order varies; grab the whole tag, then its version
        m = re.search(r'<addon\s[^>]*id="%s"[^>]*>' % re.escape(aid), _jurial_index[base])
        if m:
            vm = re.search(r'version="([^"]+)"', m.group(0))
            if vm:
                return vm.group(1), '%s/%s/%s-%s.zip' % (base, aid, aid, vm.group(1))
    return None, None


_simple_index = {}


def simple_latest(aid, base):
    """Repo layout: <base>/addons.xml plus <base>/<id>/<id>-<ver>.zip."""
    if base not in _simple_index:
        _simple_index[base] = http(base + '/addons.xml').decode('utf-8', 'replace')
    m = re.search(r'<addon\s[^>]*id="%s"[^>]*>' % re.escape(aid), _simple_index[base])
    if not m:
        return None, None
    vm = re.search(r'version="([^"]+)"', m.group(0))
    if not vm:
        return None, None
    return vm.group(1), '%s/%s/%s-%s.zip' % (base, aid, aid, vm.group(1))


def kodi_latest(aid):
    idx = http('%s/%s/' % (KODI, aid)).decode('utf-8', 'replace')
    vers = re.findall(re.escape(aid) + r'-([0-9][^"<]*?)\.zip', idx)
    if not vers:
        return None, None

    def key(v):
        return [int(x) if x.isdigit() else x
                for x in re.split(r'[.+~-]', v.replace('matrix.', 'matrix'))
                if x]
    try:
        ver = sorted(set(vers), key=key)[-1]
    except TypeError:
        ver = sorted(set(vers))[-1]
    return ver, '%s/%s/%s-%s.zip' % (KODI, aid, aid, ver)


def vnewer(a, b):
    def parts(v):
        return [int(x) for x in re.findall(r'\d+', v)]
    return parts(a) > parts(b)


def refresh(aid, url, expect_ver):
    data = http(url)
    z = zipfile.ZipFile(io.BytesIO(data))
    names = z.namelist()
    if not any(n.startswith(aid + '/') for n in names):
        raise Exception('zip root is not %s/' % aid)
    ax = z.read(aid + '/addon.xml').decode('utf-8', 'replace')
    got = re.search(r'<addon[^>]*\sversion="([^"]+)"', ax, re.S).group(1)
    if got != expect_ver:
        raise Exception('zip version %s != expected %s' % (got, expect_ver))
    dest = os.path.join(ADDONS, aid)
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    z.extractall(ADDONS)
    log('  refreshed %s -> %s (clean from %s)' % (aid, got, url.split('/')[2]))


def main():
    apply_mode = '--apply' in sys.argv
    only = [a for a in sys.argv[1:] if not a.startswith('--')]
    changed = []
    for aid, src in VANILLA_DEPS.items():
        if only and aid not in only:
            continue
        assert aid not in MODDED_ADDONS, 'refusing to touch modded addon %s' % aid
        ours = local_version(aid)
        try:
            if src == 'jurialmunkey':
                latest, url = jurial_latest(aid)
            elif src == 'nanomani':
                latest, url = simple_latest(aid, NANOMANI)
            elif src == 'bingie':
                latest, url = simple_latest(aid, BINGIE)
            else:
                latest, url = kodi_latest(aid)
        except Exception as e:
            log('%s: source check failed: %s' % (aid, e))
            continue
        if not latest:
            log('%s: not found at source (%s)' % (aid, src))
            continue
        if ours and not vnewer(latest, ours):
            log('%s: up to date (%s)' % (aid, ours))
            continue
        log('%s: ours=%s upstream=%s  *** STALE ***' % (aid, ours, latest))
        if apply_mode:
            refresh(aid, url, latest)
            changed.append('%s %s->%s' % (aid, ours, latest))
    if apply_mode:
        log('applied: %s' % (', '.join(changed) if changed else 'nothing'))
    else:
        log('report only (use --apply to refresh)')


if __name__ == '__main__':
    main()
