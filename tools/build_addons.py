#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build every addon under addons/ into a reproducible versioned zip in dist/.

Usage: python tools/build_addons.py [--repo-root .] [--out dist]

Each addons/<id>/ folder -> dist/<id>-<version>.zip (zip contains <id>/... at root).
Prints a line per addon. Skips folders without a valid addon.xml.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import build_reproducible_zip, read_addon_meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo-root', default='.')
    ap.add_argument('--out', default='dist')
    args = ap.parse_args()

    addons_root = os.path.join(args.repo_root, 'addons')
    out_dir = os.path.join(args.repo_root, args.out)
    os.makedirs(out_dir, exist_ok=True)

    if not os.path.isdir(addons_root):
        print('ERROR: no addons/ directory at %s' % addons_root, file=sys.stderr)
        return 2

    built, skipped = 0, 0
    for name in sorted(os.listdir(addons_root)):
        addon_dir = os.path.join(addons_root, name)
        if not os.path.isdir(addon_dir):
            continue
        aid, ver = read_addon_meta(addon_dir)
        if not aid or not ver:
            print('  skip (no addon.xml): %s' % name)
            skipped += 1
            continue
        if aid != name:
            print('  WARN: folder %s != addon id %s (using id)' % (name, aid))
        zip_name = '%s-%s.zip' % (aid, ver)
        out_path = os.path.join(out_dir, zip_name)
        sha, size = build_reproducible_zip(addon_dir, aid, out_path)
        print('  built %-45s %10s bytes  %s' % (zip_name, '{:,}'.format(size), sha[:12]))
        built += 1

    print('Done: %d built, %d skipped -> %s' % (built, skipped, out_dir))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
