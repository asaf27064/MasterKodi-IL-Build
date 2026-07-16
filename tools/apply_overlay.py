#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rebuild a merged (Hebrew-baked) addon from clean upstream + our overlay.

This is the heart of the "one place for Hebrew" model. We do NOT commit the
merged addon as our source of truth for the Hebrew-modified addons; instead we
keep only the *overlay* (the files that actually differ from clean upstream)
under `overlays/<addon_id>/files/`, plus a `base.json` describing where clean
upstream comes from. CI reconstructs the merged addon on demand:

    clean upstream zip  ->  strip git junk + de-version the top folder
                        ->  overlay files copied on top
                        ->  merged addon tree (byte-identical to what we used
                            to commit, verified by build_addons' reproducible zip)

Why this shape:
  * The Hebrew stays isolated and re-appliable. When upstream ships a new
    version we bump base.json's base_version, re-run, and the SAME overlay
    lands on the new base -- no hunting for "which files were Hebrew".
  * There is exactly one copy of every clean upstream file (we never store it),
    so the repo can't drift a stale upstream copy over the real one.

base.json fields:
  addon_id          - the Kodi addon id (== merged folder name)
  base_version      - upstream version to fetch
  base_zip_url      - URL template with {version}; may be a GitHub source
                      archive (versioned top folder) or a clean addon zip
  raw_top_folder    - (optional) template of the top folder inside the zip that
                      must be stripped/renamed to addon_id, e.g.
                      "skin.arctic.fuse.3-{version}". If absent we auto-detect.
  overlay_version   - informational; the version we stamp our build as

Usage:
  apply_overlay.py <overlays_dir> <out_dir> [--base-zip <local.zip>] [--offline]
  apply_overlay.py --verify <overlays_dir> <merged_addons_dir> [--base-zip-dir D]
"""

import argparse
import io
import json
import os
import re
import shutil
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import EXCLUDE_DIRS, EXCLUDE_NAMES, EXCLUDE_EXTS, sha256_file  # noqa: E402


def _log(msg):
    print('[apply_overlay] %s' % msg)


def _skip_name(name):
    return name in EXCLUDE_NAMES or os.path.splitext(name)[1] in EXCLUDE_EXTS


def _fetch_base_zip(base, local_zip=None, overlay_dir=None):
    """Return zip bytes for the clean base.

    Precedence: explicit local_zip arg > base_zip_local committed in the repo >
    download from base_zip_url. We commit the clean base for upstreams that
    delete old versions (e.g. unhingedthemes keeps only the latest gears zip);
    upstreams with permanent per-tag archives (GitHub source zips) can stay
    download-only.
    """
    if local_zip:
        with open(local_zip, 'rb') as fh:
            return fh.read()
    if base.get('base_zip_local') and overlay_dir:
        p = os.path.join(overlay_dir, base['base_zip_local'])
        if os.path.isfile(p):
            with open(p, 'rb') as fh:
                return fh.read()
    url = base['base_zip_url'].format(version=base['base_version'])
    _log('downloading base: %s' % url)
    try:
        import requests
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        return r.content
    except ImportError:
        import urllib.request
        with urllib.request.urlopen(url, timeout=60) as resp:
            return resp.read()


def _extract_clean_base(zip_bytes, base, dest):
    """Extract clean upstream into dest/<addon_id>, stripping git junk and
    de-versioning the top folder. Returns the addon dir path."""
    addon_id = base['addon_id']
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    names = [n for n in zf.namelist() if not n.endswith('/')]

    # Determine the top folder to strip. Prefer an explicit template.
    top = None
    if base.get('raw_top_folder'):
        top = base['raw_top_folder'].format(version=base['base_version']).rstrip('/') + '/'
    else:
        tops = set(n.split('/')[0] for n in names)
        if len(tops) == 1:
            only = next(iter(tops))
            # a real addon zip already uses the addon id as its top folder
            top = only + '/'

    addon_dir = os.path.join(dest, addon_id)
    if os.path.isdir(addon_dir):
        shutil.rmtree(addon_dir)
    os.makedirs(addon_dir)

    for n in names:
        rel = n
        if top and n.startswith(top):
            rel = n[len(top):]
        elif top:
            continue  # file outside the expected top folder -> junk
        parts = rel.split('/')
        if any(p in EXCLUDE_DIRS for p in parts[:-1]):
            continue
        if _skip_name(parts[-1]):
            continue
        dst = os.path.join(addon_dir, rel.replace('/', os.sep))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, 'wb') as fh:
            fh.write(zf.read(n))
    return addon_dir


def _apply_overlay_files(overlay_dir, addon_dir):
    """Copy every file under overlay_dir/files/ on top of addon_dir."""
    files_root = os.path.join(overlay_dir, 'files')
    count = 0
    for root, dirs, files in os.walk(files_root):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for fn in files:
            if _skip_name(fn):
                continue
            src = os.path.join(root, fn)
            rel = os.path.relpath(src, files_root)
            dst = os.path.join(addon_dir, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            count += 1
    return count


def _copy_committed_base(base, overlay_dir, dest):
    """base_type=local_committed: the clean base is a full addon tree committed
    in this repo (base_path, relative to the repo root = overlay_dir/../..).
    Copy it to dest/<addon_id> (skipping junk); overlay files then apply on top.
    If source and destination are the same directory (building straight into
    addons/), leave the tree in place."""
    addon_id = base['addon_id']
    repo_root = os.path.abspath(os.path.join(overlay_dir, os.pardir, os.pardir))
    src = os.path.join(repo_root, base['base_path'].replace('/', os.sep))
    addon_dir = os.path.join(dest, addon_id)
    if os.path.abspath(src) == os.path.abspath(addon_dir):
        return addon_dir
    if os.path.isdir(addon_dir):
        shutil.rmtree(addon_dir)
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for fn in files:
            if _skip_name(fn):
                continue
            p = os.path.join(root, fn)
            dst = os.path.join(addon_dir, os.path.relpath(p, src))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(p, dst)
    return addon_dir


def build_one(overlay_dir, out_dir, local_zip=None):
    """Reconstruct one merged addon. Returns its addon dir."""
    base = json.load(open(os.path.join(overlay_dir, 'base.json'), encoding='utf-8'))
    _log('%s: base %s + overlay %s'
         % (base['addon_id'], base.get('base_version', base.get('base_path', '?')),
            base.get('overlay_version', '?')))
    if base.get('base_type') == 'local_committed':
        addon_dir = _copy_committed_base(base, overlay_dir, out_dir)
    else:
        zip_bytes = _fetch_base_zip(base, local_zip, overlay_dir)
        addon_dir = _extract_clean_base(zip_bytes, base, out_dir)
    n = _apply_overlay_files(overlay_dir, addon_dir)
    _log('%s: applied %d overlay files' % (base['addon_id'], n))
    return addon_dir


def _tree_hashes(base_dir):
    out = {}
    for root, dirs, files in os.walk(base_dir):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for fn in files:
            if _skip_name(fn):
                continue
            p = os.path.join(root, fn)
            rel = os.path.relpath(p, base_dir).replace(os.sep, '/')
            out[rel] = sha256_file(p)
    return out


def verify(overlays_dir, merged_dir, base_zip_dir):
    """Rebuild each overlay and compare to the committed merged addon."""
    ok = True
    for name in sorted(os.listdir(overlays_dir)):
        odir = os.path.join(overlays_dir, name)
        if not os.path.isfile(os.path.join(odir, 'base.json')):
            continue
        base = json.load(open(os.path.join(odir, 'base.json'), encoding='utf-8'))
        aid = base['addon_id']
        merged = os.path.join(merged_dir, aid)
        if not os.path.isdir(merged):
            _log('VERIFY %s: no committed merged addon to compare -- skipping' % aid)
            continue
        local_zip = None
        if base_zip_dir:
            cand = os.path.join(base_zip_dir, aid + '-' + base['base_version'] + '.zip')
            if os.path.isfile(cand):
                local_zip = cand
        tmp = os.path.join(base_zip_dir or '.', '_verify_build')
        if os.path.isdir(tmp):
            shutil.rmtree(tmp)
        os.makedirs(tmp)
        rebuilt = build_one(odir, tmp, local_zip)
        a = _tree_hashes(rebuilt)
        b = _tree_hashes(merged)
        only_rebuilt = sorted(set(a) - set(b))
        only_merged = sorted(set(b) - set(a))
        diff = sorted(k for k in set(a) & set(b) if a[k] != b[k])
        if only_rebuilt or only_merged or diff:
            ok = False
            _log('VERIFY %s: MISMATCH' % aid)
            for k in only_rebuilt[:20]:
                _log('   only in rebuilt: %s' % k)
            for k in only_merged[:20]:
                _log('   only in merged : %s' % k)
            for k in diff[:20]:
                _log('   differs        : %s' % k)
        else:
            _log('VERIFY %s: OK (%d files identical)' % (aid, len(a)))
        shutil.rmtree(tmp, ignore_errors=True)
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('overlays_dir')
    ap.add_argument('out_or_merged')
    ap.add_argument('--verify', action='store_true')
    ap.add_argument('--base-zip-dir', default=None,
                    help='dir with local <id>-<version>.zip clean bases (offline verify/build)')
    args = ap.parse_args()

    if args.verify:
        ok = verify(args.overlays_dir, args.out_or_merged, args.base_zip_dir)
        sys.exit(0 if ok else 1)

    # build mode: reconstruct every overlay into out dir
    for name in sorted(os.listdir(args.overlays_dir)):
        odir = os.path.join(args.overlays_dir, name)
        if not os.path.isfile(os.path.join(odir, 'base.json')):
            continue
        base = json.load(open(os.path.join(odir, 'base.json'), encoding='utf-8'))
        local_zip = None
        if args.base_zip_dir:
            cand = os.path.join(args.base_zip_dir,
                                base['addon_id'] + '-' + base['base_version'] + '.zip')
            if os.path.isfile(cand):
                local_zip = cand
        build_one(odir, args.out_or_merged, local_zip)


if __name__ == '__main__':
    main()
