#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Auto-build the monolithic install bundles from the freshly built addon zips.

The fast one-zip install experience (download once, extract, done) used to come
from HAND-BUILT snapshots that aged (the base zip shipped gears 2.3.4 while the
fleet was on 2.3.6). This tool derives the bundles from the SAME dist/ zips CI
just built + the committed manifest, so they can never go stale again:

  * repack bundles (FenLight_Estuary / Arctic_Fuse_Skin / nimbus): start from
    the previous published bundle (its userdata skeleton, DB seeds and media/
    are the proven install base), REPLACE every addons/<id>/ tree with the
    freshly built one, and regenerate the wizard state-seed
    (applied_manifest.json) from the manifest's sha256 values so a fresh
    install's first update pass downloads ~nothing.
  * fresh bundles (Zephyr_Skin.zip): built purely from an addon list -- skin +
    its dependency closure. No userdata: config-apply delivers that on install.

Bundle definitions live in build.json under "bundles". A committed
bundles_state.json fingerprints each bundle's inputs; only changed bundles are
rebuilt/uploaded (they are big). Outputs:
  <out>/<bundle>.zip            the rebuilt bundles (changed ones only)
  <out>/bundles_changed.txt     names CI should upload
  bundles_state.json            refreshed fingerprints (CI commits it)

Usage:
  build_bundles.py [--repo-root .] [--dist dist] [--originals originals]
                   [--out dist/bundles] [--force]
"""

import argparse
import hashlib
import io
import json
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import sha256_file  # noqa: E402

STATE_FILE = 'bundles_state.json'
SEED_PATH = 'userdata/addon_data/plugin.program.masterkodi.il.wizard/applied_manifest.json'


def log(msg):
    print('[build_bundles] %s' % msg)


def load_json(path, default=None):
    try:
        with open(path, encoding='utf-8') as fh:
            return json.load(fh)
    except Exception:
        return default if default is not None else {}


def manifest_shas(repo_root):
    m = load_json(os.path.join(repo_root, 'manifest.json'))
    return {a['id']: a['sha256'] for a in m.get('addons', [])}


def dist_zip(dist_dir, aid, shas_by_id, manifest):
    """Path of the freshly built zip for addon id (named <id>-<ver>.zip)."""
    for a in manifest.get('addons', []):
        if a['id'] == aid:
            p = os.path.join(dist_dir, a['zip'])
            return p if os.path.isfile(p) else None
    return None


def bundle_fingerprint(spec, addon_ids, shas, original_sha):
    # NOTE: original_sha is deliberately NOT part of the fingerprint. For a
    # repack the "original" is the bundle CI itself published last time, and
    # zip output isn't byte-reproducible -- hashing it meant every run saw a
    # "changed" input and rebuilt+re-uploaded hundreds of MB forever. The
    # real inputs are the addon contents (manifest shas) + the spec; a
    # deliberate skeleton swap ships with --force.
    parts = ['%s=%s' % (i, shas.get(i, '?')) for i in sorted(addon_ids)]
    parts.append('spec=%s' % json.dumps(spec, sort_keys=True))
    return hashlib.sha256('|'.join(parts).encode()).hexdigest()


def addons_in_original(zpath):
    z = zipfile.ZipFile(zpath)
    out = set()
    for n in z.namelist():
        p = n.split('/')
        if p[0] == 'addons' and len(p) > 2:
            out.add(p[1])
    return out


def write_addon_tree(zout, dist_zip_path):
    """Append a dist zip's <id>/** tree under addons/ in the bundle."""
    zin = zipfile.ZipFile(dist_zip_path)
    for n in zin.namelist():
        if n.endswith('/'):
            continue
        zout.writestr('addons/' + n, zin.read(n))


def build_repack(name, spec, original_path, dist_dir, manifest, shas, out_path):
    orig = zipfile.ZipFile(original_path)
    orig_addons = addons_in_original(original_path)
    replaced = sorted(a for a in orig_addons if shas.get(a))
    kept = sorted(orig_addons - set(replaced))
    with zipfile.ZipFile(out_path, 'w', zipfile.ZIP_DEFLATED) as zout:
        for n in orig.namelist():
            if n.endswith('/'):
                continue
            parts = n.split('/')
            if parts[0] == 'addons' and len(parts) > 1 and parts[1] in replaced:
                continue                       # replaced with the fresh tree below
            if spec.get('state_seed') and n == SEED_PATH:
                continue                       # regenerated below
            zout.writestr(n, orig.read(n))
        for aid in replaced:
            dz = dist_zip(dist_dir, aid, shas, manifest)
            if not dz:
                raise SystemExit('%s: no dist zip for %s' % (name, aid))
            write_addon_tree(zout, dz)
        if spec.get('state_seed'):
            seed = {aid: shas[aid] for aid in replaced}
            zout.writestr(SEED_PATH, json.dumps(seed, indent=2))
    log('%s: repacked (%d addons refreshed, %d kept as-was: %s)'
        % (name, len(replaced), len(kept), ','.join(kept) or '-'))
    return sorted(orig_addons)


