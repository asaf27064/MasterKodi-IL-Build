# -*- coding: utf-8 -*-
# Google Generative Language API client. Only the pieces we need:
# generateContent (translation) and a models list (connection test).
# Bring your own free key from aistudio.google.com.

import json
import time
import urllib.parse

try:
    import requests
except ImportError:
    requests = None

from . import kodi_utils

API_BASE = 'https://generativelanguage.googleapis.com/v1beta'
REQUEST_TIMEOUT = 120

# ---- Groq (optional, very fast Llama inference; OpenAI-compatible API) ----
# A separate FREE key (console.groq.com) goes in the 'groq_api_key' setting.
# Groq is offered as an extra MODEL the user can pick for speed; Gemini stays
# the quality default. Maps our model id -> Groq's model id.
GROQ_URL = 'https://api.groq.com/openai/v1/chat/completions'
GROQ_MODELS = {'groq-llama-3.3-70b': 'llama-3.3-70b-versatile'}


# Baked community Gemini keys (free-tier) so AI translation works out of the
# box -- used ONLY when the user hasn't entered their own key. They rotate via
# the multi-key fallback, and the community pool means each title is translated
# once then served to everyone, so the shared quota stretches.
# NOTE: visible in the shipped addon; free-tier (no billing) -> worst case if
# leaked is quota exhaustion. A user's own key always takes precedence.
# Community Gemini keys for out-of-the-box translation. KEEP THIS EMPTY in the
# public repo: baking real keys exposes them on GitHub and Google auto-deletes
# them for abuse protection. For shared keys, route translation through the
# Cloudflare Worker instead (keys stay server-side as Worker secrets). Users
# can always set their own key in settings.
BAKED_GEMINI_KEYS = []


def user_key():
    return (kodi_utils.get_setting('api_key', '') or '').strip()


def primary_key():
    """The user's key if set, else the first baked community key."""
    return user_key() or (BAKED_GEMINI_KEYS[0] if BAKED_GEMINI_KEYS else '')


def proxy_available():
    """True if the community translation proxy (Cloudflare Worker /v1/translate)
    is reachable. The Worker holds the shared Gemini keys server-side, so keyless
    users still get translation without any key ever shipping in the addon."""
    try:
        from . import pool
        return bool(pool.enabled() and pool._base())
    except Exception:
        return False


def have_keys():
    """True if translation is possible at all: the user's own key, baked keys,
    or the community proxy."""
    return bool(user_key() or BAKED_GEMINI_KEYS or proxy_available())


def _is_groq(model):
    return (model or '') in GROQ_MODELS


def _groq_key():
    return (kodi_utils.get_setting('groq_api_key', '') or '').strip()

# Default model. 2.5 Flash is the highest-quality model -- the most
# natural Hebrew. The community pool + free re-sync make fresh
# translations rare, so the lower daily quota rarely bites; and if it
# ever does, model_chain() automatically falls back to the high-quota
# lite models below. Overridable in settings.
DEFAULT_MODEL = 'gemini-2.5-flash'

# Each model has its OWN separate free-tier daily quota. When one is
# exhausted (429 per-day) we transparently fall through this list so a
# movie never dies mid-translation. Ordered by QUALITY (best first) so a
# forced downgrade always steps to the next-BEST model still available,
# never straight to the weakest. The 'lite' (quality 1) model is the true
# last resort (it has the biggest quota, so it can always finish the tail).
# The user's chosen model is always tried first, ahead of this list.
FALLBACK_MODELS = [
    'gemini-2.5-flash',        # quality 3 - best
    'gemini-3.1-flash-lite',   # quality 2 - newest lite, big quota
    'gemini-2.0-flash',        # quality 2
    'gemini-2.5-flash-lite',   # quality 1 - last resort, biggest quota
]


