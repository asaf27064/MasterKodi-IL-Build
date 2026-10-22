# -*- coding: utf-8 -*-
""""מה חדש" -- one screen, once, on the first boot after an update lands.

The toast "עודכנו N תוספים" stays as it is (Asaf, 2026-10-22); this is the
screen that comes with it. It is keyed to the WIZARD VERSION: the note is
shown when the running wizard is a different version from the one that ran
last boot -- that is exactly "the device has the update now", never before,
and never on the boot the update was merely downloaded on. A fresh install
records the version silently (there is nothing to compare to), so a new
user is not greeted with a changelog.

Shown only when the home screen is up and nothing is playing; if that is
not the case now, the next boot tries again. Its own window (whats-new.xml)
so the Hebrew is right-aligned on every skin -- Kodi's built-in text viewer
follows the skin's alignment, which is not always RTL.
"""
import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo('id')
VERSION = ADDON.getAddonInfo('version')

TITLE = 'מה חדש ב-MasterKodi IL'

# One entry per release that deserves a note; the newest first. Plain
# Hebrew, short paragraphs, no numbers -- it is read on a TV from the couch.
NEWS = [
    ('2.4.191',
     '[B]מה רואים הערב?[/B]\n'
     'מסך חדש עם הצעות לצפייה לפי מה שצפיתם: המשך צפייה, בשבילכם וחם השבוע. '
     'ליד כל הצעה מסומן אם יש לה כתוביות בעברית. '
     'נמצא בתפריט הכיבוי של כל סקין ובמסך הבית.\n\n'
     '[B]פתיחת רשימות מהירה יותר[/B]\n'
     'אחרי כל עדכון הבילד מכין את קוד התוספים מראש ברקע, '
     'כך שרשימות כמו המשך צפייה, טרנדים ופופולריים נפתחות מהר יותר.\n\n'
     '[B]הודעה בזמן חיפוש מקורות[/B]\n'
     'כשחיפוש המקורות מתעכב אחרי לחיצה על סרט או פרק, מופיעה הודעה קצרה שהחיפוש בעיצומו. '
     'אין צורך ללחוץ שוב.\n\n'
     '[B]כתוביות AI נקיות יותר[/B]\n'
     'תיאורי צלילים כמו [GUNFIRE] ושמות דוברים לא מופיעים יותר על המסך, '
     'גם בכתוביות ישנות מהמאגר. שורות שלא תורגמו מתורגמות שוב במקום להישאר באנגלית.\n\n'
     '[B]פרופילים ומצב ילדים[/B]\n'
     'מסך "מי צופה?" חדש: כשיש יותר מפרופיל אחד הוא מופיע בהפעלה, ומכל סקין אפשר להחליף פרופיל '
     'מתפריט הכיבוי או מהוויזארד. לכל פרופיל אפשר לבחור אווטאר (תפריט הקשר על הפרופיל במסך). '
     'אפשר ליצור פרופיל לכל בן משפחה, והבילד מכין אותו לבד עם התפריטים וההגדרות. '
     'בפרופיל ילדים ההורה בוחר גיל, ורק תוכן שמתאים לגיל הזה מוצג ומתנגן. '
     'הכל בתפריט הוויזארד, תחת "פרופילים ומצב ילדים".\n\n'
     'עודכנו גם הסקינים ומנוע התוכן לגרסאות העדכניות ביותר.\n\n'
     'את ההגדרות החדשות אפשר לכבות בהגדרות הוויזארד, תחת "ביצועים".'),
]


def _log(msg):
    xbmc.log('[%s] whats-new: %s' % (ADDON_ID, msg), xbmc.LOGINFO)


def text_for(version):
    for v, body in NEWS:
        if v == version:
            return body
    return ''


class _NoteWindow(xbmcgui.WindowXMLDialog):
    def __init__(self, *args, **kwargs):
        self.body = kwargs.pop('body', '')
        super().__init__(*args)

    def onInit(self):
        self.setProperty('heading', TITLE)
        self.setProperty('body', self.body)
        self.setFocusId(300)

    def onClick(self, control_id):
        if control_id == 300:
            self.close()

    def onAction(self, action):
        if action.getId() in (9, 10, 92, 216, 247):
            self.close()


def show(version=None):
    body = text_for(version or VERSION) or NEWS[0][1]
    path = xbmcvfs.translatePath(ADDON.getAddonInfo('path'))
    w = _NoteWindow('whats-new.xml', path, 'Default', '1080i', body=body)
    w.doModal()
    del w


def show_if_due(monitor):
    """Service hook. Returns True when a note was shown."""
    try:
        last = ADDON.getSetting('last_boot_version') or ''
        if last == VERSION:
            return False
        if not last:
            ADDON.setSetting('last_boot_version', VERSION)   # fresh install
            _log('first boot of %s recorded' % VERSION)
            return False
        if not text_for(VERSION):
            ADDON.setSetting('last_boot_version', VERSION)   # update without a note
            return False
        # only on the home screen, only when nothing plays -- else next boot
        for _ in range(60):
            if monitor.abortRequested():
                return False
            if (xbmc.getCondVisibility('Window.IsVisible(home)')
                    and not xbmc.getCondVisibility('System.HasActiveModalDialog')
                    and not xbmc.Player().isPlaying()):
                break
            if monitor.waitForAbort(2):
                return False
        else:
            _log('home not idle; note for %s deferred to next boot' % VERSION)
            return False
        _log('showing note for %s (was %s)' % (VERSION, last))
        ADDON.setSetting('last_boot_version', VERSION)
        show(VERSION)
        return True
    except Exception as e:
        _log('failed: %s' % e)
        return False
