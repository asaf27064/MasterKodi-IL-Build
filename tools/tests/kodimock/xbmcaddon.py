import os
_ADDON_PATH = os.environ.get('MK_ADDON_PATH', '')
_SETTINGS = {}
class Addon(object):
    def __init__(self, id=None): self._id = id or 'plugin.program.masterkodi.il.wizard'
    def getAddonInfo(self, k):
        return {'id': self._id, 'name': 'MasterKodi IL Wizard',
                'version': os.environ.get('MK_VERSION', '2.4.134'),
                'path': _ADDON_PATH, 'profile': os.path.join(os.environ.get('MK_TESTHOME',''),'userdata','addon_data',self._id)}.get(k, '')
    def getSetting(self, k): return _SETTINGS.get(k, '')
    def setSetting(self, k, v): _SETTINGS[k] = v
    def getSettingBool(self, k): return _SETTINGS.get(k) in ('true', True)
