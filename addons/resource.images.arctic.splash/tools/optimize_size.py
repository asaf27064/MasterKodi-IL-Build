"""Compress lake plates once, then enforce the decimal 100 MB budget.

Requires Pillow. Run on original assets; repeated lossy encoding reduces quality.
Default invocation only checks size. Development files count; Git history and
Python caches do not form part of the distributable add-on.
"""
import argparse
from io import BytesIO
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
LIMIT = 100_000_000


def package_files():
    return sorted(p for p in ROOT.rglob("*") if p.is_file()
                  and not {".git", "__pycache__"}.intersection(p.relative_to(ROOT).parts))


def compress(path, quality, image_format):
    original = path.read_bytes()
    with Image.open(BytesIO(original)) as image:
        image.load()
        output = BytesIO()
        options = (dict(method=4, exact=True) if image_format == "WEBP"
                   else dict(subsampling=0, optimize=True))
        image.save(output, image_format, quality=quality, **options)
        encoded = output.getvalue()
        if len(encoded) >= len(original):
            return 0
        with Image.open(BytesIO(encoded)) as result:
            result.load()
            assert result.size == image.size, path
            assert result.mode == image.mode, path
            if "A" in image.getbands():
                assert result.getchannel("A").tobytes() == image.getchannel("A").tobytes(), path
        path.write_bytes(encoded)
    return len(original) - len(encoded)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lakes", action="store_true", help="encode lake WebPs at quality 85")
    args = parser.parse_args()
    files = package_files()
    before = sum(p.stat().st_size for p in files)
    changed = 0
    for path in files:
        saved = 0
        if args.lakes and path.is_relative_to(ROOT / "resources/layers/06-lake") and path.suffix == ".webp":
            saved = compress(path, 85, "WEBP")
        changed += bool(saved)
        if saved and changed % 50 == 0:
            print(f"Compressed {changed} images", flush=True)
    total = sum(p.stat().st_size for p in package_files())
    print(f"Add-on: {before:,} -> {total:,} bytes; {changed} images changed")
    print(f"Headroom: {LIMIT - total:,} bytes (100 MB = {LIMIT:,} bytes)")
    if total >= LIMIT:
        raise SystemExit("Add-on exceeds the size budget")


if __name__ == "__main__":
    main()
