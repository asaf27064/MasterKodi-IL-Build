import sys
import xbmc
import xbmcvfs
try:
    from PIL import Image
except Exception:
    Image = None

SKIN_PATH = 'special://userdata'


def hex_to_rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


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

        imgsize = (128, 32)

        f_co = list(map(int, hex_to_rgb(c1)))
        t_co = list(map(int, hex_to_rgb(c2)))

        r_gap = (t_co[0] - f_co[0]) / imgsize[0]
        g_gap = (t_co[1] - f_co[1]) / imgsize[0]
        b_gap = (t_co[2] - f_co[2]) / imgsize[0]

        im = Image.new("RGB", imgsize)

        for x in range(imgsize[0]):
            column_color = (int(f_co[0] + r_gap * x),
                            int(f_co[1] + g_gap * x),
                            int(f_co[2] + b_gap * x))
            for y in range(imgsize[1]):
                im.putpixel((x, y), column_color)

        im.save(self.gradient_path)

    def generate_monochrome(self):
        c1 = None
        for h in self.handle:
            if 'highlight' in h:
                c1 = h.split('=')[1][2:]

        imgsize = (64, 64)
        gradient = Image.new('RGBA', imgsize, color=hex_to_rgb(c1))
        gradient.save(self.gradient_path, 'png')


if __name__ == '__main__':
    Main().init()
