"""Extract tiny breeze sprites and clean the registered foreground plates.

Requires Pillow. By default, read the original foregrounds from their recorded
Git revision so repeat runs never extract from cleaned artwork. --source-dir can point
to a directory containing the original spring/summer/autumn.webp files instead.
Only the WebP alpha chunk changes: the compressed RGB artwork is kept verbatim.
"""

import argparse
import json
import math
from io import BytesIO
from pathlib import Path
import struct
import subprocess

from PIL import Image, ImageChops, ImageDraw

from generate_weather_effects import soften_snowflake


ROOT = Path(__file__).resolve().parents[1]
SOURCE_REVISION = "0dd59aa06ef6a315521582f8df6da03a194456ca"
FOREGROUNDS = Path("resources/layers/07-trees")
# Inspected, isolated alpha islands; do not remove leaves attached to branches.
FLOATING = {
    "spring": [
        (1655, 63, 1688, 91), (996, 241, 1022, 260),
        (852, 878, 876, 893), (1056, 935, 1082, 958),
        (1150, 973, 1178, 993), (1365, 954, 1394, 971),
        (294, 228, 305, 236), (320, 243, 333, 251),
        (1678, 802, 1687, 817), (1662, 785, 1668, 790),
        (1673, 805, 1678, 808), (1659, 807, 1662, 810),
        (1430, 1013, 1436, 1019), (1441, 1063, 1447, 1067),
    ],
    "autumn": [
        (1637, 39, 1717, 107), (966, 230, 1047, 290),
        (411, 538, 470, 585), (836, 847, 910, 906),
        (1042, 924, 1107, 971), (1125, 958, 1210, 1022),
        (1349, 933, 1422, 988), (1393, 1014, 1441, 1046),
    ],
}


def chunks(data):
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise ValueError("Expected a WebP RIFF file")
    offset = 12
    while offset < len(data):
        size = struct.unpack_from("<I", data, offset + 4)[0]
        end = offset + 8 + size + size % 2
        yield data[offset:offset + 4], data[offset:end]
        offset = end


def replace_alpha(original, alpha):
    # Pillow encodes the new lossless alpha; keep the original lossy RGB bitstream
    # rather than recompressing a full-size image or increasing its stored size.
    carrier = Image.new("RGBA", alpha.size, (0, 0, 0, 0))
    carrier.putalpha(alpha)
    encoded = BytesIO()
    carrier.save(encoded, "WEBP", quality=0, method=4, exact=True)
    replacement = dict(chunks(encoded.getvalue()))[b"ALPH"]
    original_chunks = list(chunks(original))
    if b"ALPH" not in dict(original_chunks) or b"VP8 " not in dict(original_chunks):
        raise ValueError("Expected the original lossy WebP with a separate alpha chunk")
    payload = b"WEBP" + b"".join(
        replacement if name == b"ALPH" else chunk
        for name, chunk in original_chunks
    )
    return b"RIFF" + struct.pack("<I", len(payload)) + payload


def original_bytes(season, source_dir):
    if source_dir:
        return (source_dir / f"{season}.webp").read_bytes()
    return subprocess.run(
        ["git", "show", f"{SOURCE_REVISION}:{(FOREGROUNDS / f'{season}.webp').as_posix()}"],
        cwd=ROOT, check=True, capture_output=True,
    ).stdout


def island_mask(alpha, box):
    left, top, right, bottom = box
    padded = (left - 1, top - 1, right + 1, bottom + 1)
    patch = alpha.crop(padded)
    width, height = patch.size
    opaque = {i for i, value in enumerate(patch.tobytes()) if value}
    expected = (1, 1, right - left + 1, bottom - top + 1)
    while opaque:
        seed = opaque.pop()
        component, pending = {seed}, [seed]
        while pending:
            i = pending.pop()
            x, y = i % width, i // width
            for ny in range(max(0, y - 1), min(height, y + 2)):
                for nx in range(max(0, x - 1), min(width, x + 2)):
                    neighbour = ny * width + nx
                    if neighbour in opaque:
                        opaque.remove(neighbour)
                        component.add(neighbour)
                        pending.append(neighbour)
        pixels = bytearray(width * height)
        for i in component:
            pixels[i] = 255
        mask = Image.frombytes("L", patch.size, bytes(pixels))
        if mask.getbbox() == expected:
            return padded, mask
    raise ValueError(f"Source island changed at {box}")


