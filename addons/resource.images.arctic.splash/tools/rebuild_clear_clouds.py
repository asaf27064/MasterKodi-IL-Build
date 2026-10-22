"""Grade a repaired transparent cloud master into clear/hazy plates (Pillow, NumPy)."""

import argparse
from io import BytesIO
from pathlib import Path
import subprocess

import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = "7b4dda18c2cadac4ecfb66182ced22c5ec303904"
HOURS = ("0000", "0500", "0600", "0700", "0800", "1700", "1800", "1900")


def original(hour):
    path = f"resources/layers/03-clouds/clear/{hour}.webp"
    data = subprocess.check_output(["git", "show", f"{ORIGINAL}:{path}"], cwd=ROOT)
    return np.asarray(Image.open(BytesIO(data)).convert("RGBA"))


def rebuild(source):
    # Recover the existing hourly palette from matching cloud pixels, avoiding
    # the contaminated extraction fringes. Keep all grading offline.
    daytime = original("0800")
    sample = (daytime[:, :, 3] > 200) & (daytime[:, :, :3].min(axis=2) > 100)
    design = np.column_stack((daytime[:, :, :3][sample], np.ones(sample.sum())))
    master = Image.open(source).convert("RGBA").resize((1920, 1080), Image.Resampling.LANCZOS)
    pixels = np.asarray(master).copy()
    # Discard invisible alpha noise from the generated transparent background.
    pixels[:, :, 3][pixels[:, :, 3] <= 2] = 0
    colors = np.column_stack((pixels[:, :, :3].reshape(-1, 3), np.ones(1920 * 1080)))
    total = 0
    for hour in HOURS:
        target = original(hour)[:, :, :3][sample]
        transform = np.linalg.lstsq(design, target, rcond=None)[0]
        error = np.abs(design @ transform - target).mean()
        rgb = np.clip(np.rint(colors @ transform), 0, 255).astype(np.uint8).reshape(1080, 1920, 3)
        for weather, opacity in (("clear", 1.0), ("hazy", 0.4)):
            rgba = pixels.copy()
            rgba[:, :, :3] = rgb
            rgba[:, :, 3] = np.rint(pixels[:, :, 3] * opacity).astype(np.uint8)
            path = ROOT / f"resources/layers/03-clouds/{weather}/{hour}.webp"
            encoded = BytesIO()
            Image.fromarray(rgba).save(encoded, format="WEBP", quality=95, method=6, lossless=False)
            path.write_bytes(encoded.getvalue())
            total += path.stat().st_size
        print(f"{hour}: hourly palette fit mean error {error:.2f}/255")
    print(f"16 cloud plates: {total:,} bytes")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Repaired transparent daytime cloud master")
    rebuild(parser.parse_args().source)
