import struct
import sys
import zlib

import xbmc
import xbmcvfs

# MASTERKODI: PIL-free. Upstream requires script.module.pil (a per-platform
# BINARY addon we cannot ship in the universal build zips), so texture
# generation silently failed fleet-wide with 'Image = None'. A 128x32 gradient
# needs no imaging library -- write the PNG directly (zlib+struct, stdlib).

SKIN_PATH = 'special://userdata'


def hex_to_rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _png_chunk(tag, data):
    raw = tag + data
    return (struct.pack('>I', len(data)) + raw
            + struct.pack('>I', zlib.crc32(raw) & 0xffffffff))


def write_png(path, width, height, rows, alpha=False):
    """rows: list of per-row byte sequences (RGB or RGBA, no filter byte)."""
    color_type = 6 if alpha else 2
    ihdr = struct.pack('>IIBBBBB', width, height, 8, color_type, 0, 0, 0)
    body = b''.join(b'\x00' + bytes(r) for r in rows)
    with open(path, 'wb') as fh:
        fh.write(b'\x89PNG\r\n\x1a\n')
        fh.write(_png_chunk(b'IHDR', ihdr))
        fh.write(_png_chunk(b'IDAT', zlib.compress(body, 9)))
        fh.write(_png_chunk(b'IEND', b''))


class Main:
    def __init__(self):
        self.handle = sys.argv
        self.skin_path = xbmcvfs.translatePath(SKIN_PATH)
        self.gradient_path = self.skin_path + 'addon_data/script.skinhelper/button_texture.png'

        self.skinhelper_path = self.skin_path + 'addon_data/script.skinhelper/'

        if not xbmcvfs.exists(self.skinhelper_path):
            xbmcvfs.mkdir(self.skinhelper_path)

    def init(self):
        success = xbmcvfs.exists(self.skin_path)

        if not success:
            return False

        for h in self.handle:
            if h == 'gradient=true':
                try:
                    self.generate_gradient()
                except Exception:
                    pass
            if h == 'monochrome=true':
                try:
                    self.generate_monochrome()
                except Exception:
                    pass
            if h == 'reload=true':
                try:
                    xbmc.executebuiltin('ReloadSkin()')
                except Exception:
                    pass

    def generate_gradient(self):
        """Generate a gradient from the two RGB given"""

        c1, c2 = None, None
        for h in self.handle:
            if 'highlight' in h:
                c1 = h.split('=')[1][2:]
            if 'gradient' in h:
                c2 = h.split('=')[1][2:]

        width, height = 128, 32

        f_co = list(map(int, hex_to_rgb(c1)))
        t_co = list(map(int, hex_to_rgb(c2)))

        r_gap = (t_co[0] - f_co[0]) / width
        g_gap = (t_co[1] - f_co[1]) / width
        b_gap = (t_co[2] - f_co[2]) / width

        row = bytearray()
        for x in range(width):
            row += bytes((max(0, min(255, int(f_co[0] + r_gap * x))),
                          max(0, min(255, int(f_co[1] + g_gap * x))),
                          max(0, min(255, int(f_co[2] + b_gap * x)))))
        write_png(self.gradient_path, width, height, [row] * height)

    def generate_monochrome(self):
        c1 = None
        for h in self.handle:
            if 'highlight' in h:
                c1 = h.split('=')[1][2:]

        width, height = 64, 64
        r, g, b = hex_to_rgb(c1)
        row = bytes((r, g, b, 255)) * width
        write_png(self.gradient_path, width, height, [row] * height, alpha=True)


if __name__ == '__main__':
    Main().init()
