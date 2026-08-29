# -*- coding: utf-8 -*-
"""Generate the shipped skin-settings seed for Arctic Zephyr Rounded.

WHY THIS EXISTS
---------------
The skin applies ~210 default settings from a first-run block in
1080i/Includes_Defs.xml, every action gated on

    <onload condition="!Skin.HasSetting(startup.init)">Skin.SetBool(x)</onload>

and the very last action sets `startup.init` itself. That block runs when Home
first loads -- i.e. AFTER the wizard has written our settings file -- so it
silently overwrote 8 of the 22 values we seeded (measured 2026-08-29), among
them `hide.furniture.flags.vertical.widgets` (which hides every rating on the
home screen) and `settingskinfont` (which forces the font back to "Default", a
fontset with no Hebrew glyphs at all).

Pre-setting `startup.init` alone is not safe: the skin's other 209 defaults
would then never be applied and the skin would come up half-configured
(no theme colour, no home style, no perf level).

So we ship the skin's OWN defaults, replayed exactly, plus `startup.init`, plus
our overrides on top. The first-run block then never fires, nothing races us,
and the user can still change every one of these from Skin settings afterwards
-- which is the point: Asaf wants users to choose their own setup.

Re-run this whenever the skin is updated, so upstream's new defaults are picked
up:  python tools/gen_rounded_skin_defaults.py

FONT
----
`settingskinfont` is an index the skin maps to a fontset on every Home load
(Home.xml lines 29-47). 11 = "NotoSans Regular", the ONLY fontset this skin
ships that contains a Hebrew-capable face (NotoSans-Regular.ttf); upstream's
default of 1 = "Default" is Roboto Condensed and renders Hebrew as tofu.
"""
import io
import os
import re

SKIN = 'skin.arctic.zephyr.rounded'
SRC = os.path.join('addons', SKIN, '1080i', 'Includes_Defs.xml')
VARIANTS = ('rounded-pov', 'rounded-pov-tmdb')

NL, T = chr(10), chr(9)

# --- our overrides, applied AFTER the skin's own defaults --------------------
# value None means "leave unset" (equivalent to the skin's Skin.Reset)
OVERRIDES = [
    # -- font: the one thing the user must not be able to get wrong ----------
    ('settingskinfont', ('string', '11')),      # 11 = NotoSans Regular

    # -- ratings: every gate that can hide the flags row ---------------------
    # the skin's first-run sets hide.furniture.flags.vertical.widgets TRUE,
    # which hides the whole flags row (ratings included) on the home screen
    # whenever the vertical/Netflix layout is in use.
    ('hide.furniture.flags.vertical.widgets', ('bool', 'false')),
    ('hide.furniture.flags', ('bool', 'false')),
    ('enable.furniture.flags.icons', ('bool', 'true')),
    ('hide.flags.rating', ('bool', 'false')),
    ('hide.video.ratings', ('bool', 'false')),
    ('hide.501.507.flags.numeric.rating', ('bool', 'false')),
    ('enable.flags.rating.numeric', ('bool', 'true')),
    ('tmdbhelper.disableratings', ('bool', 'false')),
    # the rating set Zephyr uses (union of its movie + tv picks)
    ('show.flags.rating.imdb', ('bool', 'true')),
    ('show.flags.rating.tmdb', ('bool', 'true')),
    ('show.flags.rating.trakt', ('bool', 'true')),
    ('show.flags.rating.mdblist', ('bool', 'true')),
    ('show.flags.rating.metacritics', ('bool', 'true')),
    ('show.flags.rating.tomatoes', ('bool', 'true')),
    ('show.flags.rating.letterboxd', ('bool', 'true')),

    # -- clearlogo instead of title (skin string #31414), video contexts -----
    ('show.home.flix.title.clearlogo', ('bool', 'true')),
    ('show.view.netflix.title.clearlogo.video', ('bool', 'true')),
    ('show.view.509.title.clearlogo', ('bool', 'true')),
    ('show.view.511.title.clearlogo', ('bool', 'true')),
    ('show.view.514.title.clearlogo', ('bool', 'true')),
    ('show.osd.video.clearlogo', ('bool', 'true')),
]

ACTION = re.compile(
    r'<onload condition="!Skin\.HasSetting\(startup\.init\)">'
    r'Skin\.(SetBool|SetString|Reset)\(([^)]*)\)</onload>')


def resolve(value):
    """Kodi evaluates skin expressions before storing. Only $NUMBER[n] appears
    in this block; anything else unexpected is reported rather than stored raw."""
    m = re.fullmatch(r'\$NUMBER\[(\d+)\]', value.strip())
    if m:
        return m.group(1)
    return value


def build():
    text = io.open(SRC, encoding='utf-8', errors='replace').read()
    actions = ACTION.findall(text)
    if not actions:
        raise SystemExit('no first-run block found - did the skin change?')

    # replay in file order; later actions win, exactly as Kodi would apply them
    settings = {}
    for kind, arg in actions:
        if kind == 'Reset':
            settings.pop(arg.strip(), None)
            continue
        if kind == 'SetBool':
            settings[arg.strip()] = ('bool', 'true')
            continue
        sid, _, val = arg.partition(',')
        settings[sid.strip()] = ('string', resolve(val))

    replayed = len(settings)

    # our overrides on top
    changed = []
    for sid, val in OVERRIDES:
        if settings.get(sid) != val:
            changed.append((sid, settings.get(sid), val))
        settings[sid] = val

    # make sure the skin's first-run block never fires again
    settings['startup.init'] = ('bool', 'true')

    unresolved = [s for s, (_t, v) in settings.items() if '$' in v]
    if unresolved:
        print('  WARNING unresolved skin expressions: %s' % unresolved)

    lines = ['<settings>']
    for sid in sorted(settings):
        typ, val = settings[sid]
        lines.append(T + '<setting id="%s" type="%s">%s</setting>' % (sid, typ, val))
    lines.append('</settings>')
    doc = NL.join(lines) + NL
    # Kodi crashes NATIVELY on an XML comment in addon_data/<addon>/settings.xml
    assert '<!--' not in doc

    print('  replayed %d skin defaults from its first-run block' % replayed)
    print('  applied %d override(s):' % len(changed))
    for sid, was, now in changed:
        print('     %-44s %s -> %s' % (sid, (was or ('-', '-'))[1], now[1]))
    print('  + startup.init=true so the block never overwrites us')

    for v in VARIANTS:
        p = os.path.join('config-variants', v, 'skin.rounded', 'settings.xml')
        d = os.path.dirname(p)
        if not os.path.isdir(d):
            os.makedirs(d)
        io.open(p, 'wb').write(doc.encode('utf-8'))
        print('  wrote %s (%d settings)' % (p.replace(os.sep, '/'), len(settings)))


if __name__ == '__main__':
    build()
