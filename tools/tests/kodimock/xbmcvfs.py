import os, shutil
import xbmc
def translatePath(p): return xbmc.translatePath(p)
def exists(p): return os.path.exists(p.replace('/', os.sep))
def mkdir(p):
    try: os.makedirs(p.replace('/', os.sep), exist_ok=True); return True
    except Exception: return False
def mkdirs(p): return mkdir(p)
def delete(p):
    try: os.remove(p.replace('/', os.sep)); return True
    except Exception: return False
def rmdir(p, force=False):
    try: shutil.rmtree(p.replace('/', os.sep), ignore_errors=True); return True
    except Exception: return False
def listdir(p):
    p = p.replace('/', os.sep)
    if not os.path.isdir(p): return [], []
    dirs, files = [], []
    for n in os.listdir(p):
        (dirs if os.path.isdir(os.path.join(p, n)) else files).append(n)
    return dirs, files
def copy(a, b):
    try: shutil.copy2(a.replace('/', os.sep), b.replace('/', os.sep)); return True
    except Exception: return False
class File(object):
    def __init__(self, p, mode='r'): self._f = open(p.replace('/', os.sep), mode + ('b' if 'b' not in mode else ''))
    def read(self): return self._f.read()
    def write(self, d): return self._f.write(d)
    def close(self): self._f.close()
    def __enter__(self): return self
    def __exit__(self, *a): self._f.close()
