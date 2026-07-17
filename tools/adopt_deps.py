#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Auto-adopt whitelisted PURE-UPSTREAM dep updates (CI, gated by AUTO_ADOPT).

Only deps that are byte-clean upstream copies with no skin coupling are listed
here — currently the scraper packs, which update often and where freshness
matters (dead sources fixed upstream). Skin-critical deps (skinvariables,
skinshortcuts, tmdbhelper, skinhelper) are deliberately NOT auto-adopted:
their versions couple to skin behavior, so they stay alert-only + human.

For each whitelisted dep: if upstream is newer, download the official zip,
verify it contains <id>/addon.xml at the expected version, replace the tree
under addons/, and report. The workflow commits; build-and-release ships it
to both fleets via the manifests.

Usage: python tools/adopt_deps.py [--dry-run]
Outputs (GITHUB_OUTPUT): adopted=true|false, adopted_list=<csv>
"""

import io
import os
import re
import shutil
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_upstream import _get, _dep_latest, _dep_current, _semver  # noqa: E402

# id -> (latest-version source url, source kind, zip url template)
AUTO_DEPS = {
    'script.module.gearsscrapers': (
        'https://raw.githubusercontent.com/unhingedthemes/zips/main/_zips/addons.xml', 'addons_xml',
        'https://raw.githubusercontent.com/unhingedthemes/zips/main/_zips/{id}/{id}-{ver}.zip'),
    'script.module.cocoscrapers': (
        'https://raw.githubusercontent.com/not-coco-joe/repository.cocoscrapers/master/zips/addons.xml', 'addons_xml',
        'https://raw.githubusercontent.com/not-coco-joe/repository.cocoscrapers/master/zips/{id}/{id}-{ver}.zip'),
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
    out = os.environ.get('GITHUB_OUTPUT')
    if out:
        with open(out, 'a', encoding='utf-8') as fh:
            fh.write('adopted=%s\n' % ('true' if adopted else 'false'))
            fh.write('adopted_list=%s\n' % ','.join(adopted))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
