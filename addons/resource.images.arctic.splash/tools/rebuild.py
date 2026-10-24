"""Rebuild the merged image resource offline from preserved original inputs.

No Kodi API, runtime Python, or image generation is involved. Requires Pillow.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from io import BytesIO
import hashlib
import json
from pathlib import Path
import shutil
import struct


def digest(data):
    return hashlib.sha256(data).hexdigest()


def remove_alpha(data):
    offset = 12
    while offset + 8 <= len(data):
        length = struct.unpack_from('<I', data, offset + 4)[0]
        if data[offset:offset + 4] == b'VP8 ':
            payload = data[offset:offset + 8 + length + length % 2]
            return b'RIFF' + struct.pack('<I', len(payload) + 4) + b'WEBP' + payload
        offset += 8 + length + length % 2
    raise ValueError('Masked RGB source has no VP8 color stream')


def encode_texture(job):
    from PIL import Image
    source = Path(job['sources']) / job['source_aliases'][0]
    data = source.read_bytes()
    if digest(data) != job['source_sha256']:
        raise ValueError(f'Original input changed: {source}. Do not use delivery files as masters.')
    with Image.open(BytesIO(data)) as original:
        image = original.convert('RGBA')
    mode = job['encoding']
    if mode == 'original':
        encoded = data
    else:
        output = BytesIO()
        if mode.startswith('lossless'):
            image.save(output, 'WEBP', lossless=True, exact=True,
                       method=4 if mode == 'lossless-fast' else 6,
                       quality=80 if mode == 'lossless-fast' else 100)
        else:
            image.save(output, 'WEBP', quality=int(mode[1:]), method=4,
                       exact=True, alpha_quality=100)
        encoded = output.getvalue()
    with Image.open(BytesIO(encoded)) as candidate:
        expected = candidate.convert('RGBA')
    if expected.size != image.size or expected.getchannel('A').tobytes() != image.getchannel('A').tobytes():
        raise ValueError(f'Dimensions or alpha changed: {source}')
    if job['diffuse']:
        encoded = remove_alpha(encoded)
        with Image.open(BytesIO(encoded)) as rgb:
            reconstructed = rgb.convert('RGBA')
        reconstructed.putalpha(image.getchannel('A'))
        if reconstructed.tobytes() != expected.tobytes():
            raise ValueError(f'RGB/matte reconstruction failed: {source}')
    if job['verify_bytes'] and digest(encoded) != job['sha256']:
        raise ValueError(f'Encoder output differs from recorded build: {source}')
    target = Path(job['output']) / 'resources' / job['path']
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(encoded)
    result = {key: value for key, value in job.items()
              if key not in ('sources', 'output', 'verify_bytes')}
    result.update(bytes=len(encoded), sha256=digest(encoded))
    return result


def main():
    from PIL import Image, ImageChops
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources', type=Path, required=True,
                        help='Folder containing the preserved original three add-on directories')
    parser.add_argument('--output', type=Path, required=True,
                        help='New output directory outside the installed package and sources')
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--verify-bytes', action='store_true',
                        help='Require identical encoded bytes (same Pillow/libwebp version)')
    args = parser.parse_args()
    package = Path(__file__).resolve().parents[1]
    sources = args.sources.resolve(strict=True)
    output = args.output.resolve()
    if output.exists() or output.is_relative_to(package) or output.is_relative_to(sources):
        parser.error('Output must be a new directory outside the package and preserved sources')
    plan = json.loads((package / 'tools/build-plan.json').read_text(encoding='utf-8'))
    output.mkdir(parents=True)
    for source in [*package.glob('*.md'), package / 'addon.xml']:
        shutil.copy2(source, output / source.name)
    shutil.copytree(package / 'tools', output / 'tools',
                    ignore=shutil.ignore_patterns('__pycache__'))
    for source in (package / 'resources').rglob('*.json'):
        if source.name == 'catalog.json':
            continue
        target = output / source.relative_to(package)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    masks = {}
    for recipe in plan['masks']:
        with Image.open(sources / recipe['source']) as original:
            alpha = original.convert('RGBA').getchannel('A')
        if recipe.get('band_source'):
            with Image.open(sources / recipe['band_source']) as band:
                band_alpha = band.convert('RGBA').getchannel('A').resize(alpha.size, Image.Resampling.BILINEAR)
            alpha = ImageChops.multiply(alpha, band_alpha)
        if digest(alpha.tobytes()) != recipe['alpha_sha256']:
            raise ValueError(f'Mask input changed: {recipe["source"]}')
        mask = Image.new('RGBA', alpha.size, 'white')
        mask.putalpha(alpha)
        target = output / 'resources' / recipe['path']
        target.parent.mkdir(parents=True, exist_ok=True)
        mask.save(target, 'WEBP', lossless=True, exact=True, method=6, quality=100)
        mask_hash = digest(target.read_bytes())
        if args.verify_bytes and mask_hash != recipe['sha256']:
            raise ValueError(f'Mask encoder output differs: {recipe["path"]}')
        with Image.open(target) as checked:
            if checked.convert('RGBA').tobytes() != mask.tobytes():
                raise ValueError(f'Mask encoding changed pixels: {recipe["path"]}')
        masks[recipe['path']] = dict(alpha_sha256=recipe['alpha_sha256'], sha256=mask_hash,
                                     dimensions=list(alpha.size), bytes=target.stat().st_size)
    results = []
    with ProcessPoolExecutor(max_workers=max(1, args.workers)) as pool:
        pending = [pool.submit(encode_texture, dict(row, sources=str(sources),
                   output=str(output), verify_bytes=args.verify_bytes)) for row in plan['assets']]
        for task in as_completed(pending):
            results.append(task.result())
            if len(results) % 100 == 0:
                print(f'Built {len(results)}/{len(pending)} textures', flush=True)
    catalog = dict(version=2, assets=sorted(results, key=lambda row: row['path']), masks=masks)
    (output / 'resources/catalog.json').write_text(json.dumps(catalog, indent=2) + '\n', encoding='utf-8')
    size = sum(path.stat().st_size for path in output.rglob('*') if path.is_file()
               and '__pycache__' not in path.parts)
    if size >= 100_000_000:
        raise ValueError(f'Build exceeds decimal 100 MB: {size:,} bytes')
    print(f'Built {output}: {size:,} bytes', flush=True)


if __name__ == '__main__':
    main()
