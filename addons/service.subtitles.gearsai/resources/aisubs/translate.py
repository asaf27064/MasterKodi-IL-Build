# -*- coding: utf-8 -*-
# Orchestration: English SRT text in, Hebrew SRT text out.
#
# Pipeline:
#   parse -> chunk -> per-chunk Gemini call (with cross-chunk
#   continuity) -> map translated lines back onto entries -> stitch
#   -> serialise. Defensive throughout: a chunk that fails to
#   translate keeps its source text rather than dropping entries
#   (better a few English lines than a broken/short file).

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import gemini
from . import kodi_utils
from . import prompt
from . import srt
from . import rtl
from . import quota

# Entries per Gemini request. Bigger chunks = far fewer requests (matters on
# the free tier's per-minute limit) AND fewer chunk boundaries -- gender/pronoun
# consistency errors cluster at boundaries, so fewer boundaries = better Hebrew.
# 140 cues is still comfortably inside the output-token cap (truncation-split
# handles the rare overflow anyway).
DEFAULT_CHUNK_SIZE = 140
# Bigger chunks used only in opt-in fast mode: fewer requests + more context.
FAST_CHUNK_SIZE = 180
# How many trailing source lines to carry into the next chunk as
# speaker/gender continuity context (bigger tail = the model sees who's
# mid-conversation across the boundary).
CONTEXT_TAIL = 10
# How many chunks to translate concurrently. The best model is slow per
# call, so overlapping calls is the biggest win; the chunks are
# independent (each carries its own English context), so this is safe.
# Capped to stay friendly to the free tier's per-minute limit.
DEFAULT_CONCURRENCY = 2
MAX_CONCURRENCY = 6
# Retry policy for transient (overload) errors.
MAX_OVERLOAD_RETRIES = 3
OVERLOAD_BACKOFF_SEC = 5
# Per-minute rate-limit (429) retries. Free tier clears these in <60s and
# Google tells us exactly how long to wait, so we can retry generously.
MAX_RATELIMIT_RETRIES = 6


class TranslationAborted(Exception):
    """Raised when the caller's abort_cb signals the translation is no
    longer wanted (e.g. the user switched to a different video). The caller
    discards the partial result -- it is never cached or uploaded."""


def _chunk(entries, size):
    for i in range(0, len(entries), size):
        yield entries[i:i + size]


def _apply_translation(chunk_entries, translated_map):
    """Overlay translated_map (entry-number -> [lines]) onto the chunk's
    entries in place. Missing numbers keep their source text."""
    applied = 0
    for e in chunk_entries:
        new_lines = translated_map.get(e.index)
        if new_lines:
            # Guard against the model collapsing/expanding line counts
            # wildly: accept whatever it gave (we preserved timecodes),
            # but never leave an entry empty.
            e.lines = [ln for ln in new_lines] or e.lines
            applied += 1
    return applied


def _gender_map_key(tvshow, title, year, season):
    """Cache slug for a series+season gender map ('' when not derivable)."""
    import re as _re
    name = (tvshow or title or '').strip().lower()
    if not name:
        return ''
    slug = _re.sub(r'[^0-9a-z֐-׿]+', '_', name).strip('_')
    return 'gmap_{0}_{1}_s{2}'.format(slug[:60], year or 'x', season or 'x')


def _gender_map_dir():
    d = os.path.join(kodi_utils.profile_dir(), 'gender_maps')
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def _gender_map_load(key):
    try:
        p = os.path.join(_gender_map_dir(), key + '.txt')
        if os.path.exists(p):
            with open(p, encoding='utf-8') as f:
                return f.read().strip()
    except Exception:
        pass
    return ''


def _gender_map_save(key, text):
    try:
        p = os.path.join(_gender_map_dir(), key + '.txt')
        with open(p, 'w', encoding='utf-8') as f:
            f.write(text)
    except Exception:
        pass


