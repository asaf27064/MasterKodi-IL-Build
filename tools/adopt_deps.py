#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Auto-adopt whitelisted PURE-UPSTREAM dep updates (CI, gated by AUTO_ADOPT).

Every dep we ship as a byte-clean upstream copy is auto-adopted — these are
exactly the addons Kodi itself would have auto-updated before the fleet
pinned them (Asaf, 2026-07-19). NOT auto-adopted, forever alert-only:
  - skin.nimbus       — our committed FORK; upstream would erase the Hebrew mod
  - script.skinhelper — ships with our PIL-requirement removal; needs re-merge

For each whitelisted dep: if upstream is newer, download the official zip,
verify it contains <id>/addon.xml at the expected version, replace the tree
under addons/, and report. The PIERS-pinned replacements in build.json get
the same treatment (version+url bump after validating the new zip). The
workflow commits; build-and-release ships it to both fleets via the manifests.

Usage: python tools/adopt_deps.py [--dry-run]
Outputs (GITHUB_OUTPUT): adopted=true|false, adopted_list=<csv>
"""

import io
import json
import os
import re
import shutil
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_upstream import _get, _dep_latest, _dep_current, _semver  # noqa: E402

_JURIAL = 'https://raw.githubusercontent.com/jurialmunkey/repository.jurialmunkey/master/omega/zips'

# id -> (latest-version source url, source kind, zip url template)
AUTO_DEPS = {
    'script.module.gearsscrapers': (
        'https://raw.githubusercontent.com/unhingedthemes/zips/main/_zips/addons.xml', 'addons_xml',
        'https://raw.githubusercontent.com/unhingedthemes/zips/main/_zips/{id}/{id}-{ver}.zip'),
    'script.module.cocoscrapers': (
        'https://raw.githubusercontent.com/not-coco-joe/repository.cocoscrapers/master/zips/addons.xml', 'addons_xml',
        'https://raw.githubusercontent.com/not-coco-joe/repository.cocoscrapers/master/zips/{id}/{id}-{ver}.zip'),
    'plugin.video.themoviedb.helper': (
        _JURIAL + '/addons.xml', 'addons_xml', _JURIAL + '/{id}/{id}-{ver}.zip'),
    'script.skinvariables': (
        _JURIAL + '/addons.xml', 'addons_xml', _JURIAL + '/{id}/{id}-{ver}.zip'),
    'script.module.jurialmunkey': (
        _JURIAL + '/addons.xml', 'addons_xml', _JURIAL + '/{id}/{id}-{ver}.zip'),
    'script.module.infotagger': (
        _JURIAL + '/addons.xml', 'addons_xml', _JURIAL + '/{id}/{id}-{ver}.zip'),
    'script.skinshortcuts': (
        'https://mirrors.kodi.tv/addons/omega/script.skinshortcuts/', 'kodi_dir',
        'https://mirrors.kodi.tv/addons/omega/{id}/{id}-{ver}.zip'),
}


def adopt_one(aid, src_url, kind, zip_tpl, dry=False):
    cur = _dep_current('addons', aid)
    if not cur:
        print('%s: not in addons/, skipped' % aid)
        return False
    latest = _dep_latest(aid, src_url, kind)
    if not latest or _semver(latest) <= _semver(cur):
        print('%s: up to date (%s)' % (aid, cur))
        return False
    print('%s: %s -> %s' % (aid, cur, latest))
    if dry:
        return False
    blob = _get(zip_tpl.format(id=aid, ver=latest))
    zf = zipfile.ZipFile(io.BytesIO(blob))
    xml = zf.read('%s/addon.xml' % aid).decode('utf-8', 'replace')
    m = re.search(r'<addon[^>]*?version="([0-9.]+)"', xml, re.S)
    if not m or m.group(1) != latest:
        raise SystemExit('%s: zip version mismatch (%s vs %s)' % (aid, m and m.group(1), latest))
    target = os.path.join('addons', aid)
    shutil.rmtree(target)
    zf.extractall('addons')
    print('%s: adopted %s (official zip, verbatim)' % (aid, latest))
    return True


def adopt_piers_replacements(dry=False):
    """Bump build.json piers.replacements to the newest kodi-piers zip after
    validating the new zip really carries that version."""
    adopted = []
    try:
        with open('build.json', encoding='utf-8') as fh:
            cfg = json.load(fh)
        reps = cfg.get('piers', {}).get('replacements', {})
    except Exception as e:
        print('piers replacements: read error - %s' % e)
        return adopted
    changed = False
    for rid, rep in reps.items():
        try:
            cur = rep.get('version')
            base = 'https://mirrors.kodi.tv/addons/piers/%s/' % rid
            latest = _dep_latest(rid, base, 'kodi_dir')
            if not latest or _semver(latest) <= _semver(cur):
                print('%s (piers pin): up to date (%s)' % (rid, cur))
                continue
            print('%s (piers pin): %s -> %s' % (rid, cur, latest))
            if dry:
                continue
            url = base + '%s-%s.zip' % (rid, latest)
            zf = zipfile.ZipFile(io.BytesIO(_get(url)))
            xml = zf.read('%s/addon.xml' % rid).decode('utf-8', 'replace')
            m = re.search(r'<addon[^>]*?version="([0-9.]+)"', xml, re.S)
            if not m or m.group(1) != latest:
                print('%s (piers pin): zip version mismatch, skipped' % rid)
                continue
            rep['version'] = latest
            rep['url'] = url
            changed = True
            adopted.append('%s(piers)' % rid)
            print('%s (piers pin): adopted %s' % (rid, latest))
        except Exception as e:
            print('%s (piers pin): error - %s' % (rid, e))
    if changed:
        with open('build.json', 'w', encoding='utf-8') as fh:
            json.dump(cfg, fh, indent=2, ensure_ascii=False)
            fh.write('\n')
    return adopted


def main():
    dry = '--dry-run' in sys.argv
    adopted = []
    for aid, (src, kind, tpl) in AUTO_DEPS.items():
        try:
            if adopt_one(aid, src, kind, tpl, dry=dry):
                adopted.append(aid)
        except SystemExit:
            raise
        except Exception as e:
            print('%s: error - %s' % (aid, e))
    adopted += adopt_piers_replacements(dry=dry)
    out = os.environ.get('GITHUB_OUTPUT')
    if out:
        with open(out, 'a', encoding='utf-8') as fh:
            fh.write('adopted=%s\n' % ('true' if adopted else 'false'))
            fh.write('adopted_list=%s\n' % ','.join(adopted))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
