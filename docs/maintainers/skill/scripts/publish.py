#!/usr/bin/env python3
"""One-command publisher for the MasterKodi AI Subs (gearsai) add-on.

Does the whole "ship an AI Subs update" flow that used to be manual:
  1. read the version from the addon's addon.xml
  2. rebuild  gearsai_subtitles.zip  into the pov-modified-heb repo
     -- EXCLUDING cloudflare/ (maintainer-only), __pycache__, *.pyc
  3. bump gearsai_version.json "version" to match (+ optional --changelog)
  4. (optional) re-inject the addon into FenLight_Estuary.zip for the v1.0
     release (also cloudflare-excluded)

Deploys are OFF by default -- the script BUILDS + prints the exact git / gh
commands. Pass --push to commit+push the pov repo, --upload to push the
release zip. Both are explicit opt-ins (a bad push reaches every user).

Usage (from anywhere):
  python publish.py                         # build + bump, print deploy cmds
  python publish.py --changelog "added Podnapisi source"
  python publish.py --reinject              # also rebuild FenLight_Estuary.zip
  python publish.py --push --upload         # actually deploy

Paths are auto-detected but overridable:
  --addon    <service.subtitles.gearsai dir>
  --pov      <pov-modified-heb repo dir>
  --estuary  <FenLight_Estuary working tree>
"""

import argparse
import os
import re
import subprocess
import sys
import zipfile

ADDON_ID = 'service.subtitles.gearsai'
ZIP_NAME = 'gearsai_subtitles.zip'
VERSION_JSON = 'gearsai_version.json'

# Never ship these on-device.
EXCLUDE_DIRS = {'cloudflare', '__pycache__', '.git', '.github'}
EXCLUDE_EXTS = {'.pyc', '.pyo'}
EXCLUDE_NAMES = {'.DS_Store', 'Thumbs.db'}

CANDIDATE_ROOTS = [
    r'C:\Users\asaf2\Desktop\kodi',
    r'C:\Users\asaf2\Desktop\kodi_project',
    os.getcwd(),
]


def find_first(rel, must_contain=None):
    """Find <root>/<rel> across the candidate roots; if must_contain is given,
    require that file to exist inside it."""
    for root in CANDIDATE_ROOTS:
        p = os.path.join(root, rel)
        if os.path.exists(p) and (must_contain is None or os.path.exists(os.path.join(p, must_contain))):
            return p
    return None


def find_addon(explicit):
    if explicit:
        return explicit
    for root in CANDIDATE_ROOTS:
        p = os.path.join(root, 'FenLight_Estuary', 'addons', ADDON_ID)
        if os.path.exists(os.path.join(p, 'addon.xml')):
            return p
    return None


def find_pov(explicit):
    if explicit:
        return explicit
    # the live repo is the one that actually has gearsai_version.json AND is a git repo
    best = None
    for root in CANDIDATE_ROOTS:
        p = os.path.join(root, 'pov-modified-heb')
        if not os.path.isdir(p):
            continue
        has_json = os.path.exists(os.path.join(p, VERSION_JSON))
        is_git = os.path.isdir(os.path.join(p, '.git'))
        if has_json and is_git:
            return p
        if has_json and best is None:
            best = p
    return best


def read_addon_version(addon_dir):
    with open(os.path.join(addon_dir, 'addon.xml'), encoding='utf-8') as f:
        txt = f.read()
    m = re.search(r'id="%s"[^>]*?version="([^"]+)"' % re.escape(ADDON_ID), txt, re.S)
    if not m:
        m = re.search(r'version="([0-9][^"]*)"', txt)
    return m.group(1) if m else None


