"""Compatibility helpers for providers ported from POV 6.10.16.
Keep CocoScrapers filtering and result conventions.
"""
from concurrent.futures import Future
from threading import Thread as _Thread
from cocoscrapers.modules import source_utils as _original

def __getattr__(name):
    return getattr(_original, name)

class Thread(_Thread):
    def __init__(self, target, *args):
        super().__init__(target=target, args=args)

class SourceRegistry:
    @classmethod
    def clear_all(cls):
        with cls._lock:
            cls._cache.clear()

    def get_future(self, key):
        with self._lock:
            if key not in self._cache:
                self._cache[key] = Future()
            return self._cache[key]

def aliases_to_array(aliases, filter=None):
    if not aliases:
        return []
    countries = [filter] if isinstance(filter, str) else filter
    return [item if isinstance(item, str) else item.get('title', '')
            for item in aliases if isinstance(item, str) or not countries or item.get('country') in countries]
