# -*- coding: utf-8 -*-
"""Sweep everything WE ship for the mistakes that have actually bitten us.

Every check here exists because the pattern silently broke a real box. This is
not style linting -- each one maps to an incident:

  1. writing a Kodi settings file while Kodi runs
       Kodi keeps settings in MEMORY and re-saves the file on a graceful exit,
       so the edit is discarded. Broke the OLED feature on every install
       (2026-08-13). Safe only when PAIRED with Settings.SetSettingValue, or
       when the process hard-exits afterwards.
  2. XML comments in a shipped addon settings file
       Kodi's addon-settings reader dies NATIVELY on a comment node -- no
       traceback, the log just stops. Crash-looped the fleet (2026-08-10).
  3. setting-id matching that ignores attributes
       The real entry is <setting id="x" default="true">, so an exact
       '<setting id="x">' match finds nothing and an append silently creates a
       DUPLICATE. That is how the skip_intro flip was lost (2026-08-13).
  4. treating Gears' 'empty_setting' sentinel as a real value
       It is the "never configured" placeholder; carrying it across made every
       unused debrid service look authorised (2026-08-09).
  5. bare `except: pass` in our overlay code
       Swallows the failure with no log, which is how several of the bugs above
       stayed invisible for so long.

Usage:  python tools/check_forbidden_patterns.py [--strict]
Without --strict it reports; with --strict it exits 1 on any finding.
"""

import glob
import io
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Only OUR code. Upstream addons are not ours to police, and their own idioms
# would drown the signal.
OUR_CODE = [
    'addons/plugin.program.masterkodi.il.wizard/**/*.py',
    'addons/service.masterkodi.skipintro/**/*.py',
    'addons/service.subtitles.gearsai/**/*.py',
    'overlays/*/files/**/*.py',
    'overlays-piers/*/files/**/*.py',
]

SETTINGS_FILES = ('guisettings.xml', 'advancedsettings.xml')


def _our_files():
    seen = set()
    for pat in OUR_CODE:
        for p in glob.glob(os.path.join(REPO, pat), recursive=True):
            if '__pycache__' in p or not os.path.isfile(p):
                continue
            if p not in seen:
                seen.add(p)
                yield p


def _rel(p):
    return os.path.relpath(p, REPO).replace(os.sep, '/')


def _strip_comments(src):
    """Blank out # comments and docstrings so prose about a pattern is not
    mistaken for the pattern itself."""
    src = re.sub(r'"""(?:.|\n)*?"""', '', src)
    src = re.sub(r"'''(?:.|\n)*?'''", '', src)
    return re.sub(r'#[^\n]*', '', src)


def check_settings_file_writes(findings):
    """A write to a Kodi settings file is only safe when the same module also
    pushes the value through the settings API (or the process hard-exits)."""
    for p in _our_files():
        src = _strip_comments(io.open(p, encoding='utf-8', errors='replace').read())
        for name in SETTINGS_FILES:
            if name not in src:
                continue
            # does this module open that file for WRITING?
            writes = re.search(r"open\s*\([^)]*%s[^)]*['\"][wa]" % re.escape(name.split('.')[0]), src) \
                or re.search(r"open\s*\(\s*\w*(guisettings|advancedsettings)\w*\s*,\s*['\"][wa]", src)
            if not writes:
                continue
            paired = 'SetSettingValue' in src
            findings.append((
                'settings-file-write' if not paired else 'settings-file-write (paired)',
                _rel(p),
                '%s written directly%s' % (
                    name,
                    '' if not paired else ' -- but this module also uses the settings API'),
                not paired))


def check_xml_comments_in_shipped_settings(findings):
    """Comment nodes in a shipped addon_data settings file crash Kodi."""
    for pat in ('config/**/settings.xml', 'config-variants/**/settings.xml',
                'config-variants-piers/**/settings.xml',
                'addons/*/resources/settings.xml'):
        for p in glob.glob(os.path.join(REPO, pat), recursive=True):
            rel = _rel(p)
            # the addon's own DEFINITION file legitimately carries comments --
            # only the addon_data VALUES files are parsed by the crashing reader
            if rel.startswith('addons/'):
                continue
            txt = io.open(p, encoding='utf-8', errors='replace').read()
            if '<!--' in txt:
                findings.append(('xml-comment', rel,
                                 'comment node in a shipped settings file -- crashes Kodi',
                                 True))