def build_zip(addon_dir, dst_zip):
    """Zip the addon as  <ADDON_ID>/...  (Kodi addon-zip layout), excluding
    maintainer-only + junk paths."""
    if os.path.exists(dst_zip):
        os.remove(dst_zip)
    parent = os.path.dirname(addon_dir)
    n = 0
    with zipfile.ZipFile(dst_zip, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(addon_dir):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            for fn in files:
                if fn in EXCLUDE_NAMES or os.path.splitext(fn)[1] in EXCLUDE_EXTS:
                    continue
                fp = os.path.join(root, fn)
                arc = os.path.relpath(fp, parent).replace(os.sep, '/')
                zf.write(fp, arc)
                n += 1
    return n


def assert_no_cloudflare(zip_path):
    with zipfile.ZipFile(zip_path) as zf:
        bad = [m for m in zf.namelist() if '/cloudflare/' in m or m.endswith('.pyc')]
    if bad:
        raise SystemExit('REFUSING TO SHIP: excluded files leaked into zip: %s' % bad[:3])


def bump_version_json(pov_dir, version, changelog):
    import json
    path = os.path.join(pov_dir, VERSION_JSON)
    data = {}
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    data['version'] = version
    data.setdefault('addon_id', ADDON_ID)
    data.setdefault('name', 'MasterKodi AI Subs')
    if changelog:
        data['changelog'] = changelog
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    return path


def run(cmd, cwd=None):
    print('  $ ' + ' '.join(cmd))
    return subprocess.call(cmd, cwd=cwd)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--addon')
    ap.add_argument('--pov')
    ap.add_argument('--estuary')
    ap.add_argument('--changelog', default='')
    ap.add_argument('--reinject', action='store_true', help='also rebuild FenLight_Estuary.zip')
    ap.add_argument('--push', action='store_true', help='git commit+push the pov repo')
    ap.add_argument('--upload', action='store_true', help='gh release upload the build zip')
    args = ap.parse_args()

    addon = find_addon(args.addon)
    if not addon:
        sys.exit('Could not locate the %s addon. Pass --addon.' % ADDON_ID)
    pov = find_pov(args.pov)
    if not pov:
        sys.exit('Could not locate the pov-modified-heb repo. Pass --pov.')

    version = read_addon_version(addon)
    if not version:
        sys.exit('Could not read version from addon.xml')

    print('addon  : %s' % addon)
    print('pov    : %s' % pov)
    print('version: %s' % version)

    dst_zip = os.path.join(pov, ZIP_NAME)
    n = build_zip(addon, dst_zip)
    assert_no_cloudflare(dst_zip)
    print('built  : %s  (%d files, %.0f KB, cloudflare/ excluded)' % (
        ZIP_NAME, n, os.path.getsize(dst_zip) / 1024))

    jpath = bump_version_json(pov, version, args.changelog)
    print('bumped : %s -> %s' % (os.path.basename(jpath), version))

    if args.reinject:
        estuary = args.estuary or find_first('FenLight_Estuary', 'addons')
        if not estuary:
            print('!! --reinject: FenLight_Estuary not found, skipping')
        else:
            here = os.path.dirname(os.path.abspath(__file__))
            rc = run([sys.executable, os.path.join(here, 'build_release_zips.py'),
                      '--root', os.path.dirname(estuary),
                      '--estuary', os.path.basename(estuary)])
            print('reinject FenLight_Estuary.zip: %s' % ('OK' if rc == 0 else 'FAILED'))

    # ---- deploy (explicit opt-in) ----
    print('\n=== deploy ===')
    commit_msg = 'AI Subs %s%s' % (version, (': ' + args.changelog) if args.changelog else '')
    if args.push:
        run(['git', 'add', ZIP_NAME, VERSION_JSON], cwd=pov)
        run(['git', 'commit', '-m', commit_msg], cwd=pov)
        run(['git', 'push'], cwd=pov)
    else:
        print('To publish the AI Subs update (existing users get it via the wizard):')
        print('  cd "%s"' % pov)
        print('  git add %s %s && git commit -m "%s" && git push' % (ZIP_NAME, VERSION_JSON, commit_msg))

    if args.reinject:
        if args.upload:
            estuary = args.estuary or find_first('FenLight_Estuary', 'addons')
            zip_path = os.path.join(os.path.dirname(estuary), 'FenLight_Estuary.zip')
            run(['gh', 'release', 'upload', 'v1.0', zip_path,
                 '-R', 'asaf27064/asaf27064.github.io', '--clobber'])
        else:
            print('\nTo refresh new-install bundle (v1.0 release):')
            print('  gh release upload v1.0 FenLight_Estuary.zip -R asaf27064/asaf27064.github.io --clobber')

    print('\nDone.')


if __name__ == '__main__':
    main()
