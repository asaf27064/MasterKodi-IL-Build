#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Package the clean default config tree (config/userdata/) into a reproducible
config-<version>.zip in dist/.

The config holds the build's default userdata: favourites, sources.xml, view DBs,
default addon_data (settings scrubbed of secrets). The wizard applies it on first
install and on config-version bumps.

Usage: python tools/build_config.py [--repo-root .] [--out dist]
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import sha256_file


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo-root', default='.')
    ap.add_argument('--out', default='dist')
    args = ap.parse_args()

    with open(os.path.join(args.repo_root, 'build.json'), encoding='utf-8') as fh:
        cfg = json.load(fh)
    version = str(cfg.get('config_version', 1))

    config_root = os.path.join(args.repo_root, 'config')
    if not os.path.isdir(os.path.join(config_root, 'userdata')):
        print('  no config/userdata/ -> skipping config zip')
        return 0

    out_dir = os.path.join(args.repo_root, args.out)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, 'config-%s.zip' % version)

    # zip config/ so entries are userdata/... (extracts under Kodi home)
    _rebuild_config_zip(config_root, out_path)
    sha, size = sha256_file(out_path), os.path.getsize(out_path)
    print('  built config-%s.zip  %s bytes  %s' % (version, '{:,}'.format(size), sha[:12]))
    return 0


def _rebuild_config_zip(config_root, out_path):
    import zipfile
    from common import iter_addon_files, FIXED_DATE, EXCLUDE_DIRS, EXCLUDE_NAMES, EXCLUDE_EXTS
    entries = []
    for root, dirs, files in os.walk(config_root):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        # never ship runtime databases in the config (regenerated on-device;
        # they also carry per-user tokens like trakt.secret) -> defaults come
        # from each addon's baked settings instead.
        dirs[:] = [d for d in dirs if d.lower() != 'database']
        for fn in sorted(files):
            if fn in EXCLUDE_NAMES or os.path.splitext(fn)[1] in EXCLUDE_EXTS:
                continue
            if fn.lower().endswith('.db'):
                continue
            abspath = os.path.join(root, fn)
            arc = os.path.relpath(abspath, config_root).replace(os.sep, '/')
            entries.append((abspath, arc))
    entries.sort(key=lambda x: x[1])
    with zipfile.ZipFile(out_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for abspath, arc in entries:
            with open(abspath, 'rb') as fh:
                data = fh.read()
            info = zipfile.ZipInfo(arc, date_time=FIXED_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, data)


if __name__ == '__main__':
    raise SystemExit(main())
