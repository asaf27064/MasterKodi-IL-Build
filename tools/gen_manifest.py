#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate manifest.json from the built zips in dist/, and compute what changed
versus the previously committed manifest.

manifest.json is the single source of truth the client updater reads. Each addon
entry carries id, version, channel (core|optional), zip name, sha256, size, and
the release download URL. The client compares its installed version+hash against
this and pulls only what differs.

Outputs:
  manifest.json      (committed to main)
  dist/changed.txt   (newline list of zip filenames whose sha256 changed - the
                      only assets CI needs to (re)upload)

Usage: python tools/gen_manifest.py [--repo-root .] [--dist dist]

Fails (exit 3) if any zip is missing a sha256 - never publish an unverifiable build.
"""

import argparse
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import sha256_file, read_addon_meta


def load_build_cfg(repo_root):
    with open(os.path.join(repo_root, 'build.json'), encoding='utf-8') as fh:
        return json.load(fh)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo-root', default='.')
    ap.add_argument('--dist', default='dist')
    args = ap.parse_args()

    repo_root = args.repo_root
    cfg = load_build_cfg(repo_root)
    repo = cfg['repo']
    tag = cfg['release_tag']
    optional = set(cfg.get('channels', {}).get('optional', []))
    base_url = 'https://github.com/%s/releases/download/%s/' % (repo, tag)

    dist_dir = os.path.join(repo_root, args.dist)
    addons_root = os.path.join(repo_root, 'addons')

    # Map addon id -> version from source (authoritative), so we name the right zip.
    id_to_ver = {}
    for name in sorted(os.listdir(addons_root)):
        d = os.path.join(addons_root, name)
        if not os.path.isdir(d):
            continue
        aid, ver = read_addon_meta(d)
        if aid and ver:
            id_to_ver[aid] = ver

    addons = []
    missing = []
    for aid, ver in sorted(id_to_ver.items()):
        zip_name = '%s-%s.zip' % (aid, ver)
        zpath = os.path.join(dist_dir, zip_name)
        if not os.path.isfile(zpath):
            missing.append(zip_name)
            continue
        sha = sha256_file(zpath)
        addons.append({
            'id': aid,
            'version': ver,
            'channel': 'optional' if aid in optional else 'core',
            'zip': zip_name,
            'sha256': sha,
            'size': os.path.getsize(zpath),
            'url': base_url + zip_name,
        })

    if missing:
        print('ERROR: built zips missing for: %s' % ', '.join(missing), file=sys.stderr)
        return 3

    manifest = {
        'build': cfg.get('build_name', 'MasterKodi IL'),
        'brand': cfg.get('brand', 'MasterKodi'),
        'schema': cfg.get('manifest_schema', 1),
        'generated_utc': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'repo': repo,
        'release_tag': tag,
        'base_url': base_url,
        'addons': addons,
    }

    # config zip (optional)
    cfg_ver = str(cfg.get('config_version', 1))
    cfg_zip = os.path.join(dist_dir, 'config-%s.zip' % cfg_ver)
    if os.path.isfile(cfg_zip):
        manifest['config'] = {
            'version': cfg_ver,
            'zip': 'config-%s.zip' % cfg_ver,
            'sha256': sha256_file(cfg_zip),
            'size': os.path.getsize(cfg_zip),
            'url': base_url + 'config-%s.zip' % cfg_ver,
        }

    # diff against previously committed manifest to find changed assets
    old = _load_old_manifest(repo_root)
    old_sha = {a['zip']: a['sha256'] for a in old.get('addons', [])}
    if 'config' in old:
        old_sha[old['config']['zip']] = old['config']['sha256']

    changed = []
    for a in addons:
        if old_sha.get(a['zip']) != a['sha256']:
            changed.append(a['zip'])
    if 'config' in manifest and old_sha.get(manifest['config']['zip']) != manifest['config']['sha256']:
        changed.append(manifest['config']['zip'])

    with open(os.path.join(repo_root, 'manifest.json'), 'w', encoding='utf-8') as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)
        fh.write('\n')
    with open(os.path.join(dist_dir, 'changed.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(changed) + ('\n' if changed else ''))
    # also emit the full list of valid asset names for orphan pruning
    valid = [a['zip'] for a in addons]
    if 'config' in manifest:
        valid.append(manifest['config']['zip'])
    with open(os.path.join(dist_dir, 'valid_assets.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(sorted(valid)) + '\n')

    print('manifest.json: %d addons (%d core, %d optional)' % (
        len(addons),
        sum(1 for a in addons if a['channel'] == 'core'),
        sum(1 for a in addons if a['channel'] == 'optional')))
    print('changed assets this run: %d' % len(changed))
    for z in changed:
        print('  + %s' % z)
    return 0


def _load_old_manifest(repo_root):
    p = os.path.join(repo_root, 'manifest.json')
    if os.path.isfile(p):
        try:
            with open(p, encoding='utf-8') as fh:
                return json.load(fh)
        except Exception:
            pass
    return {}


if __name__ == '__main__':
    raise SystemExit(main())
