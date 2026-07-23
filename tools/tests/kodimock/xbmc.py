import os
LOGDEBUG, LOGINFO, LOGWARNING, LOGERROR, LOGFATAL, LOGNONE = -1, 0, 1, 2, 3, 4
_HOME = os.environ.get('MK_TESTHOME', os.getcwd())


def log(msg, level=0):
    pass


def translatePath(p):
    m = {
        'special://home/': _HOME + os.sep,
        'special://xbmc/': _HOME + os.sep,
        'special://userdata/': os.path.join(_HOME, 'userdata') + os.sep,
        'special://profile/': os.path.join(_HOME, 'userdata') + os.sep,
        'special://masterprofile/': os.path.join(_HOME, 'userdata') + os.sep,
        'special://database/': os.path.join(_HOME, 'userdata', 'Database') + os.sep,
        'special://thumbnails/': os.path.join(_HOME, 'userdata', 'Thumbnails') + os.sep,
        'special://logpath/': _HOME + os.sep,
        'special://temp/': os.path.join(_HOME, 'temp') + os.sep,
    }
    for k, v in m.items():
        if p.startswith(k):
            return v + p[len(k):].replace('/', os.sep)
    return p.replace('/', os.sep)


def getInfoLabel(x):
    if 'BuildVersion' in x:
        return '21.3 (21.3.0) Git:...'
    return ''


def getSkinDir():
    return os.environ.get('MK_SKIN', 'skin.estuary')


def executebuiltin(x, *a):
    pass


def sleep(ms):
    pass


def getCondVisibility(x):
    return False


class Monitor(object):
    def abortRequested(self): return False
    def waitForAbort(self, t=0): return True


class Player(object):
    def isPlaying(self): return False
    def isPlayingVideo(self): return False