def model_chain(chosen):
    """Ordered, de-duplicated model list: chosen first, then fallbacks.
    Groq models are dropped when no Groq key is configured, so a Groq pick
    cleanly falls through to Gemini."""
    chain = [chosen] if chosen else []
    for m in FALLBACK_MODELS:
        if m not in chain:
            chain.append(m)
    if not _groq_key():
        chain = [m for m in chain if not _is_groq(m)]
        # If the user PICKED Groq but has no Groq key, lead with the quality
        # default (2.5 Flash) rather than a lite fallback.
        if chosen and _is_groq(chosen):
            if DEFAULT_MODEL in chain:
                chain.remove(DEFAULT_MODEL)
            chain.insert(0, DEFAULT_MODEL)
    return chain or [DEFAULT_MODEL]


# Translation-quality tier per model (higher = better Hebrew). The full
# "flash" model is meaningfully better than the "-lite" tiers; lite is
# faster with a bigger free daily quota. Used to (a) tell the user which
# tier is running and (b) decide whether a pooled sub is worth keeping or
# could be upgraded by a stronger model.
MODEL_QUALITY = {
    'gemini-2.5-flash': 3,        # best quality
    'gemini-3.1-flash-lite': 2,   # newest lite, good + highest quota
    'gemini-2.0-flash': 2,
    'groq-llama-3.3-70b': 2,      # very fast, good (separate Groq quota)
    'gemini-2.5-flash-lite': 1,   # fastest, lowest quality
}

# Short human label (explicit Hebrew quality tier + model id) for UI. The
# ★ on the top model makes "is this the best?" answerable at a glance.
MODEL_LABELS = {
    'gemini-2.5-flash': '★ איכות מרבית (2.5 Flash)',
    'gemini-3.1-flash-lite': 'איכות טובה (3.1 Flash-Lite)',
    'gemini-2.0-flash': 'איכות טובה (2.0 Flash)',
    'groq-llama-3.3-70b': '⚡ מהיר מאוד (Groq Llama 70B)',
    'gemini-2.5-flash-lite': 'איכות בסיסית (2.5 Flash-Lite)',
}

# The strongest model we'll try to upgrade to (for "re-translate better").
BEST_MODEL = 'gemini-2.5-flash'


def quality(model):
    """0-3 quality rank for a model id (unknown -> 1)."""
    return MODEL_QUALITY.get((model or '').strip(), 1)


def is_best(model):
    """True if `model` is the highest-quality model we know of."""
    return quality(model) >= quality(BEST_MODEL)


def label(model):
    """Human label for a model id, falling back to the raw id."""
    model = (model or '').strip()
    return MODEL_LABELS.get(model, model or 'Gemini')


class GeminiError(Exception):
    """Any non-recoverable API failure."""


class QuotaExceeded(GeminiError):
    """DAILY request limit hit (HTTP 429, PerDay quota). Not retryable
    until UTC midnight."""


class RateLimited(GeminiError):
    """PER-MINUTE rate limit hit (HTTP 429, PerMinute quota). Retryable
    after a short wait -- Google tells us how long via RetryInfo."""
    def __init__(self, message, retry_after=20):
        super(RateLimited, self).__init__(message)
        self.retry_after = retry_after


class OverloadError(GeminiError):
    """Service-side overload (HTTP 500/503). Retryable with backoff."""


class InvalidKey(GeminiError):
    """Key missing / revoked / malformed (HTTP 400/403)."""


class TruncatedResponse(GeminiError):
    """Model hit its output-token cap mid-reply. partial_text holds
    what we did get -- caller should retry with a smaller chunk."""
    def __init__(self, message, partial_text=''):
        super(TruncatedResponse, self).__init__(message)
        self.partial_text = partial_text


def _require_requests():
    if not requests:
        raise GeminiError('python-requests is not available')


def _err_reason(resp):
    """Surface Google's actual error message instead of a bare code."""
    try:
        err = (resp.json() or {}).get('error') or {}
        return (err.get('message') or err.get('status') or '')[:300]
    except Exception:
        return ''