def translate_srt(english_srt, source_lang='en', title='', year='',
                  cast=None, is_episode=False, tvshow='', season='',
                  episode='', api_key='', model=None,
                  progress_cb=None, rtl_fix=True, concurrency=None,
                  abort_cb=None, partial_cb=None, stats_out=None):
    """Translate English SRT text to Hebrew SRT text. Returns the
    Hebrew SRT string, or raises gemini.* errors the caller handles.

    Chunks are translated CONCURRENTLY (the best model is slow per call,
    so overlapping calls is the biggest speedup). Each chunk carries the
    previous chunk's source lines as continuity context, so the chunks are
    independent and order-preserving on reassembly.

    rtl_fix: visually reorder Hebrew lines (punctuation/hyphen) so they
    render correctly in Kodi -- matches the DarkSubs behavior."""
    model = model or gemini.DEFAULT_MODEL
    entries = srt.parse(english_srt)
    if not entries:
        raise gemini.GeminiError('Could not parse the English subtitle')

    total = len(entries)
    # Model fallback chain, shared across all worker threads: if the active
    # model's DAILY quota is exhausted mid-movie, advance to the next so the
    # rest of the chunks use the working model. `lock` guards the shared
    # index + the usage counter.
    models = gemini.model_chain(model)
    # Multiple free Gemini keys (primary + 'extra_api_keys') each have their OWN
    # daily quota for the BEST model -- so when one key's daily quota runs out we
    # rotate to the NEXT KEY on the same model, and only drop to a weaker model
    # once EVERY key is exhausted on it. `state` is the shared (model, key) cursor.
    keys = _key_list(api_key)
    state = {'midx': 0, 'kidx': 0}
    lock = threading.Lock()
    # Lines translated per model -> lets us record the PREDOMINANT model in the
    # pool (not just whichever model happened to do the last chunk after a
    # mid-movie quota fallback).
    model_lines = {}

    if concurrency is None:
        concurrency = kodi_utils.get_int('parallel_chunks', DEFAULT_CONCURRENCY)
    concurrency = max(1, min(MAX_CONCURRENCY, int(concurrency or 1)))

    # FAST MODE (opt-in, default OFF = exactly today's behavior): disables the
    # model's internal "thinking" on the per-chunk calls and uses bigger
    # chunks. Pure latency lever -- gated so it can never touch quality unless
    # the user explicitly turns it on.
    fast = kodi_utils.get_bool('fast_mode', False)
    chunk_size = FAST_CHUNK_SIZE if fast else DEFAULT_CHUNK_SIZE
    thinking_budget = 0 if fast else None

    # PASS 0 (default ON): build a dialogue-derived character/gender guide once
    # and hand it to every chunk. Purely additive context + fail-open, so it
    # can only improve gender, never harm the translation.
    #
    # SERIES MEMORY: for episodes, the guide is cached per (show, season) --
    # the same characters recur, so episode 5 reuses (and benefits from) the
    # map built on an earlier episode: consistent gender across the whole
    # season, zero extra analysis calls after the first episode.
    gender_map = ''
    if kodi_utils.get_bool('gender_analysis', True):
        gm_key = _gender_map_key(tvshow, title, year, season) if is_episode else ''
        if gm_key:
            gender_map = _gender_map_load(gm_key)
            if gender_map:
                kodi_utils.log('gender map: reusing cached series map ({0})'.format(gm_key))
        if not gender_map:
            if progress_cb:
                try:
                    progress_cb(0, 0, total, {'message': 'מנתח דמויות ומגדר...',
                                              'phase': 'analyzing', 'model': models[0]})
                except Exception:
                    pass
            try:
                from . import analysis
                gender_map = analysis.character_map(
                    english_srt, keys[0], model=models[0], title=title, year=year,
                    is_episode=is_episode, tvshow=tvshow, season=season, episode=episode)
                if gender_map:
                    quota.note(models[0], 1)  # the analysis call counts as one request
                    if gm_key:
                        _gender_map_save(gm_key, gender_map)
            except Exception as e:
                kodi_utils.log('gender analysis skipped: {0}'.format(e))

    chunks = list(_chunk(entries, chunk_size))
    nchunks = len(chunks)
    concurrency = min(concurrency, nchunks)
    kodi_utils.log('Translating {0} entries in {1} chunks (x{2}); chain={3}'.format(
        total, nchunks, concurrency, models))

    # Per-chunk continuity context (the previous chunk's last few source
    # lines). Computed upfront -> no dependency between chunks -> safe to
    # run in parallel.
    contexts = [[]]
    for ci in range(1, nchunks):
        tail = []
        for e in chunks[ci - 1][-CONTEXT_TAIL:]:
            if e.lines:
                tail.append(' '.join(e.lines))
        contexts.append(tail)

    start_time = time.time()
    done = {'lines': 0, 'chunks': 0}

    def _report():
        if not progress_cb:
            return
        try:
            with lock:
                active = models[state['midx']] if state['midx'] < len(models) else ''
            d = done['lines']
            progress_cb(int(d * 100 / total) if total else 0, d, total, {
                'chunk': done['chunks'], 'chunks': nchunks, 'phase': 'translating',
                'eta': _eta(start_time, d, total), 'model': active,
            })
        except Exception:
            pass

    # Shared prompt metadata reused by both the main chunk call AND any
    # truncation-split subchunk -- so a split never loses cast/gender context.
    pmeta = dict(source_lang=source_lang, title=title, year=year, cast=cast,
                 is_episode=is_episode, tvshow=tvshow, season=season,
                 episode=episode, gender_map=gender_map)

    def _work(ci):
        chunk_entries = chunks[ci]
        ptext = prompt.build(entry_count=len(chunk_entries),
                             prev_context_lines=contexts[ci],
                             chunk=srt.to_blocks(chunk_entries), **pmeta)
        reply = _generate_chunk(keys, models, state, lock, ptext, chunk_entries,
                                pmeta, contexts[ci], thinking_budget, model_lines)
        mapped = srt.parse_model_blocks(reply) if reply else {}
        # JSON RESCUE (#2, rescue-only -> zero impact on the normal path):
        # if the SRT parse clearly failed (model didn't return clean blocks),
        # retry THIS chunk in strict-JSON mode -- where the alternative is
        # leaving English, so it can only help. Same gender/cast context.
        if reply and len(mapped) < int(len(chunk_entries) * 0.8):
            try:
                rescued = _json_rescue_chunk(keys, models, state, lock,
                                             chunk_entries, pmeta, thinking_budget)
                if rescued and len(rescued) > len(mapped):
                    mapped = rescued
            except Exception as e:
                kodi_utils.log('json rescue failed: {0}'.format(e))
        return mapped

    def _aborted():
        try:
            return bool(abort_cb and abort_cb())
        except Exception:
            return False

    # Progressive delivery (#3): apply + RTL each chunk AS it completes, then
    # emit the longest contiguous translated prefix so the caller can show
    # subtitles within seconds instead of after the whole movie. The final
    # text is byte-identical to non-progressive -- only delivery changes.
    applied_upto = {'k': 0}

    def _finalize_chunk(ci):
        _apply_translation(chunks[ci], results.get(ci, {}))
        if rtl_fix:
            for e in chunks[ci]:
                e.lines = rtl.fix_lines(e.lines)
        done['lines'] += len(chunks[ci]); done['chunks'] += 1
        _report()
        if partial_cb:
            k = applied_upto['k']
            while k < nchunks and k in results:
                k += 1
            if k > applied_upto['k']:
                applied_upto['k'] = k
                try:
                    prefix = [e for cj in range(k) for e in chunks[cj]]
                    partial_cb(srt.serialize(prefix), k >= nchunks)
                except Exception as e:
                    kodi_utils.log('partial_cb failed: {0}'.format(e))

    _report()  # show 0% / model immediately
    results = {}
    if _aborted():
        raise TranslationAborted()
    if concurrency == 1:
        for ci in range(nchunks):
            if _aborted():
                raise TranslationAborted()
            results[ci] = _work(ci)
            _finalize_chunk(ci)
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as ex:
            futs = {ex.submit(_work, ci): ci for ci in range(nchunks)}
            for fut in as_completed(futs):
                if _aborted():
                    for f in futs:
                        f.cancel()
                    raise TranslationAborted()
                ci = futs[fut]
                results[ci] = fut.result()  # propagates QuotaExceeded/RateLimited
                _finalize_chunk(ci)

    # Record the model that actually did the most lines (the "predominant"
    # model) -- so a translation that started on the best model and only fell
    # back to a lite model for the tail is labelled by what mostly made it.
    if stats_out is not None and model_lines:
        stats_out['model'] = max(model_lines, key=model_lines.get)

    # Per-chunk apply + RTL already happened in _finalize_chunk, so the whole
    # file is ready -- just serialize it.
    return srt.serialize(entries)


