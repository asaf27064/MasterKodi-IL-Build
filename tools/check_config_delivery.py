#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fail the build if the shipped CONFIG carries a file that config_policy.json
never delivers.

Why this exists (found on-device 2026-07-30): the AF3 home widgets in
config/.../skin.arctic.fuse.3/ were the TMDb-based set (Asaf's python-crash fix),
but `skinvariables-shortcut-1101widgets.json` + `-homewidgets.json` were missing
from config_policy's file list. The skin BUNDLE ships its own older, Gears-based
copies of those files, so a fresh AF3 install extracted the bundle's versions and
the config never overwrote them -- silently restoring the configuration we had
deliberately moved away from. Nothing failed, nothing logged; the widgets were
just wrong.

A config file that isn't in the policy is dead weight at best and a silent
regression at worst, so it must be either delivered or deleted.

Scope: the addon_data trees where a bundle also ships copies (skinvariables nodes,
skinshortcuts). Extend SCAN_GLOBS as needed.

Usage:  python tools/check_config_delivery.py [repo_root]
"""
import glob
import json
import os
import sys

SCAN_GLOBS = (
    'config/userdata/addon_data/script.skinvariables/nodes/**/*.json',
    'config/userdata/addon_data/script.skinshortcuts/**/*.DATA.xml',
)


def _delivered(policy):
    exact, prefixes = set(), []
    for e in policy.get('files', []):
        d = e.get('dest')
        if d:
            exact.add(d.replace('\\', '/'))
    for e in policy.get('dirs', []):
        dd = (e.get('dest_dir') or '').replace('\\', '/').rstrip('/')
        if dd:
            prefixes.append(dd + '/')
    return exact, prefixes


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else '.'
    pol = os.path.join(root, 'config', 'config_policy.json')
    if not os.path.isfile(pol):
        print('[check_config_delivery] no config_policy.json -- nothing to check')
        return 0
    with open(pol, encoding='utf-8') as fh:
        policy = json.load(fh)
    exact, prefixes = _delivered(policy)

    missing = []
    for pattern in SCAN_GLOBS:
        for path in glob.glob(os.path.join(root, pattern), recursive=True):
            rel = os.path.relpath(path, root).replace(os.sep, '/')
            rel_in_config = rel.split('config/', 1)[1] if rel.startswith('config/') else rel
            if rel_in_config in exact:
                continue
            if any(rel_in_config.startswith(p) for p in prefixes):
                continue
            missing.append(rel_in_config)

    if missing:
        print('CONFIG DELIVERY GAP -- these config files are never applied by '
              'config_policy.json, so a bundle\'s stale copy wins on install:',
              file=sys.stderr)
        for m in sorted(missing):
            print('  ' + m, file=sys.stderr)
        print('Fix: add a files[] entry (fresh/update: replace) for each, or '
              'delete it from config/ if it is genuinely unused.', file=sys.stderr)
        return 1
    print('[check_config_delivery] clean: every scanned config file is delivered')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
