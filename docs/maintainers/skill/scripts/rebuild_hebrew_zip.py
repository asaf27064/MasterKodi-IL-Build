#!/usr/bin/env python3
"""Rebuild gears_hebrew_subtitles.zip from a working tree.

Validates the overlay before packaging:
- Confirms kodirdil/ has been rebranded (no fenlight refs)
- Confirms the 3 source files contain Hebrew markers
- Sets version.txt to match a target version
- Excludes __pycache__, *.pyc, .DS_Store, .git

Usage:
    python rebuild_hebrew_zip.py --src <working-tree-with-resources/> --out <zip-path> --version 2.0.0

Where <working-tree-with-resources/> contains a `resources/` directory directly.
"""

import argparse
import os
import re
import sys
import zipfile


REQUIRED_FILES = [
    'resources/lib/kodirdil/__init__.py',
    'resources/lib/kodirdil/db_utils.py',
    'resources/lib/kodirdil/hebrew_subtitles_search_utils.py',
    'resources/lib/kodirdil/string_utils.py',
    'resources/lib/kodirdil/thread_utils.py',
    'resources/lib/kodirdil/websites/__init__.py',
    'resources/lib/kodirdil/websites/hebrew_embedded.py',
    'resources/lib/kodirdil/websites/ktuvit.py',
    'resources/lib/kodirdil/websites/opensubtitles.py',
    'resources/lib/kodirdil/websites/wizdom.py',
    'resources/lib/modules/sources.py',
    'resources/lib/windows/sources.py',
    'resources/lib/caches/settings_cache.py',
    'resources/lib/apis/tmdb_api.py',
    # NOTE: apis/torbox_api.py was DROPPED from the overlay as of Gears 2.2.2 —
    # upstream now ships a native TorBox QR/device-code flow, so our patch is
    # redundant. We use upstream's torbox_api.py as-is. Do NOT re-add it.
    'resources/lib/indexers/movies.py',
    'resources/lib/indexers/tvshows.py',
    'resources/lib/indexers/navigator.py',
    'resources/lib/modules/meta_lists.py',
    'resources/lib/service.py',
    'resources/skins/Default/1080i/settings_manager.xml',
]

# Files that MUST NOT be in the overlay (stale FenLight code; no Hebrew patches in any shipped version)
# Note: service.py used to be forbidden because the legacy FenLight Hebrew zip shipped a stale
# service.py. As of 2.0.7 we legitimately patch service.py to add the TorBox subscription banner,
# so it's now in REQUIRED_FILES above instead — but ONLY if patched against clean gears base.
FORBIDDEN_FILES = [
    'resources/lib/modules/metadata.py',
]

EXCLUDE_DIRS = {'__pycache__', '.git'}
EXCLUDE_EXTS = {'.pyc', '.pyo'}
EXCLUDE_NAMES = {'.DS_Store', 'Thumbs.db'}


