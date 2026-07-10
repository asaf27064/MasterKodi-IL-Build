# -*- coding: utf-8 -*-
# Pass-0 character/gender analysis.
#
# Hebrew's #1 MT problem is gender: "you" -> אתה/את depends on the ADDRESSEE,
# which the per-chunk translator must infer line by line. TMDb gives only the
# ACTOR's gender for billed cast and misses minor speakers. So before chunking
# we make ONE pass over the whole dialogue and ask the model to build a
# character gender guide FROM THE DIALOGUE ITSELF (how characters are
# addressed, pronouns, names). That guide is then handed to every chunk.
#
# This is purely ADDITIVE context: it can only give the chunk translator more
# information to get gender right. It is wrapped so any failure returns '' and
# the pipeline falls back to exactly today's behavior -- it can never make a
# translation worse.

from . import gemini
from . import srt
from . import kodi_utils

# Don't bother for very short subs -- a single chunk already sees everything.
MIN_ENTRIES = 30
# Cap the dialogue we send (keeps the call cheap; a feature film is ~1500).
MAX_DIALOGUE_LINES = 2500


def _dialogue_only(entries):
    """Flatten to plain dialogue lines (no numbers/timecodes), lightly
    cleaned, for the analysis prompt."""
    import re
    out = []
    for e in entries:
        for ln in e.lines:
            t = re.sub(r'<[^>]+>', '', ln).strip()          # drop tags
            if not t:
                continue
            out.append(t)
            if len(out) >= MAX_DIALOGUE_LINES:
                return out
    return out


def _build_prompt(dialogue, ctx_title):
    head = (
        'You are analysing a film/TV subtitle script to build a CHARACTER '
        'GENDER GUIDE for a Hebrew translator (Hebrew inflects verbs, '
        'adjectives and pronouns by gender, so the translator must know each '
        "character's gender and who addresses whom).\n\n"
        'From the DIALOGUE below{0}, identify each named or clearly '
        'identifiable speaking character and their gender, using ONLY '
        'evidence in the dialogue: how others address them, gendered '
        'pronouns, names, self-reference. Note key relationships that affect '
        'address (e.g. "Sara addresses David, her son").\n\n'
        'Output a concise list, one per line, no preamble:\n'
        '- <Character>: <male|female|unknown> - <short evidence / who they address>\n\n'
        'Rules: include a character ONLY when you have real evidence; never '
        'guess. If the script is too ambiguous to extract anything useful, '
        'output exactly: INSUFFICIENT\n\n'
        'DIALOGUE:\n'
    ).format(ctx_title)
    return head + '\n'.join(dialogue)


def character_map(english_srt, api_key, model=None, title='', year='',
                  is_episode=False, tvshow='', season='', episode=''):
    """Return a plain-text character/gender guide derived from the dialogue,
    or '' if unavailable. NEVER raises -- fail-open by design."""
    try:
        if not api_key or not english_srt:
            return ''
        entries = srt.parse(english_srt)
        if len(entries) < MIN_ENTRIES:
            return ''
        dialogue = _dialogue_only(entries)
        if len(dialogue) < MIN_ENTRIES:
            return ''

        name = tvshow or title
        ctx = ' of "{0}"'.format(name) if name else ''
        ptext = _build_prompt(dialogue, ctx)

        # Use the chosen (best) model WITH its normal reasoning -- this pass
        # genuinely benefits from it, and it runs only once per movie.
        reply = gemini.generate(api_key, model or gemini.DEFAULT_MODEL, ptext,
                                temperature=0.0)
        reply = (reply or '').strip()
        if not reply or 'INSUFFICIENT' in reply[:40].upper():
            return ''
        # Keep only the bulleted character lines; guard size.
        lines = [ln.rstrip() for ln in reply.splitlines() if ln.strip()]
        lines = [ln for ln in lines if ln.lstrip().startswith(('-', '*', '•'))][:60]
        guide = '\n'.join(lines).strip()
        kodi_utils.log('analysis: character guide built ({0} entries)'.format(len(lines)))
        return guide
    except Exception as e:
        kodi_utils.log('analysis: character_map failed (ignored): {0}'.format(e))
        return ''
