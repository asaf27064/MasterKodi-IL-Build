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
not the case now, the next boot tries again. Dismissed with OK/Back.
Always available later from the wizard's status menu.
"""
import xbmc
import xbmcaddon
import xbmcgui

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo('id')
VERSION = ADDON.getAddonInfo('version')

TITLE = 'מה חדש ב-MasterKodi IL'

# One entry per release that deserves a note; the newest first. Plain text,
# short paragraphs -- it is read on a TV from the couch.
NEWS = [
    ('2.4.191',
     '[B]מה רואים הערב?[/B]\n'
     'מסך אחד עם הצעות לפי מה שצפיתם -- המשך צפייה, בשבילכם, וחם השבוע. '
     'כל הצעה מסומנת אם יש לה כתוביות בעברית, והן קודם. '
     'נמצא בתפריט הכיבוי של כל סקין, ובמסך הבית.\n\n'
     '[B]פתיחת רשימות מהירה יותר[/B]\n'
     'אחרי כל עדכון, הבילד מכין את קוד התוספים מראש ברקע. '
     'הפתיחה הראשונה של רשימה (המשך צפייה, טרנדים, פופולריים) כבר לא מחכה. '
     'נמדד: המשך צפייה מ-7 שניות לשנייה אחת.\n\n'
     '[B]לחצתם? אנחנו עובדים על זה[/B]\n'
     'אם חיפוש המקורות מתעכב אחרי לחיצה על סרט או פרק, תופיע הודעה קצרה. '
     'לא צריך ללחוץ שוב.\n\n'
     '[B]כתוביות AI נקיות יותר[/B]\n'
     'תיאורי צלילים כמו [GUNFIRE] ושמות דוברים לא מגיעים יותר למסך, '
     'גם בכתוביות ישנות מהמאגר. שורות שהמודל דילג עליהן מתורגמות שוב '
     'במקום להישאר באנגלית.\n\n'
     'כל ההגדרות החדשות ניתנות לכיבוי בהגדרות הוויזארד, תחת "ביצועים".'),
]


def _log(msg):
    xbmc.log('[%s] whats-new: %s' % (ADDON_ID, msg), xbmc.LOGINFO)


def text_for(version):
    for v, body in NEWS:
        if v == version:
            return body
    return ''


def show(version=None):
    body = text_for(version or VERSION) or NEWS[0][1]
    xbmcgui.Dialog().textviewer(TITLE, body)


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