def grade(image, hour, kind="fg"):
    """Use the original scene author's night and twilight pigment transforms."""
    altitude = math.cos((hour - 12) * math.pi / 12)
    t = max(0, min(1, (altitude + .30) / .70))
    daylight = t * t * (3 - 2 * t)
    twilight = math.exp(-((abs(altitude) - .06) / .24) ** 2)
    if twilight < .04:
        twilight = 0
    weights, bias, strength = (
        ((.14, .17, .27), (.007, .009, .021), .22)
        if kind == "fg" else ((.105, .15, .255), (.007, .012, .025), .30)
    )
    result = image.copy()
    pixels = []
    for r, g, b, alpha in image.getdata():
        color = (r / 255, g / 255, b / 255)
        luminance = sum(c * w for c, w in zip(color, (.2126, .7152, .0722)))
        shaded = [c * daylight + (luminance * w + offset) * (1 - daylight)
                  for c, w, offset in zip(color, weights, bias)]
        warmth = twilight * strength
        rgb = [round(255 * max(0, min(1, c * (1 - warmth) + luminance * w * warmth)))
               for c, w in zip(shaded, (1.12, .70, .54))]
        pixels.append((*rgb, alpha))
    result.putdata(pixels)
    return result


def clean_time_plates(season, masks, from_working_tree=False):
    """Remove detached silhouettes from the registered scene-lighting plates too.

    Sky and prelit water need no replacement grade. Where a leaf covered land,
    solve the alpha of the existing pigment against the newly exposed land grade.
    This retains the original compressed RGB and every pixel outside the islands.
    """
    layers = ROOT / "resources/layers"
    states = json.loads((layers / "manifest.json").read_text())["states"]
    saved = 0
    error_sum = pixel_count = 0
    for path in sorted((layers / "09-time" / season).glob("*.webp")):
        relative = path.relative_to(ROOT).as_posix()
        original = path.read_bytes() if from_working_tree else subprocess.run(
            ["git", "show", f"{SOURCE_REVISION}:{relative}"],
            cwd=ROOT, check=True, capture_output=True,
        ).stdout
        plate = Image.open(BytesIO(original)).convert("RGBA")
        alpha = plate.getchannel("A")
        if not alpha.getbbox():
            continue
        hour = int(path.stem) // 100
        paths = states[f"{season}/clear/{hour}"][:8]
        images = [Image.open(layers / p).convert("RGBA") for p in paths]
        for box, mask in masks:
            base = Image.new("RGBA", mask.size)
            target = base.copy()
            for index, image in enumerate(images):
                patch = image.crop(box)
                base.alpha_composite(patch)
                target.alpha_composite(grade(patch, hour, "land") if index == 4 else patch)
            pigment = plate.crop(box)
            replacement = alpha.crop(box)
            values = list(replacement.getdata())
            for i, (background, desired, paint, selected) in enumerate(zip(
                base.getdata(), target.getdata(), pigment.getdata(), mask.getdata(),
            )):
                if not selected:
                    continue
                direction = [p - b for p, b in zip(paint[:3], background[:3])]
                delta = [d - b for d, b in zip(desired[:3], background[:3])]
                norm = sum(d * d for d in direction)
                amount = max(0, min(1, sum(d * c for d, c in zip(delta, direction)) / norm)) if norm else 0
                values[i] = round(255 * amount)
                error_sum += sum(abs(b + (values[i] / 255) * d - want)
                                 for b, d, want in zip(background[:3], direction, desired[:3]))
                pixel_count += 3
            replacement.putdata(values)
            alpha.paste(replacement, box)
        expected = plate.copy()
        expected.putalpha(alpha)
        cleaned = replace_alpha(original, alpha)
        if Image.open(BytesIO(cleaned)).convert("RGBA").tobytes() != expected.tobytes():
            raise ValueError(f"{path}: pixels outside the repaired alpha changed")
        path.write_bytes(cleaned)
        saved += len(original) - len(cleaned)
    print(f"  lighting plates: {saved:,} bytes saved; exposed-background mean error {error_sum / max(1, pixel_count):.2f}/255")