def _classify_429(resp):
    """Decide whether a 429 is the per-DAY cap (fatal until midnight) or a
    per-MINUTE rate limit (retryable). Returns (is_daily, retry_after_sec).

    Google's 429 body carries google.rpc.QuotaFailure (with a quotaId like
    'GenerateRequestsPerMinutePerProjectPerModel-FreeTier' or '...PerDay...')
    and optionally google.rpc.RetryInfo.retryDelay ('30s'). We read both.
    Default to NON-daily (retryable) when ambiguous -- safer to retry than
    to wrongly tell the user their day is over."""
    retry_after = 20
    is_daily = False
    try:
        err = (resp.json() or {}).get('error') or {}
        details = err.get('details') or []
        for d in details:
            t = (d.get('@type') or '')
            if 'QuotaFailure' in t:
                for v in (d.get('violations') or []):
                    qid = (v.get('quotaId') or '') + (v.get('quotaMetric') or '')
                    low = qid.lower()
                    if 'perday' in low or 'per_day' in low:
                        is_daily = True
            if 'RetryInfo' in t:
                rd = str(d.get('retryDelay') or '').strip().rstrip('s')
                try:
                    retry_after = max(1, int(float(rd)))
                except (ValueError, TypeError):
                    pass
        # Fallback: the human message sometimes only says "per day".
        msg = (err.get('message') or '').lower()
        if 'per day' in msg or 'daily' in msg:
            is_daily = True
    except Exception:
        pass
    return is_daily, retry_after