def build_fresh(name, spec, dist_dir, manifest, shas, out_path, originals_dir=None):
    ids = spec['fresh']
    # Optional neutral base skeleton: a fresh bundle carries only addons by
    # default (config-apply delivers userdata). But the fast one-zip install
    # also wants the CONTENT-NEUTRAL assets config does NOT deliver -- above all
    # the Hebrew fonts (media/fonts, needed by subtitles.fontname) and the
    # subtitle-addon default settings. We lift exactly those paths out of an
    # existing proven bundle (seed_base) so POV lands as complete + fast as
    # Gears, WITHOUT any Gears-specific content (menus/favourites/gears db).
    seed_base = spec.get('seed_base')
    seed_include = spec.get('seed_include', [])
    seeded = 0
    with zipfile.ZipFile(out_path, 'w', zipfile.ZIP_DEFLATED) as zout:
        if seed_base and seed_include:
            sbp = os.path.join(originals_dir or 'originals', seed_base)
            if not os.path.isfile(sbp):
                raise SystemExit('%s: seed_base %s not found (need it in originals/)'
                                 % (name, seed_base))
            zin = zipfile.ZipFile(sbp)
            wanted = [p.rstrip('/') for p in seed_include]
            for n in zin.namelist():
                if n.endswith('/'):
                    continue
                if any(n == w or n.startswith(w + '/') for w in wanted):
                    zout.writestr(n, zin.read(n))
                    seeded += 1
        for aid in ids:
            dz = dist_zip(dist_dir, aid, shas, manifest)
            if not dz:
                raise SystemExit('%s: no dist zip for %s' % (name, aid))
            write_addon_tree(zout, dz)
        if spec.get('state_seed'):
            seed = {aid: shas[aid] for aid in ids if shas.get(aid)}
            zout.writestr(SEED_PATH, json.dumps(seed, indent=2))
    log('%s: built fresh (%d addons%s)' % (name, len(ids),
        ', +%d seeded base files from %s' % (seeded, seed_base) if seeded else ''))
    return sorted(ids)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo-root', default='.')
    ap.add_argument('--dist', default='dist')
    ap.add_argument('--originals', default='originals')
    ap.add_argument('--out', default=os.path.join('dist', 'bundles'))
    ap.add_argument('--force', action='store_true')
    args = ap.parse_args()

    root = args.repo_root
    cfg = load_json(os.path.join(root, 'build.json'))
    bundles = cfg.get('bundles', {})
    if not bundles:
        log('no bundles defined in build.json - nothing to do')
        return 0
    manifest = load_json(os.path.join(root, 'manifest.json'))
    shas = manifest_shas(root)
    state = load_json(os.path.join(root, STATE_FILE), {})
    os.makedirs(args.out, exist_ok=True)
    dist_dir = os.path.join(root, args.dist)

    changed = []
    for name, spec in bundles.items():
        original_path = os.path.join(args.originals, name)
        original_sha = sha256_file(original_path) if os.path.isfile(original_path) else None
        if spec.get('repack'):
            if not original_sha:
                log('%s: SKIPPED - original bundle not available for repack' % name)
                continue
            addon_ids = sorted(a for a in addons_in_original(original_path))
        else:
            addon_ids = list(spec.get('fresh', []))
        fp = bundle_fingerprint(spec, addon_ids, shas, original_sha if spec.get('repack') else None)
        if not args.force and state.get(name) == fp:
            log('%s: unchanged (fingerprint match) - skipped' % name)
            continue
        out_path = os.path.join(args.out, name)
        if spec.get('repack'):
            build_repack(name, spec, original_path, dist_dir, manifest, shas, out_path)
        else:
            build_fresh(name, spec, dist_dir, manifest, shas, out_path,
                        originals_dir=args.originals)
        state[name] = fp
        changed.append(name)
        log('%s: %.1f MB' % (name, os.path.getsize(out_path) / 1e6))

    with open(os.path.join(root, STATE_FILE), 'w', encoding='utf-8') as fh:
        json.dump(state, fh, indent=2, sort_keys=True)
        fh.write('\n')
    with open(os.path.join(args.out, 'bundles_changed.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(changed) + ('\n' if changed else ''))
    log('done: %d bundle(s) rebuilt: %s' % (len(changed), ', '.join(changed) or '-'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
