"""Build two tiny diffuse masks for opposing lake ripple bands (Pillow)."""

from math import pi, sin
from pathlib import Path

from PIL import Image


def make_masks():
    destination = Path(__file__).resolve().parents[1] / "resources/effects"
    destination.mkdir(parents=True, exist_ok=True)
    for name, direction in (("odd", 1), ("even", -1)):
        mask = Image.new("RGBA", (4, 1080), (255, 255, 255, 0))
        for y in range(720, 1080):
            depth = (y - 720) / 360
            # Sixteen alternating bands widen toward the viewer. Feather the
            # joins and fade in below the distant bank, leaving it stationary.
            wave = sin(2 * pi * 8 * depth ** 0.8)
            band = max(0, min(1, 0.5 + direction * wave * 3))
            alpha = round(255 * band * min(1, (y - 720) / 50))
            for x in range(mask.width):
                mask.putpixel((x, y), (255, 255, 255, alpha))
        path = destination / f"lake-{name}.png"
        mask.save(path, optimize=True)
        print(f"{path.name}: {path.stat().st_size} bytes")


if __name__ == "__main__":
    make_masks()