def save_lighting_variants(sprite, folder):
    folder.mkdir(parents=True, exist_ok=True)
    for hour in (0, 5, 6, 7, 8, 17, 18, 19):
        grade(sprite, hour).save(folder / f"{hour:02d}00.png", optimize=True)
    print(f"  {folder.name} sprites: {sprite.width} x {sprite.height}, {sum(p.stat().st_size for p in folder.glob('*.png')):,} bytes for 8 lighting states")


def make_snowflake(source):
    sprite = Image.open(source).convert("RGBA")
    bounds = sprite.getchannel("A").getbbox()
    if not bounds or sprite.getchannel("A").getextrema()[0] != 0:
        raise ValueError("Snowflake source must have a transparent background")
    sprite = sprite.crop(bounds)
    sprite.thumbnail((60, 60), Image.Resampling.LANCZOS)
    sprite, _ = soften_snowflake(sprite)
    sprite.thumbnail((64, 64), Image.Resampling.LANCZOS)
    tile = Image.new("RGBA", (64, 64))
    tile.paste(sprite, ((64 - sprite.width) // 2, (64 - sprite.height) // 2))
    save_lighting_variants(tile, ROOT / "resources/effects/foliage/winter")


def extract(source_dir=None):
    destination = ROOT / "resources/effects/foliage"
    destination.mkdir(parents=True, exist_ok=True)
    for season in ("spring", "summer", "autumn"):
        original = original_bytes(season, source_dir)
        image = Image.open(BytesIO(original)).convert("RGBA")
        alpha = image.getchannel("A")
        boxes = FLOATING.get(season, [])
        masks = []
        if boxes:
            for box in boxes:
                padded, mask = island_mask(alpha, box)
                masks.append((padded, mask))
                alpha.paste(0, padded, mask)
            sprite = image.crop(boxes[0])
            expected = image.copy()
            expected.putalpha(alpha)
            cleaned = replace_alpha(original, alpha)
            decoded = Image.open(BytesIO(cleaned)).convert("RGBA")
            if decoded.tobytes() != expected.tobytes():
                raise ValueError(f"{season}: pixels changed outside the extracted alpha")
            (ROOT / FOREGROUNDS / f"{season}.webp").write_bytes(cleaned)
            print(f"{season}: removed {len(boxes)} islands; plate {len(original):,} -> {len(cleaned):,} bytes; RGB unchanged")
            clean_time_plates(season, masks)
        else:
            # Summer has no loose leaves. Clip a terminal leaf for animation,
            # keeping the attached leaf in the static tree. Trim its stem/neighbours.
            sprite = image.crop((849, 365, 874, 393))
            mask = Image.new("L", sprite.size)
            ImageDraw.Draw(mask).polygon(
                [(8, 3), (17, 3), (23, 9), (23, 27), (1, 27), (1, 9)], fill=255,
            )
            sprite.putalpha(ImageChops.multiply(sprite.getchannel("A"), mask))
            sprite = sprite.crop(sprite.getbbox())
            print("summer: no floating leaves in the source; static plate unchanged")
        padded_sprite = Image.new("RGBA", (sprite.width + 4, sprite.height + 4))
        padded_sprite.paste(sprite, (2, 2))
        save_lighting_variants(padded_sprite, destination / season)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--snowflake-source", type=Path, help="Build only winter variants from a transparent snowflake PNG")
    args = parser.parse_args()
    if args.snowflake_source:
        make_snowflake(args.snowflake_source)
    else:
        extract(args.source_dir)
