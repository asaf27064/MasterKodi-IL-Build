"""Shared setup: put the Kodi shim + the wizard addon on sys.path so the tests
import the REAL wizard modules. Repo root is derived from this file's location,
so the suite runs anywhere (dev box or CI)."""
import os, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))          # tools/tests -> tools -> repo
ADDON = os.path.join(REPO, 'addons', 'plugin.program.masterkodi.il.wizard')

def make_home():
    home = tempfile.mkdtemp(prefix='mkhome_')
    os.makedirs(os.path.join(home, 'userdata', 'addon_data'), exist_ok=True)
    os.makedirs(os.path.join(home, 'addons'), exist_ok=True)
    os.environ['MK_TESTHOME'] = home
    os.environ['MK_ADDON_PATH'] = ADDON
    return home

def setup_path():
    sys.path.insert(0, os.path.join(HERE, 'kodimock'))
    sys.path.insert(0, ADDON)
