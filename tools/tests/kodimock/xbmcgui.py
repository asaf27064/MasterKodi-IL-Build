NOTIFICATION_INFO = NOTIFICATION_WARNING = NOTIFICATION_ERROR = 'x'
class Dialog(object):
    def ok(self, *a, **k): return True
    def yesno(self, *a, **k): return True
    def select(self, *a, **k): return 0
    def multiselect(self, *a, **k): return []
    def notification(self, *a, **k): return None
    def textviewer(self, *a, **k): return None
class DialogProgress(object):
    def create(self, *a, **k): pass
    def update(self, *a, **k): pass
    def close(self): pass
    def iscanceled(self): return False
class DialogProgressBG(DialogProgress): pass
class ListItem(object):
    def __init__(self, *a, **k): pass
    def setArt(self, *a, **k): pass
    def setInfo(self, *a, **k): pass
    def setProperty(self, *a, **k): pass
class WindowXMLDialog(object):
    def __init__(self, *a, **k): pass
class WindowXML(object):
    def __init__(self, *a, **k): pass


class Window(object):
    """Window properties, shared per window id like the real Kodi.

    Absent until 2026-08-02, which left every window-property path UNTESTABLE --
    including the Gears settings/views mirroring that makes a db write take
    effect without a restart. Code that only wrote the db therefore passed its
    tests while being invisible to the running addon.
    """
    _store = {}

    def __init__(self, win_id=10000):
        self._id = win_id
        Window._store.setdefault(win_id, {})

    def setProperty(self, key, value):
        Window._store[self._id][key] = '' if value is None else str(value)

    def getProperty(self, key):
        return Window._store[self._id].get(key, '')

    def clearProperty(self, key):
        Window._store[self._id].pop(key, None)

    def clearProperties(self):
        Window._store[self._id].clear()
