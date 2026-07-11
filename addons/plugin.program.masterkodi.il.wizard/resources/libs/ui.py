# -*- coding: utf-8 -*-
"""Shared branded UI: the MasterKodi custom-window list (wizard-menu.xml).

Every list the wizard shows -- the main menu AND the install/build flow --
goes through here, so the whole experience looks the same instead of dropping
into Kodi's plain dialog.select (which looks nothing like the wizard). Falls
back to a useDetails select only if the custom window can't load.
"""
import re

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

_ADDON = xbmcaddon.Addon()
_ADDON_ID = _ADDON.getAddonInfo('id')
_WIZ_PATH = xbmcvfs.translatePath(_ADDON.getAddonInfo('path'))


def _log(msg, level=xbmc.LOGWARNING):
    xbmc.log('[%s.ui] %s' % (_ADDON_ID, msg), level)


def menu_item(label, label2='', icon='DefaultAddon.png'):
    """A menu row as a (label, label2, icon) tuple, consumed by wizard_select."""
    return (label, label2, icon)


def strip_markup(s):
    """Drop Kodi [COLOR]/[B]/[I] tags for the detail panel + fallbacks."""
    return re.sub(r'\[/?(COLOR[^\]]*|B|I|UPPERCASE|LOWERCASE)\]', '', s or '')


class WizardMenu(xbmcgui.WindowXMLDialog):
    """Unified MasterKodi menu: RTL list on the right, a big branded detail
    panel (icon + title + description) on the left. Returns the chosen index
    via .selection (-1 = cancelled)."""

    def __init__(self, *args, **kwargs):
        self.rows = kwargs.pop('rows', [])       # [(label, label2, icon), ...]
        self.heading = kwargs.pop('heading', '')
        self.selection = -1
        super().__init__(*args)

    @staticmethod
    def pick(heading, rows):
        d = WizardMenu('wizard-menu.xml', _WIZ_PATH, 'Default', '1080i',
                       rows=rows, heading=heading)
        d.doModal()
        sel = d.selection
        del d
        return sel

    def onInit(self):
        self.setProperty('heading', self.heading)
        lst = self.getControl(100)
        lst.reset()
        for label, label2, icon in self.rows:
            li = xbmcgui.ListItem(label)
            li.setLabel2(label2)
            li.setArt({'icon': icon, 'thumb': icon})
            lst.addItem(li)
        self.setFocusId(100)

    def onClick(self, control_id):
        if control_id == 100:
            self.selection = self.getControl(100).getSelectedPosition()
            self.close()

    def onAction(self, action):
        if action.getId() in (9, 10, 92):  # BACK / PREVIOUS_MENU / NAV_BACK
            self.selection = -1
            self.close()


def wizard_select(header, rows):
    """Show a menu via the custom WizardMenu window. `rows` may be
    (label, label2, icon) tuples (from menu_item) or plain strings. Falls back
    to a useDetails dialog.select if the window can't load."""
    norm = []
    for r in rows:
        if isinstance(r, (tuple, list)):
            label, label2, icon = (list(r) + ['', 'DefaultAddon.png'])[:3]
        else:
            label, label2, icon = strip_markup(r), '', 'DefaultAddon.png'
        norm.append((strip_markup(label), strip_markup(label2), icon))
    try:
        return WizardMenu.pick(strip_markup(header), norm)
    except Exception as e:
        _log('WizardMenu failed (%s); using fallback select' % e)
        li_list = []
        for label, label2, icon in norm:
            li = xbmcgui.ListItem(label)
            li.setLabel2(label2)
            li.setArt({'icon': icon, 'thumb': icon})
            li_list.append(li)
        return xbmcgui.Dialog().select(strip_markup(header), li_list, useDetails=True)
