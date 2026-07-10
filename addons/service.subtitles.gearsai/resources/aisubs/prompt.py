# -*- coding: utf-8 -*-
# The translation prompt. Kept here so we can iterate on wording
# without touching orchestration. Our own design.
#
# Three things make Hebrew subtitle MT hard, and the prompt attacks
# each explicitly:
#   1. Structural fidelity  - same entry count/order, timecodes verbatim.
#   2. Gender agreement     - Hebrew inflects verbs/adjectives/pronouns
#                             by speaker + addressee gender.
#   3. RTL punctuation      - sentence-final punctuation must sit after
#                             the Hebrew text in the plain-text stream.

LANG_NAME = {
    'en': 'English', 'es': 'Spanish', 'fr': 'French', 'de': 'German',
    'pt': 'Portuguese', 'it': 'Italian', 'ru': 'Russian', 'ar': 'Arabic',
    'nl': 'Dutch', 'pl': 'Polish', 'tr': 'Turkish', 'ja': 'Japanese',
    'ko': 'Korean', 'zh': 'Chinese',
}


def _cast_block(cast):
    """Render the cast list the model uses to pick gender forms.
    `cast` is a list of {name, character, gender} dicts (gender in
    'male'/'female'/'unknown'). Empty -> instruct context inference."""
    if not cast:
        return (
            'CAST: not available. Infer each character\'s gender from '
            'dialogue cues (names, pronouns, how others address them). '
            'When genuinely ambiguous, rephrase to avoid the gendered '
            'form rather than guess.'
        )
    lines = []
    for c in cast:
        name = (c.get('name') or '').strip()
        char = (c.get('character') or '').strip()
        gender = (c.get('gender') or 'unknown').strip()
        if not name and not char:
            continue
        if char and gender in ('male', 'female'):
            lines.append('- {0} ({1}, played by {2})'.format(char, gender, name))
        elif char:
            lines.append('- {0} (gender unknown, played by {1})'.format(char, name))
        elif gender in ('male', 'female'):
            lines.append('- {0} ({1})'.format(name, gender))
        else:
            lines.append('- {0}'.format(name))
    if not lines:
        return 'CAST: not available.'
    return 'CAST (use to choose correct Hebrew gender forms):\n' + '\n'.join(lines)


def _context_line(title, year, is_episode, tvshow, season, episode):
    if is_episode and tvshow:
        s = '"{0}", season {1} episode {2}'.format(tvshow, season or '?', episode or '?')
        if title:
            s += ' ("{0}")'.format(title)
    elif title:
        s = '"{0}"'.format(title)
    else:
        s = 'an unknown title'
    if year:
        s += ' ({0})'.format(year)
    return s


def _guide_block(gender_map):
    """Dialogue-derived character/gender guide (analysis.py). Supplementary:
    explicitly subordinate to the cast list and to the actual line evidence,
    so it can only ADD information, never override good data."""
    if not gender_map or not gender_map.strip():
        return ''
    return (
        'DIALOGUE-DERIVED CHARACTER GUIDE (supplementary hints built from this '
        "film's own dialogue; use to resolve characters' gender you otherwise "
        "couldn't tell. The cast list and the actual evidence in each line "
        'still take precedence if they clearly disagree):\n'
        + gender_map.strip() + '\n\n'
    )


def build_json(source_lang, cast=None, gender_map='', src_cues=None, nl='⏎'):
    """JSON-array prompt used ONLY by the rescue path (translate._json_rescue).
    Same gender/RTL guidance as build(), but asks for a JSON array out so the
    model can't return malformed SRT. src_cues = list of cue strings (in-cue
    line breaks marked by `nl`)."""
    import json as _json
    src = LANG_NAME.get((source_lang or 'en').lower(), source_lang or 'English')
    cast_block = _cast_block(cast)
    guide_block = _guide_block(gender_map)
    src_cues = src_cues or []
    arr = _json.dumps(src_cues, ensure_ascii=False)
    return (
        'You are a professional Hebrew subtitle translator. Translate the JSON '
        'array of {src} subtitle cues below into natural, conversational '
        'Hebrew.\n\n'
        '{cast}\n\n{guide}'
        'GENDER (Hebrew is heavily gendered): 1st person -> speaker gender; '
        '2nd person "you" -> ADDRESSEE gender (man "אתה", woman "את"); 3rd -> '
        'referent. Use the cast/guide + dialogue cues; keep each character '
        'consistent.\n'
        'RTL: sentence-final punctuation (. ? ! , : ;) goes AFTER the Hebrew '
        'text, never before it.\n'
        'Drop hearing-impaired annotations ([music], (sighs)). Keep <i> tags.\n\n'
        'OUTPUT: return ONLY a JSON array of strings -- SAME length and order '
        'as the input ({n} items). Preserve the "{nl}" symbol exactly where it '
        'marks an in-cue line break.\n\n'
        'INPUT:\n{arr}\n'
    ).format(src=src, cast=cast_block, guide=guide_block, n=len(src_cues),
             nl=nl, arr=arr)