def _eta(start_time, done, total):
    """Rough seconds-remaining estimate from average time per line so far."""
    if done <= 0:
        return None
    elapsed = time.time() - start_time
    per = elapsed / done
    remaining = max(0, (total - done)) * per
    return int(remaining)


def _key_list(primary):
    """Primary key + any 'extra_api_keys' (comma/space/newline/semicolon
    separated), de-duplicated, non-empty. Each is a separate free account, so
    each gives the best model its own daily quota."""
    import re
    out = []
    extra = kodi_utils.get_setting('extra_api_keys', '') or ''
    for k in [primary] + re.split(r'[\s,;]+', extra):
        k = (k or '').strip()
        if k and k not in out:
            out.append(k)
    # No user key -> fall back to the baked community keys (they rotate too).
    if not out:
        out = [k for k in gemini.BAKED_GEMINI_KEYS if k]
    # Append the community proxy as the LAST resort. '' is the proxy sentinel
    # (gemini.generate('', ...) routes to the server-side Worker keys). So the
    # rotation is: the user's own key(s) FIRST, then the community keys when
    # those are exhausted or absent. Keyless users get [''] = proxy-only.
    if gemini.proxy_available() and '' not in out:
        out.append('')
    return out or ['']


def _generate_chunk(keys, models, state, lock, ptext, chunk_entries,
                    pmeta=None, context=None, thinking_budget=None, model_lines=None):
    """Translate one chunk. On a per-DAY quota exhaustion we first rotate to the
    NEXT KEY on the SAME model (keys = separate free accounts, separate quotas),
    and only advance to a weaker model once EVERY key is exhausted on the current
    one. `state` (midx, kidx) is shared across worker threads -> guarded by lock.
    A bad key (InvalidKey) is skipped the same way. Raises QuotaExceeded only
    when every model+key is spent."""
    def _advance(mi, ki):
        # First-thread-wins: only advance if no other thread already moved us.
        if (state['midx'], state['kidx']) != (mi, ki):
            return
        if ki + 1 < len(keys):
            state['kidx'] += 1                      # same model, next key
        else:
            state['midx'] += 1                      # next model, reset keys
            state['kidx'] = 0

    while True:
        with lock:
            mi, ki = state['midx'], state['kidx']
            if mi >= len(models):
                raise gemini.QuotaExceeded('All models + keys exhausted for today')
            model, key = models[mi], keys[ki]
        try:
            reply = _generate_with_retry(key, model, ptext, chunk_entries,
                                         pmeta, context, thinking_budget)
            with lock:
                quota.note(model, 1)  # count one successful request for today
                if model_lines is not None:
                    model_lines[model] = model_lines.get(model, 0) + len(chunk_entries)
            return reply
        except gemini.QuotaExceeded:
            with lock:
                kodi_utils.log('{0} key#{1} daily-exhausted; rotating'.format(model, ki + 1))
                _advance(mi, ki)
        except gemini.InvalidKey:
            with lock:
                if len(keys) > 1:           # skip a bad key when we have others
                    kodi_utils.log('key#{0} rejected; skipping'.format(ki + 1))
                    _advance(mi, ki)
                else:
                    raise


