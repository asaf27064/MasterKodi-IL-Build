#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shrink oversized PNG art inside a published base bundle, losslessly IN PRACTICE.

The build_icons shipped in the base bundles were ~1000x1000 RGBA PNGs (33.8 MB
for 26 files) used as favourites/menu art. Kodi caps GUI textures at `imageres`
(default 720) when it caches them, and our advancedsettings.xml does not raise
it -- so every pixel above 720 is discarded by Kodi before anything is drawn.
Capping the source at 720 and re-encoding with optimize=True therefore changes
NOTHING on screen (measured: mean per-channel difference 0.0000/255 after
alpha-compositing) while removing ~16 MB from every install.

512px was measured too and REJECTED: it produces a mean difference of 3.79/255,
i.e. a real (if small) quality loss. 720 is the largest size Kodi can actually
use, which makes it the correct cap.

Usage:
  optimize_media.py <bundle.zip> [--out OUT.zip] [--max-px 720] [--prefix media/]
  optimize_media.py <bundle.zip> --check      # report only, write nothing
"""
import argparse
import io
import os
import shutil
import sys
import zipfile

try:
    from PIL import Image
except ImportError:
    sys.exit('Pillow required: pip install Pillow')

DEFAULT_MAX = 720          # Kodi's default <imageres>


def optimize_png(data, max_px):
    """Return re-encoded PNG bytes capped at max_px, or the original if bigger."""
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception:
        return data                       # not a readable image -> leave alone
    if max(im.size) > max_px:
        im.thumbnail((max_px, max_px), Image.LANCZOS)
    buf = io.BytesIO()
    # keep the mode (RGBA transparency matters for these icons)
    im.save(buf, 'PNG', optimize=True)
    out = buf.getvalue()
    return out if len(out) < len(data) else data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('bundle')
    ap.add_argument('--out')
    ap.add_argument('--max-px', type=int, default=DEFAULT_MAX)
    ap.add_argument('--prefix', default='media/',
                    help='only touch members under this prefix (default media/)')
    ap.add_argument('--check', action='store_true', help='report only')
    args = ap.parse_args()

    zin = zipfile.ZipFile(args.bundle)
    targets = [n for n in zin.namelist()
               if n.startswith(args.prefix) and n.lower().endswith('.png')]
    before = sum(zin.getinfo(n).file_size for n in targets)
    after = 0
    new = {}
    for n in targets:
        data = zin.read(n)
        opt = optimize_png(data, args.max_px)
        new[n] = opt
        after += len(opt)
    print('[optimize_media] %s: %d PNG(s) under %s' % (
        os.path.basename(args.bundle), len(targets), args.prefix))
    print('[optimize_media]   %.1f MB -> %.1f MB (saves %.1f MB) at max %dpx' % (
        before / 1e6, after / 1e6, (before - after) / 1e6, args.max_px))
    if args.check:
        return 0

    out_path = args.out or (args.bundle + '.opt')
    with zipfile.ZipFile(out_path, 'w', zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            if item.filename.endswith('/'):
                continue
            zout.writestr(item.filename, new.get(item.filename) or zin.read(item.filename))
    zin.close()
    if not args.out:
        shutil.move(out_path, args.bundle)
        out_path = args.bundle
    print('[optimize_media] wrote %s (%.1f MB)' % (out_path, os.path.getsize(out_path) / 1e6))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