def build(source_lang, title='', year='', cast=None, is_episode=False,
          tvshow='', season='', episode='', entry_count=0,
          prev_context_lines=None, chunk='', gender_map=''):
    """Assemble the full prompt for one chunk.

    prev_context_lines: dialogue text (Hebrew) from the tail of the
    previous chunk -- continuity only, NOT re-translated.
    chunk: the numbered SRT blocks to translate (from srt.to_blocks).
    gender_map: optional dialogue-derived character guide (analysis.py).
    """
    src = LANG_NAME.get((source_lang or 'en').lower(), source_lang or 'English')
    ctx = _context_line(title, year, is_episode, tvshow, season, episode)
    cast_block = _cast_block(cast)
    guide_block = _guide_block(gender_map)

    prev_block = ''
    if prev_context_lines:
        body = '\n'.join('   ' + ln for ln in prev_context_lines if ln and ln.strip())
        if body.strip():
            prev_block = (
                'PREVIOUS LINES (the dialogue immediately before this chunk; '
                'do NOT output or translate these -- use them only to keep '
                'speaker/gender continuity across the cut):\n' + body + '\n\n'
            )

    return (
        'You are a professional Hebrew subtitle translator. Translate the '
        '{src} subtitles below into natural, conversational Hebrew.\n\n'
        'Context: {ctx}\n\n'
        '{cast}\n\n'
        '{guide}'

        'OUTPUT STRUCTURE (read first -- violating these breaks playback):\n'
        '- Output EXACTLY the same number of entries as the input, in the '
        'SAME ORDER. Every input entry number appears exactly once.\n'
        '- Copy each "HH:MM:SS,mmm --> HH:MM:SS,mmm" timecode line VERBATIM. '
        'Do not alter a single character.\n'
        '- Only translate the TEXT. Keep the same number of text lines per '
        'entry, in the same order (do not swap stacked lines).\n'
        '- Separate entries with one blank line. Output ONLY the SRT -- no '
        'preamble, no commentary.\n\n'

        'GENDER (Hebrew is heavily gendered -- this is the #1 quality issue):\n'
        '- Decide who is SPEAKING and who they ADDRESS for each line, using '
        'the cast list, reply patterns, and name/pronoun cues.\n'
        '- 1st person ("I am tired") -> speaker gender: male "אני עייף", '
        'female "אני עייפה".\n'
        '- 2nd person ("you are right") -> addressee gender: to a man '
        '"אתה צודק", to a woman "את צודקת", to a group "אתם/אתן".\n'
        '- 3rd person -> referent gender: "הוא הלך" / "היא הלכה".\n'
        '- Keep a character\'s gender consistent across consecutive lines; '
        'do not flip mid-scene.\n'
        '- ALL-CAPS speaker tags like "JOHN:" identify the speaker -- match '
        'against the cast for gender, then DROP the tag from the Hebrew.\n'
        '- In two-person dialogue, speakers usually alternate; a short reply '
        'is most likely the other person.\n\n'

        'RTL PUNCTUATION (critical -- a frequent AI mistake):\n'
        '- In the plain text you produce, sentence-final punctuation '
        '(. ? ! , : ; ...) goes AFTER the last Hebrew word, never before '
        'the first.\n'
        '- Correct: "מה שלומך?"   Wrong: "?מה שלומך"\n'
        '- Correct: "באמת!"       Wrong: "!באמת"\n'
        '- Kodi renders the result right-to-left; you only need the logical '
        'text order correct.\n\n'

        'OTHER RULES:\n'
        '1. Idiomatic, native-sounding Hebrew -- not word-for-word.\n'
        '2. Keep HTML tags like <i></i> intact around the translated text.\n'
        '3. Drop hearing-impaired annotations ([music], (sighs), etc.) from '
        'the Hebrew output. (Speaker tags are handled by the gender rule.)\n'
        '4. Numbers, proper nouns and on-screen text: translate sensibly; '
        'keep brand/character names recognisable.\n\n'

        '{prev}'
        'SRT to translate ({n} entries):\n\n'
        '{chunk}\n'
    ).format(src=src, ctx=ctx, cast=cast_block, guide=guide_block,
             prev=prev_block, n=entry_count, chunk=chunk)