def check_attribute_blind_setting_edits(findings):
    """A matcher that demands '>' right after the id cannot see a real setting.

    The live entry is <setting id="x" default="true">, so '<setting id="x">'
    matches nothing -- and the usual fallback then APPENDS a duplicate. That is
    how the skip_intro flip was silently lost (2026-08-13).

    NOT flagged (these already tolerate attributes):
      '<setting id="x"'            -- stops at the id
      '<setting id="([^"]+)"'      -- captures the id
      '<setting id="x"[^>]*>'      -- explicitly allows them
    """
    MATCHERS = ('re.search', 're.sub', 're.subn', 're.match', 're.compile',
                're.findall', 're.finditer', 'pat', 'pattern')
    for p in _our_files():
        src = _strip_comments(io.open(p, encoding='utf-8', errors='replace').read())
        for i, line in enumerate(src.split(chr(10)), 1):
            if '<setting id=' not in line:
                continue
            if '[^>]' in line or '[^<]' in line:
                continue                       # explicitly tolerates attributes
            # dangerous ONLY if the id is immediately followed by the closing >
            if ('"' + '>') not in line and ("'" + '>') not in line:
                continue
            if not any(tok in line for tok in MATCHERS):
                continue                       # output, not a matcher
            findings.append(('attribute-blind-match', '%s:%d' % (_rel(p), i),
                             'matcher demands ">" right after the id: %s'
                             % line.strip()[:80], True))


# NOTE: there is deliberately no grep check for the 'empty_setting' sentinel.
# The value is legitimate upstream idiom in a dozen places -- a read default
# (get_setting(key, 'empty_setting')), the setting_default column of Gears'
# own settings table, and our own scrub migration all mention it. A pattern
# check fired on all of those and buried the one case that matters. The real
# risk -- carrying the sentinel across as if it were a login -- is covered by
# behaviour tests instead (test_pov_placeholder_scrub: cross-engine filter,
# xml staging, and the migration).


def check_bare_except_pass(findings):
    """Bare except: pass in OUR overlay code hides exactly these failures."""
    for p in _our_files():
        if '/overlays/' not in _rel(p) and 'overlays-piers' not in _rel(p):
            continue
        if '/kodirdil/' not in _rel(p):
            continue          # kodirdil is the code we author inside the engines
        src = io.open(p, encoding='utf-8', errors='replace').read()
        for m in re.finditer(r'except\s*:\s*\n\s*pass\b', src):
            line = src[:m.start()].count('\n') + 1
            findings.append(('bare-except-pass', '%s:%d' % (_rel(p), line),
                             'failure swallowed with no log', False))


def main():
    strict = '--strict' in sys.argv
    findings = []
    check_settings_file_writes(findings)
    check_xml_comments_in_shipped_settings(findings)
    check_attribute_blind_setting_edits(findings)
    check_bare_except_pass(findings)

    hard = [f for f in findings if f[3]]
    soft = [f for f in findings if not f[3]]

    print('scanned %d file(s) of our own code' % len(list(_our_files())))
    if not findings:
        print('\nCLEAN - none of the known-dangerous patterns are present')
        return 0

    if hard:
        print('\n%d PROBLEM(S):' % len(hard))
        for kind, where, why, _ in hard:
            print('  [%s] %s\n      %s' % (kind, where, why))
    if soft:
        print('\n%d note(s) (worth knowing, not a defect):' % len(soft))
        for kind, where, why, _ in soft:
            print('  [%s] %s -- %s' % (kind, where, why))
    return 1 if (hard and strict) else 0


if __name__ == '__main__':
    sys.exit(main())