def _generate_with_retry(api_key, model, ptext, chunk_entries,
                         pmeta=None, context=None, thinking_budget=None):
    """Call gemini.generate with handling for truncation + overload.
    On unrecoverable truncation, split the chunk in half and translate
    each separately. Quota/key errors propagate to the caller."""
    overload_tries = 0
    ratelimit_tries = 0
    while True:
        try:
            return gemini.generate(api_key, model, ptext, thinking_budget=thinking_budget)
        except gemini.RateLimited as e:
            ratelimit_tries += 1
            if ratelimit_tries > MAX_RATELIMIT_RETRIES:
                kodi_utils.log('Rate limit persisted, giving up on chunk')
                # Re-raise as QuotaExceeded only if we truly can't proceed;
                # but a persistent per-minute limit usually means the day cap
                # is near too. Surface as RateLimited so caller can decide.
                raise
            wait = getattr(e, 'retry_after', 20)
            kodi_utils.log('Rate limited; waiting {0}s (retry {1})'.format(wait, ratelimit_tries))
            time.sleep(wait)
        except gemini.OverloadError as e:
            overload_tries += 1
            if overload_tries > MAX_OVERLOAD_RETRIES:
                kodi_utils.log('Overload, giving up on chunk: {0}'.format(e))
                return None
            time.sleep(OVERLOAD_BACKOFF_SEC * overload_tries)
        except gemini.TruncatedResponse as e:
            # The chunk was too big for one response. Split and recurse.
            if len(chunk_entries) <= 1:
                # Single entry truncated -- take whatever partial we got.
                return e.partial_text
            mid = len(chunk_entries) // 2
            kodi_utils.log('Truncated; splitting chunk of {0}'.format(len(chunk_entries)))
            left = _translate_subchunk(api_key, model, chunk_entries[:mid], pmeta, context, thinking_budget)
            right = _translate_subchunk(api_key, model, chunk_entries[mid:], pmeta, context, thinking_budget)
            # Merge the two partial SRT replies into one block of text.
            return (left or '') + '\n\n' + (right or '')


