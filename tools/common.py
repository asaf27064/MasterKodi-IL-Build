#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared helpers for the MasterKodi IL build pipeline.

The whole pipeline is built around ONE idea: reproducible zips. A given addon
folder always produces a byte-identical zip (fixed entry order, zeroed
timestamps, fixed permissions), so its sha256 only changes when the *content*
changes. That's what lets CI upload only what actually changed instead of
re-shipping the whole build on every push.
"""

import hashlib
import os
import zipfile

# Files/dirs never shipped inside an addon zip.
EXCLUDE_DIRS = {'.git', '__pycache__', '.github', '.idea', '.vscode', 'node_modules'}
EXCLUDE_NAMES = {'.DS_Store', 'Thumbs.db', 'desktop.ini', '.gitignore', '.gitattributes', '.gitmodules'}
EXCLUDE_EXTS = {'.pyc', '.pyo', '.pyd'}

# Addon sub-paths that must never ship (heavy/binary or environment specific).
# Keyed by addon id -> list of top-level relative paths to skip.
PER_ADDON_EXCLUDES = {
    'service.subtitles.gearsai': ['cloudflare'],
}

# A fixed timestamp for every zip entry (2010-01-01) -> reproducible archives.
FIXED_DATE = (2010, 1, 1, 0, 0, 0)


def iter_addon_files(addon_dir, addon_id):
    """Yield (absolute_path, arcname) for every file that belongs in the zip.

    arcname is prefixed with the addon id so the zip extracts to
    `addons/<id>/...` the way Kodi expects.
    """
    skip_rel = set(PER_ADDON_EXCLUDES.get(addon_id, []))
    for root, dirs, files in os.walk(addon_dir):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        rel_root = os.path.relpath(root, addon_dir).replace(os.sep, '/')
        # honour per-addon top-level excludes
        if rel_root != '.' and rel_root.split('/')[0] in skip_rel:
            dirs[:] = []
            continue
        for fn in sorted(files):
            if fn in EXCLUDE_NAMES:
                continue
            if os.path.splitext(fn)[1] in EXCLUDE_EXTS:
                continue
            abspath = os.path.join(root, fn)
            rel = os.path.relpath(abspath, addon_dir).replace(os.sep, '/')
            if rel.split('/')[0] in skip_rel:
                continue
            arcname = '%s/%s' % (addon_id, rel)
            yield abspath, arcname


def build_reproducible_zip(addon_dir, addon_id, out_path):
    """Zip an addon folder deterministically. Returns (sha256, size)."""
    entries = sorted(iter_addon_files(addon_dir, addon_id), key=lambda x: x[1])
    with zipfile.ZipFile(out_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for abspath, arcname in entries:
            with open(abspath, 'rb') as fh:
                data = fh.read()
            info = zipfile.ZipInfo(arcname, date_time=FIXED_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16  # stable file mode
            zf.writestr(info, data)
    return sha256_file(out_path), os.path.getsize(out_path)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def read_addon_meta(addon_dir):
    """Return (id, version) parsed from addon.xml, or (None, None)."""
    xml = os.path.join(addon_dir, 'addon.xml')
    if not os.path.isfile(xml):
        return None, None
    import re
    with open(xml, 'r', encoding='utf-8', errors='replace') as fh:
        head = fh.read(4000)
    aid = re.search(r'<addon\s+[^>]*id="([^"]+)"', head)
    ver = re.search(r'<addon\s+[^>]*version="([^"]+)"', head)
    if not aid:
        # id/version can be in any attribute order
        aid = re.search(r'id="([^"]+)"', head)
    if not ver:
        ver = re.search(r'version="([^"]+)"', head)
    return (aid.group(1) if aid else None, ver.group(1) if ver else None)
