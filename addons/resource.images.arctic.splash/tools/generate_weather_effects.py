"""Rebuild the small, vertically seamless precipitation tiles (requires Pillow)."""

from pathlib import Path
from random import Random

from PIL import Image, ImageFilter, ImageOps


def soften_snowflake(sprite):
    """Fill crystal branches and feather alpha for both winter and weather snow."""
    size = max(sprite.size)
    blur = max(0.9, size * 0.13)
    padding = round(blur * 3) + 2
    alpha = ImageOps.expand(sprite.getchannel("A"), border=padding)
    alpha = alpha.filter(ImageFilter.MaxFilter(2 * max(1, round(size * 0.08)) + 1))
    alpha = alpha.filter(ImageFilter.GaussianBlur(blur))
    # Constant colour under feathered alpha avoids dark rims.
    flake = Image.new("RGBA", alpha.size, (237, 243, 255, 0))
    flake.putalpha(alpha)
    return flake, padding


def make_snow():
    # Reuse the authored snowflake, with varied sizes, angles and softness.
    # The 300 x 560 tile is drawn at 600 x 1120 with room for vertical lilt.
    effects = Path(__file__).resolve().parents[1] / "resources/effects"
    # Keep the original separate from runtime sprites so rebuilds never blur twice.
    source = Image.open(Path(__file__).with_name("snowflake-source.png")).convert("RGBA")
    tile = Image.new("RGBA", (300, 560))
    random = Random(1192)
    for _ in range(36):
        size = random.randrange(5, 17)
        flake = source.rotate(random.randrange(360), Image.Resampling.BICUBIC)
        flake = flake.resize((size, size), Image.Resampling.LANCZOS)
        flake, padding = soften_snowflake(flake)
        opacity = random.uniform(0.45, 0.9)
        flake.putalpha(flake.getchannel("A").point(lambda a: round(a * opacity)))
        x = random.randrange(6, tile.width - size - 6)
        y = random.randrange(tile.height)
        # Wrap flakes across the vertical seam instead of clipping their shapes.
        for offset in (-tile.height, 0, tile.height):
            tile.alpha_composite(flake, (x - padding, y - padding + offset))
    destination = effects / "snow.png"
    tile.save(destination, optimize=True)
    print(f"{destination}: {destination.stat().st_size:,} bytes")


def make_rain():
    # Half-resolution art is drawn at 480 x 1080 by the skin. Keep a single
    # shared texture; stagger the speed and mirroring of its four screen strips.
    width, height = 240, 540
    alpha = Image.new("L", (width, height))
    pixels = alpha.load()
    random = Random(1191)
    for _ in range(48):
        x = random.randrange(3, width - 3)
        y = random.randrange(height)
        length = random.randrange(10, 35)
        peak = random.randrange(110, 235)
        for step in range(length):
            # A tapered trail with a bright lower tip; wrap at the vertical seam.
            strength = int(peak * (step / length) ** 0.7 * min(1, (length - step) / 3))
            row = (y + step) % height
            for dx, scale in ((-1, 0.15), (0, 1), (1, 0.15)):
                pixels[x + dx, row] = max(pixels[x + dx, row], int(strength * scale))

    # White with 16 alpha levels compresses to a tiny indexed PNG and tints in Kodi.
    tile = Image.new("P", alpha.size)
    tile.putpalette([255, 255, 255] * 256)
    tile.putdata([round(value / 17) for value in alpha.getdata()])
    tile.info["transparency"] = bytes(min(255, value * 17) for value in range(256))
    destination = Path(__file__).resolve().parents[1] / "resources/effects/rain.png"
    destination.parent.mkdir(parents=True, exist_ok=True)
    tile.save(destination, optimize=True)
    print(f"{destination}: {destination.stat().st_size:,} bytes")


if __name__ == "__main__":
    make_rain()
    make_snow()