def _translate_subchunk(api_key, model, sub_entries, pmeta=None, context=None,
                        thinking_budget=None):
    """Re-issue translation for a sub-slice after a truncation split.
    Rebuilds the FULL prompt (same cast + gender guide + context) for just
    these entries -- so a split never degrades gender/quality."""
    build_kwargs = dict(pmeta) if pmeta else {'source_lang': 'en'}
    ptext = prompt.build(entry_count=len(sub_entries),
                         prev_context_lines=context,
                         chunk=srt.to_blocks(sub_entries), **build_kwargs)
    try:
        return gemini.generate(api_key, model, ptext, thinking_budget=thinking_budget)
    except gemini.GeminiError as e:
        kodi_utils.log('Subchunk failed: {0}'.format(e))
        return None


def _extract_json_array(text):
    import json as _json
    import re as _re
    if not text:
        return None
    t = text.strip()
    if '```' in t:
        m = _re.search(r'```(?:json)?\s*([\s\S]*?)```', t)
        if m:
            t = m.group(1).strip()
    try:
        v = _json.loads(t)
        return v if isinstance(v, list) else None
    except Exception:
        pass
    s, e = t.find('['), t.rfind(']')
    if 0 <= s < e:
        try:
            v = _json.loads(t[s:e + 1])
            return v if isinstance(v, list) else None
        except Exception:
            return None
    return None


def _json_rescue_chunk(keys, models, state, lock, chunk_entries, pmeta, thinking_budget):
    """Retry a chunk in strict-JSON mode when the SRT parse failed. Returns
    {entry.index: [lines]} or None. Uses the same gender/cast context, so the
    rescue keeps quality; it only changes the OUTPUT format to a JSON array
    the model can't malform. Single active-(model, key) call, best-effort."""
    NL = '⏎'  # ⏎ marks an in-cue line break
    src_cues = [NL.join(e.lines) for e in chunk_entries]
    with lock:
        mi, ki = state['midx'], state['kidx']
        model = models[mi] if mi < len(models) else models[-1]
        key = keys[ki] if ki < len(keys) else keys[0]
    pm = pmeta or {}
    ptext = prompt.build_json(
        source_lang=pm.get('source_lang', 'en'), cast=pm.get('cast'),
        gender_map=pm.get('gender_map', ''), src_cues=src_cues, nl=NL)
    reply = gemini.generate(key, model, ptext, temperature=0.2,
                            thinking_budget=thinking_budget, response_json=True)
    arr = _extract_json_array(reply)
    if not isinstance(arr, list) or len(arr) != len(chunk_entries):
        return None
    out = {}
    for e, t in zip(chunk_entries, arr):
        out[e.index] = str(t).split(NL)
    return out