def test_key(api_key, model=DEFAULT_MODEL):
    """Cheap sanity check: list the user's models, confirm `model`
    exists. Returns the matched model id. Raises on any problem."""
    _require_requests()
    if not api_key:
        raise InvalidKey('No API key provided')
    url = '{0}/models?key={1}'.format(API_BASE, urllib.parse.quote(api_key, safe=''))
    try:
        r = requests.get(url, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as e:
        raise GeminiError('Network error: {0}'.format(e))

    if r.status_code in (400, 403):
        raise InvalidKey('Key rejected ({0}): {1}'.format(r.status_code, _err_reason(r)))
    if r.status_code == 429:
        raise QuotaExceeded('Quota exceeded while testing key')
    if r.status_code >= 500:
        raise OverloadError('Google service error ({0})'.format(r.status_code))
    if r.status_code != 200:
        raise GeminiError('Unexpected status {0}'.format(r.status_code))

    try:
        models = (r.json() or {}).get('models', [])
    except ValueError:
        raise GeminiError('Bad JSON from models endpoint')

    names = set()
    for m in models:
        nm = (m.get('name') or '').split('/')[-1]
        if nm:
            names.add(nm)
    # Accept exact match, or a family match (e.g. user typed a short id).
    if model in names:
        return model
    for nm in names:
        if model in nm or nm in model:
            return nm
    # Key works but the chosen model isn't in the list -- still a useful
    # signal; let the caller decide. We return the model unchanged.
    return model


def _supports_thinking(model):
    """Only the 2.5 / 3.x families accept generationConfig.thinkingConfig.
    Sending it to e.g. gemini-2.0-flash returns HTTP 400, so gate on this."""
    m = (model or '')
    return '2.5' in m or m.startswith('gemini-3') or '-3.' in m


def _generate_groq(model, prompt_text, temperature=0.2, response_json=False):
    """Groq chat-completions (OpenAI-compatible). Uses the separate Groq key.
    Raises QuotaExceeded when no Groq key is set so the model chain falls
    through to Gemini."""
    _require_requests()
    key = _groq_key()
    if not key:
        raise QuotaExceeded('No Groq key set; falling through to Gemini')
    groq_id = GROQ_MODELS.get(model, 'llama-3.3-70b-versatile')
    body = {
        'model': groq_id,
        'temperature': temperature,
        'messages': [{'role': 'user', 'content': prompt_text}],
    }
    if response_json:
        body['response_format'] = {'type': 'json_object'}
    try:
        r = requests.post(GROQ_URL, json=body, timeout=REQUEST_TIMEOUT,
                          headers={'Authorization': 'Bearer ' + key,
                                   'Content-Type': 'application/json'})
    except requests.RequestException as e:
        raise GeminiError('Groq network error: {0}'.format(e))
    if r.status_code in (400, 401, 403):
        raise InvalidKey('Groq key rejected ({0}): {1}'.format(r.status_code, _err_reason(r)))
    if r.status_code == 429:
        # Groq free tier is per-minute; treat as retryable rate limit.
        raise RateLimited('Groq rate limit (HTTP 429)', retry_after=10)
    if r.status_code >= 500:
        raise OverloadError('Groq overloaded (HTTP {0})'.format(r.status_code))
    if r.status_code != 200:
        raise GeminiError('Groq status {0}: {1}'.format(r.status_code, _err_reason(r)))
    try:
        data = r.json()
        text = (data['choices'][0]['message']['content'] or '').strip()
    except Exception:
        raise GeminiError('Bad JSON from Groq')
    if not text:
        raise GeminiError('Groq returned empty text')
    return text


def _generate_proxy(model, prompt_text, temperature=0.2, thinking_budget=None,
                    response_json=False, _retried=False):
    """Translate via the community Worker proxy (keys live server-side). We
    send the model + prompt; the Worker rotates its keys and returns the text.
    A 429 'quota' means all server keys are exhausted for THIS model -> raise
    QuotaExceeded so the caller's model_chain advances to the next model and
    asks again. Other failures map to the same typed errors as direct Gemini."""
    _require_requests()
    from . import pool
    base = pool._base()
    if not base:
        raise InvalidKey('No API key and no translation proxy configured')
    body = {'model': model, 'prompt': prompt_text, 'temperature': temperature}
    if thinking_budget is not None and _supports_thinking(model):
        body['thinking_budget'] = int(thinking_budget)
    if response_json:
        body['response_json'] = True
    headers = {'Content-Type': 'application/json', 'User-Agent': pool.USER_AGENT}
    tok = pool._token()
    if tok:
        headers['X-Gears-Key'] = tok
    try:
        # Big chunks on the full model can generate for several minutes --
        # a short read timeout here used to KILL the whole translation
        # ("Proxy network error: Read timed out (120)"). Long read timeout +
        # timeouts are retryable (OverloadError), not fatal.
        r = requests.post(base.rstrip('/') + '/v1/translate', json=body,
                          timeout=(15, 300), headers=headers)
    except requests.Timeout:
        raise OverloadError('Proxy timeout -- retrying')
    except requests.RequestException as e:
        raise GeminiError('Proxy network error: {0}'.format(e))

    if r.status_code == 429:
        kind = ''
        try:
            kind = (r.json() or {}).get('kind') or ''
        except Exception:
            pass
        if kind == 'rate':
            raise RateLimited('Proxy per-minute rate limit', retry_after=15)
        # 'quota' from the proxy is AMBIGUOUS: Google often returns a generic
        # 429 for per-MINUTE bursts too (parallel chunks on shared keys), which
        # used to downgrade the model mid-movie even though the daily quota was
        # fine. Verify before downgrading: wait out the minute window once and
        # retry; only a PERSISTENT 429 is treated as real daily exhaustion.
        if not _retried:
            kodi_utils.log('proxy 429 (quota?) on {0} -- waiting 30s to verify'.format(model))
            time.sleep(30)
            return _generate_proxy(model, prompt_text, temperature=temperature,
                                   thinking_budget=thinking_budget,
                                   response_json=response_json, _retried=True)
        raise QuotaExceeded('Proxy: all community keys exhausted for this model')
    if r.status_code == 503:
        # Worker reachable but no keys configured server-side.
        raise InvalidKey('Translation proxy has no keys configured')
    if r.status_code in (400, 401, 403):
        raise InvalidKey('Proxy rejected ({0}): {1}'.format(r.status_code, _err_reason(r)))
    if r.status_code >= 500:
        raise OverloadError('Proxy overloaded (HTTP {0})'.format(r.status_code))
    if r.status_code != 200:
        raise GeminiError('Proxy status {0}: {1}'.format(r.status_code, _err_reason(r)))
    try:
        text = ((r.json() or {}).get('text') or '').strip()
    except ValueError:
        raise GeminiError('Bad JSON from proxy')
    if not text:
        raise GeminiError('Proxy returned empty text')
    return text


def generate(api_key, model, prompt_text, temperature=0.2, max_output_tokens=None,
             thinking_budget=None, response_json=False):
    """Single generation call. Returns the model's text. Raises typed errors
    the orchestrator can react to. Routes Groq models to Groq's OpenAI-style
    endpoint; everything else to Gemini.

    thinking_budget: if set (e.g. 0) AND the model supports it, caps the
    model's internal 'thinking' tokens -- a pure latency lever. Left None
    (default) the model decides, i.e. exactly today's behavior.
    response_json: ask for a strict JSON response (used only by the JSON
    rescue path; the normal SRT path leaves this False -> unchanged)."""
    _require_requests()
    if _is_groq(model):
        return _generate_groq(model, prompt_text, temperature, response_json)
    if not api_key:
        # No user/baked key -> translate through the community Worker proxy,
        # which holds the shared keys server-side.
        return _generate_proxy(model, prompt_text, temperature, thinking_budget,
                               response_json)

    url = '{0}/models/{1}:generateContent?key={2}'.format(
        API_BASE, urllib.parse.quote(model, safe=''),
        urllib.parse.quote(api_key, safe=''))

    gen_config = {
        'temperature': temperature,
        # Low randomness; subtitles want faithful, consistent output.
        'topP': 0.95,
    }
    if max_output_tokens:
        gen_config['maxOutputTokens'] = int(max_output_tokens)
    if thinking_budget is not None and _supports_thinking(model):
        gen_config['thinkingConfig'] = {'thinkingBudget': int(thinking_budget)}
    if response_json:
        gen_config['responseMimeType'] = 'application/json'

    payload = {
        'contents': [{'role': 'user', 'parts': [{'text': prompt_text}]}],
        'generationConfig': gen_config,
        # Don't let safety filters silently nuke dialogue (violence,
        # profanity in films is normal). BLOCK_NONE where allowed.
        'safetySettings': [
            {'category': c, 'threshold': 'BLOCK_NONE'} for c in (
                'HARM_CATEGORY_HARASSMENT', 'HARM_CATEGORY_HATE_SPEECH',
                'HARM_CATEGORY_SEXUALLY_EXPLICIT', 'HARM_CATEGORY_DANGEROUS_CONTENT')
        ],
    }

    try:
        r = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT,
                          headers={'Content-Type': 'application/json'})
    except requests.RequestException as e:
        raise GeminiError('Network error: {0}'.format(e))

    if r.status_code in (400, 403):
        reason = _err_reason(r)
        # 400 can also mean a malformed request; but for our fixed payload
        # the overwhelmingly common cause is a bad key.
        raise InvalidKey('Rejected ({0}): {1}'.format(r.status_code, reason))
    if r.status_code == 429:
        # 429 covers BOTH per-minute rate limits and the per-day cap.
        # Parse the body to tell them apart so we retry the (common,
        # transient) per-minute case instead of giving up.
        is_daily, retry_after = _classify_429(r)
        if is_daily:
            raise QuotaExceeded('Daily Gemini quota exhausted (HTTP 429)')
        raise RateLimited('Per-minute rate limit (HTTP 429)', retry_after=retry_after)
    if r.status_code >= 500:
        raise OverloadError('Google overloaded (HTTP {0})'.format(r.status_code))
    if r.status_code != 200:
        raise GeminiError('Unexpected status {0}: {1}'.format(r.status_code, _err_reason(r)))

    try:
        data = r.json()
    except ValueError:
        raise GeminiError('Bad JSON from generateContent')

    candidates = data.get('candidates') or []
    if not candidates:
        # promptFeedback.blockReason means the whole prompt was blocked.
        block = (data.get('promptFeedback') or {}).get('blockReason')
        if block:
            raise GeminiError('Prompt blocked by Google: {0}'.format(block))
        raise GeminiError('Empty response (no candidates)')

    cand = candidates[0]
    parts = ((cand.get('content') or {}).get('parts')) or []
    text = ''.join(p.get('text', '') for p in parts)
    finish = cand.get('finishReason') or ''

    if finish == 'MAX_TOKENS':
        raise TruncatedResponse('Model hit output token cap', partial_text=text)
    if not text.strip():
        raise GeminiError('Model returned empty text (finishReason={0})'.format(finish))

    return text
