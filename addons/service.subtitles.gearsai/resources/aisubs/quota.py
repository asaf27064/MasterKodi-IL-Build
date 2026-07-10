# -*- coding: utf-8 -*-
# Track Gemini request usage per model per UTC-day.
#
# The free tier resets at UTC midnight. We persist a tiny JSON map
# {date_utc: {model: count}} in the addon profile and surface today's
# usage in settings + the completion toast. Purely informational --
# Google enforces the real limits; we just show the user where they are.
#
# Fully defensive: any failure here must never affect translation.

import datetime
import json
import os

from . import kodi_utils

_FILE = 'usage.json'
# Approx free-tier requests/day, for the % display. Conservative.
DAILY_LIMITS = {
    'gemini-3.1-flash-lite': 1000,
    'gemini-2.5-flash-lite': 1000,
    'gemini-2.0-flash': 200,
    'gemini-2.5-flash': 250,
}


def _path():
    return os.path.join(kodi_utils.profile_dir(), _FILE)


def _today():
    return datetime.datetime.utcnow().strftime('%Y-%m-%d')


def _load():
    try:
        with open(_path(), 'r', encoding='utf-8') as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save(data):
    try:
        with open(_path(), 'w', encoding='utf-8') as f:
            json.dump(data, f)
    except Exception:
        pass


def note(model, n=1):
    """Record n successful requests against `model` for today."""
    if not model:
        return
    try:
        today = _today()
        data = _load()
        # prune old days
        data = {today: data.get(today, {})}
        day = data[today]
        day[model] = int(day.get(model, 0)) + int(n)
        _save(data)
    except Exception:
        pass


def today_counts():
    """Return {model: count} for today."""
    return (_load() or {}).get(_today(), {})


def summary_line():
    """One-line Hebrew summary of today's usage, e.g.
    'שימוש היום: 3.1-flash-lite 42/1000 · 2.5-flash 5/250'."""
    counts = today_counts()
    if not counts:
        return 'שימוש היום: 0 בקשות'
    parts = []
    for model, n in sorted(counts.items()):
        short = model.replace('gemini-', '')
        lim = DAILY_LIMITS.get(model)
        parts.append('{0} {1}/{2}'.format(short, n, lim) if lim else '{0} {1}'.format(short, n))
    return 'שימוש היום: ' + ' · '.join(parts)
