#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Adopt a new upstream version into an overlay -- in one command.

Single overlay:
    python tools/adopt_upstream.py overlays/plugin.video.gears [--version 2.3.4] [--force] [--yes]

All overlays, SAFE ones only (used by CI auto-adopt):
    python tools/adopt_upstream.py --all-safe [--yes]

What adopting does, per overlay:
  1. Determine the target version (--version, else upstream latest).
  2. Classify the jump (same baseline-diff as the watcher). If any file our
     overlay REPLACES changed upstream it's a MANUAL merge -> skipped in
     --all-safe; aborted for a single overlay unless --force.
  3. For a committed-base overlay (gears: has base_zip_local), download the new
     clean upstream zip, store it as base/<id>-<version>.zip, delete the old
     one, point base_zip_local at it. (unhingedthemes deletes old zips, so we
     keep our own clean copy of the version we ship -- exactly one.)
     For a live-base overlay (AF3), nothing is stored; we just bump base_version.
  4. Bump base_version in base.json.
  5. (single-overlay only, unless --no-build) reconstruct the merged addon so
     you can smoke-test before pushing.

It does NOT commit or push.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_upstream as cu  # noqa: E402
import apply_overlay as ao   # noqa: E402


def adopt(overlay_dir, target=None, force=False, do_build=False, out='addons'):
    """Adopt `target` (or latest) into one overlay. Returns:
    'adopted' | 'up-to-date' | 'manual' | 'error'."""
    bpath = os.path.join(overlay_dir, 'base.json')
    base = json.load(open(bpath, encoding='utf-8'))
    aid = base['addon_id']

    res = cu.check_one(overlay_dir, target=target)
    if res.get('error'):
        print('%s: ERROR %s' % (aid, res['error']))
        return 'error'
    tgt = res['latest']
    if not res['has_update']:
        print('%s: already at %s (target %s)' % (aid, base['base_version'], tgt))
        return 'up-to-date'

    print('%s: %s -> %s' % (aid, res['current'], tgt))
    if res['manual']:
        print('  MANUAL conflicts (overlaid files changed upstream):')
        for rel, why in res['manual']:
            print('    - %s (%s)' % (rel, why))
        if not force:
            print('  -> skipped (re-merge those into %s/files/ then adopt).' % overlay_dir)
            return 'manual'
        print('  --force: adopting anyway.')
    else:
        print('  SAFE: no overlaid file changed upstream.')

    # swap committed clean base if this overlay keeps one
    if base.get('base_zip_local'):
        up_fmt = base.get('upstream_zip_url') or base['base_zip_url']
        url = up_fmt.format(version=tgt)
        print('  downloading new clean base: %s' % url)
        data = cu._get(url)
        new_rel = ('base/%s-%s.zip' % (aid, tgt))
        new_abs = os.path.join(overlay_dir, new_rel)
        os.makedirs(os.path.dirname(new_abs), exist_ok=True)
        with open(new_abs, 'wb') as fh:
            fh.write(data)
        old_rel = base.get('base_zip_local')
        if old_rel and old_rel != new_rel:
            old_abs = os.path.join(overlay_dir, old_rel)
            if os.path.isfile(old_abs):
                os.remove(old_abs)
                print('  removed old base: %s' % old_rel)
        base['base_zip_local'] = new_rel

    base['base_version'] = tgt
    with open(bpath, 'w', encoding='utf-8') as fh:
        json.dump(base, fh, indent=2, ensure_ascii=False)
        fh.write('\n')
    print('  base.json -> base_version=%s' % tgt)

    if do_build:
        print('  reconstructing merged addon into %s/ ...' % out)
        ao.build_one(overlay_dir, out)
    return 'adopted'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('overlay_dir', nargs='?', help='a single overlays/<id> dir')
    ap.add_argument('--all-safe', action='store_true',
                    help='iterate overlays/ and adopt every SAFE update (skip MANUAL)')
    ap.add_argument('--overlays-root', default='overlays')
    ap.add_argument('--version', default=None)
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--yes', action='store_true')
    ap.add_argument('--no-build', action='store_true', help='do not reconstruct after adopting')
    ap.add_argument('--out', default='addons')
    args = ap.parse_args()

    if args.all_safe:
        adopted = []
        for name in sorted(os.listdir(args.overlays_root)):
            odir = os.path.join(args.overlays_root, name)
            if not os.path.isfile(os.path.join(odir, 'base.json')):
                continue
            status = adopt(odir, target=None, force=False,
                           do_build=not args.no_build, out=args.out)
            if status == 'adopted':
                adopted.append(name)
        print('\nadopted: %s' % (', '.join(adopted) if adopted else '(none)'))
        # machine flag for CI
        gh = os.environ.get('GITHUB_OUTPUT')
        if gh:
            with open(gh, 'a', encoding='utf-8') as fh:
                fh.write('adopted=%s\n' % ('true' if adopted else 'false'))
                fh.write('adopted_list=%s\n' % (', '.join(adopted)))
        return 0

    if not args.overlay_dir:
        ap.error('provide an overlay dir, or use --all-safe')

    if not args.yes:
        # preview first
        res = cu.check_one(args.overlay_dir, target=args.version)
        if res.get('has_update'):
            print('%s: %s -> %s (%s)' % (res['addon_id'], res['current'], res['latest'],
                                         'MANUAL' if res['manual'] else 'SAFE'))
        try:
            ans = input('Proceed? [y/N] ').strip().lower()
        except EOFError:
            ans = 'n'
        if ans not in ('y', 'yes'):
            print('Cancelled.')
            return 1

    status = adopt(args.overlay_dir, target=args.version, force=args.force,
                   do_build=not args.no_build, out=args.out)
    if status == 'adopted':
        print('Done. Smoke-test it, then commit + push (CI rebuilds + ships).')
        return 0
    return 1 if status in ('manual', 'error') else 0


if __name__ == '__main__':
    raise SystemExit(main())
