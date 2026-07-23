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
