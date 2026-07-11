#!/usr/bin/env python3
"""Build FenLight_Estuary.zip and Arctic_Fuse_Skin.zip from the working trees.

The zip contents are flat (addons/, userdata/, media/ at the root). No outer folder.
This is what the wizard's BuildManager.extract_zip() expects.

Usage:
    cd C:\\Users\\asaf2\\Desktop\\kodi_project
    python ~/.claude/skills/masterkodi-il-builder/scripts/build_release_zips.py

Or override paths:
    python build_release_zips.py --root <project-root> --estuary FenLight_Estuary --arctic Arctic_Fuse_Skin
"""

import argparse
import os
import sys
import zipfile


EXCLUDE_DIRS = {'__pycache__', '.git'}
EXCLUDE_EXTS = {'.pyc', '.pyo'}
EXCLUDE_NAMES = {'.DS_Store', 'Thumbs.db'}
# Maintainer-only paths (relative to src_dir, '/'-separated) that must NOT
# ship on-device. The gearsai community-pool Worker source lives inside the
# addon for convenience but is server-side only -- users never run it.
EXCLUDE_RELPATHS = {
    'addons/service.subtitles.gearsai/cloudflare',
}


def _excluded_relpath(rel):
    rel = rel.replace(os.sep, '/')
    return any(rel == p or rel.startswith(p + '/') for p in EXCLUDE_RELPATHS)


def zip_folder_contents(src_dir, dst_zip):
    """Zip the CONTENTS of src_dir into dst_zip (no outer folder wrapper)."""
    if os.path.exists(dst_zip):
        os.remove(dst_zip)
    n = 0
    total_input = 0
    with zipfile.ZipFile(dst_zip, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(src_dir):
            # Prune excluded dirs in-place
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            rel_root = os.path.relpath(root, src_dir)
            dirs[:] = [d for d in dirs
                       if not _excluded_relpath(os.path.join(rel_root, d))]
            for fn in files:
                if fn in EXCLUDE_NAMES: continue
                if os.path.splitext(fn)[1] in EXCLUDE_EXTS: continue
                fp = os.path.join(root, fn)
                arc = os.path.relpath(fp, src_dir).replace(os.sep, '/')
                zf.write(fp, arc)
                n += 1
                total_input += os.path.getsize(fp)
    return n, total_input, os.path.getsize(dst_zip)


def sanity_check(zip_path, expected_root_prefixes):
    """Confirm the zip has flat layout (no FenLight_Estuary/ wrapper)."""
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        if not names:
            return False, "empty zip"
        # Every top-level entry must be one of the expected prefixes
        bad = [n for n in names if not any(n.startswith(p) for p in expected_root_prefixes)]
        if bad:
            return False, f"unexpected top-level entries (first 3): {bad[:3]}"
    return True, f"OK: {len(names)} entries"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--root', default='.', help='Project root (default: current dir)')
    ap.add_argument('--estuary', default='FenLight_Estuary', help='Base build folder name')
    ap.add_argument('--arctic', default='Arctic_Fuse_Skin', help='Arctic Fuse folder name')
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        print(f"FAIL: root not a directory: {root}", file=sys.stderr)
        sys.exit(1)

    targets = [
        (args.estuary, 'FenLight_Estuary.zip', ('addons/', 'userdata/', 'media/')),
        (args.arctic, 'Arctic_Fuse_Skin.zip', ('addons/', 'userdata/')),
    ]

    failed = False
    for folder, zip_name, expected_prefixes in targets:
        src = os.path.join(root, folder)
        dst = os.path.join(root, zip_name)

        if not os.path.isdir(src):
            print(f"SKIP: {folder} not found at {src}", file=sys.stderr)
            failed = True
            continue

        print(f"Building {zip_name} from {folder}/...")
        n, in_size, out_size = zip_folder_contents(src, dst)
        print(f"  {n} files, {in_size/1048576:.1f} MB input -> {out_size/1048576:.1f} MB zip")

        ok, msg = sanity_check(dst, expected_prefixes)
        if ok:
            print(f"  layout: {msg}")
        else:
            print(f"  LAYOUT FAILED: {msg}", file=sys.stderr)
            failed = True

    if failed:
        sys.exit(1)

    print("\nReady to upload:")
    for _, zip_name, _ in targets:
        print(f"  {os.path.join(root, zip_name)}")
    print("\nUpload with:")
    print("  gh release upload v1.0 FenLight_Estuary.zip Arctic_Fuse_Skin.zip \\")
    print("      -R asaf27064/asaf27064.github.io --clobber")


if __name__ == '__main__':
    main()