def fail(msg):
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def validate(src_dir):
    """Pre-flight checks before zipping."""
    errors = []

    # All required files present
    for rel in REQUIRED_FILES:
        if not os.path.exists(os.path.join(src_dir, rel.replace('/', os.sep))):
            errors.append(f"missing: {rel}")

    # No forbidden files
    for rel in FORBIDDEN_FILES:
        if os.path.exists(os.path.join(src_dir, rel.replace('/', os.sep))):
            errors.append(f"forbidden (must not be in overlay): {rel}")

    # Icons folders must exist with reasonable file counts (bundled from Tikipeter fenlight mirror).
    # We don't list every PNG — that's 178 files — but we sanity-check the folders have content.
    icons_dir = os.path.join(src_dir, 'resources', 'media', 'icons')
    netic_dir = os.path.join(src_dir, 'resources', 'media', 'network_icons')
    icons_count = len([f for f in os.listdir(icons_dir) if f.endswith('.png')]) if os.path.isdir(icons_dir) else 0
    netic_count = len([f for f in os.listdir(netic_dir) if f.endswith('.png')]) if os.path.isdir(netic_dir) else 0
    if icons_count < 50:
        errors.append(f"resources/media/icons/ has only {icons_count} PNG (expected ~100). The Hebrew overlay bundles FenLight icons to override Gears defaults; a low count means icons weren't staged.")
    if netic_count < 50:
        errors.append(f"resources/media/network_icons/ has only {netic_count} PNG (expected ~76). The Hebrew overlay bundles network logos (Netflix, Disney+, etc.) because the original FenlightAnonyMouse.github.io URLs are dead.")

    if errors:
        for e in errors: print(f"  - {e}", file=sys.stderr)
        fail("validation failed")

    # Hebrew markers present
    check_markers = {
        'resources/lib/modules/sources.py': ['kodirdil', 'is_hebrew_subtitles_enabled'],
        'resources/lib/windows/sources.py': ['kodirdil', 'has_hebrew_subs'],
        'resources/lib/caches/settings_cache.py': ['hebrew_subtitles.enable_matching'],
        # Note: tmdb_api.py and the indexers use UPPERCASE KODIRDIL markers; lowercase 'kodirdil' won't match.
        'resources/lib/apis/tmdb_api.py': ['KODIRDIL', 'get_meta_language'],
        # torbox_api.py no longer overlaid (upstream 2.2.2 has native QR) -- not checked.
        'resources/lib/indexers/movies.py': ['KODIRDIL', 'gears.tmdb_rating'],
        'resources/lib/indexers/tvshows.py': ['KODIRDIL', 'gears.tmdb_rating'],
        # Per-genre category icons (added 2026-05-15): meta_lists.py defines {'icon': 'genre_X'}
        # for each genre; navigator.py reads i.get('icon', 'genres'). Without both, all genres
        # collapse to a single 'genres' icon (gears stock behavior).
        'resources/lib/indexers/navigator.py': ['KODIRDIL', "i.get('icon', 'genres')"],
        'resources/lib/modules/meta_lists.py': ['KODIRDIL', "'icon': 'genre_action'"],
        # service.py — Debrid subscription banner for all 6 debrids (added 2.0.7, generalized 2.0.8)
        'resources/lib/service.py': ['KODIRDIL', 'DebridSubscriptionCheck', 'DEBRID_SUBS'],
        # SDR-only filter (no HDR/DV) and tried-source tracking — features unique to the
        # Hebrew mod that DO NOT use a KODIRDIL header comment (added 2026-05-15 after a
        # missed-features audit). Guard with their function names so they can't be dropped silently.
        # 'windows/sources.py' is already listed above with kodirdil markers; add the extras:
    }
    # Extend windows/sources.py markers with the non-KODIRDIL features:
    check_markers['resources/lib/windows/sources.py'].extend(['_is_hdr_item', 'tried_sources_key', "filter_value == 'sdr_only'"])
    for rel, markers in check_markers.items():
        p = os.path.join(src_dir, rel.replace('/', os.sep))
        with open(p, 'r', encoding='utf-8') as fh:
            c = fh.read()
        for m in markers:
            if m not in c:
                errors.append(f"{rel} missing marker: {m!r}")

    # FenLight residue scan
    pattern = re.compile(r'fenlight|FenLight|FenlightAnony|plugin\.video\.fenlight')
    for root, dirs, files in os.walk(src_dir):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for fn in files:
            if fn in EXCLUDE_NAMES: continue
            if os.path.splitext(fn)[1] in EXCLUDE_EXTS: continue
            fp = os.path.join(root, fn)
            try:
                with open(fp, 'r', encoding='utf-8') as fh: c = fh.read()
            except (UnicodeDecodeError, OSError):
                continue
            for m in pattern.finditer(c):
                rel = os.path.relpath(fp, src_dir).replace(os.sep, '/')
                errors.append(f"FenLight residue in {rel} at offset {m.start()}: {m.group()!r}")
                break  # one per file is enough

    if errors:
        for e in errors: print(f"  - {e}", file=sys.stderr)
        fail("validation failed")

    print(f"  validated {len(REQUIRED_FILES)} required files, 0 forbidden, 0 residue")


def build(src_dir, dst_zip, version):
    n = 0
    with zipfile.ZipFile(dst_zip, 'w', zipfile.ZIP_DEFLATED) as zf:
        # First write version.txt
        zf.writestr('version.txt', f'{version}\n')
        n += 1

        for root, dirs, files in os.walk(src_dir):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            for fn in files:
                if fn in EXCLUDE_NAMES: continue
                if os.path.splitext(fn)[1] in EXCLUDE_EXTS: continue
                fp = os.path.join(root, fn)
                # arcname relative to src_dir (which contains `resources/`)
                arc = os.path.relpath(fp, src_dir).replace(os.sep, '/')
                # Skip version.txt at top level (we wrote it above with new version)
                if arc == 'version.txt': continue
                zf.write(fp, arc)
                n += 1
    print(f"  built {dst_zip}: {n} entries, {os.path.getsize(dst_zip):,} bytes")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--src', required=True, help='Path to working tree (containing resources/)')
    ap.add_argument('--out', required=True, help='Output zip path')
    ap.add_argument('--version', required=True, help='Version string for version.txt')
    ap.add_argument('--skip-validation', action='store_true', help='Skip pre-flight checks (not recommended)')
    args = ap.parse_args()

    if not os.path.isdir(args.src):
        fail(f"source not a directory: {args.src}")
    if not os.path.isdir(os.path.join(args.src, 'resources')):
        fail(f"source missing resources/ subdir: {args.src}")
    if not re.match(r'^\d+\.\d+\.\d+', args.version):
        fail(f"version must be semver-like: {args.version}")

    if not args.skip_validation:
        print(f"Validating {args.src}...")
        validate(args.src)

    print(f"Building {args.out} with version {args.version}...")
    build(args.src, args.out, args.version)
    print("Done.")


if __name__ == '__main__':
    main()
