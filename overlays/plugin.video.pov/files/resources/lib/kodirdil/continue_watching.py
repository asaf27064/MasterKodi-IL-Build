# -*- coding: utf-8 -*-
"""One "continue watching" row: resume what you started, else the next episode.

WHY THIS EXISTS
---------------
POV keeps these as two separate lists, fed by two different tables:

    build_in_progress_episode -> the `progress` table (resume points). Finish the
        episode and the bookmark is deleted, so the series drops off the row.
    build_next_episode        -> the `watched_status` table (last watched episode
        per show, +1). A show you are twenty minutes into only shows up here once
        the PREVIOUS episode was marked watched.

Every widget in this build pointed at the first one, so finishing an episode made
the series vanish instead of advancing to the next one (Asaf, 2026-08-24). One
row that does both is what a viewer actually expects.

HOW
---
Nothing is re-rendered by hand: this drives POV'S OWN episode builder twice --
once in `in_progress` mode, once in `next_episode_pov` mode -- and concatenates
what it produces. Artwork, context menus, resume points, unaired handling, the
watched flags and our Hebrew-subtitle props therefore stay identical to the stock
lists, and an upstream change to any of that lands here for free.

RULES
-----
* A show appears ONCE. Any show holding a resume point is dropped from the
  next-episode half, because the resume point is the more precise answer -- it is
  the episode you are actually inside.
* Order is by recency across both halves (`last_played`, the same column POV
  sorts those lists by), newest first, so what you watched last is first.
* Unaired episodes sink to the bottom, never to the top, however recent the show
  is -- an episode that has not aired cannot be continued. (Whether they appear
  at all remains POV's own `nextep.include_unaired` setting; we do not override
  the user's choice, we only refuse to rank one first.)
* Entries with no timestamp sort last rather than disappearing.
"""
from menus.episodes import Menu
from modules import kodi_utils
from modules.kodi_utils import local_string as ls

# `progress` row layout, from modules/cache.py:
#   0 db_type | 1 media_id | 2 season | 3 episode | 4 resume_point
#   5 curr_time | 6 last_played | 7 resume_id | 8 title
_DB_TYPE, _MEDIA_ID, _SEASON, _EPISODE, _LAST_PLAYED, _TITLE = 0, 1, 2, 3, 6, 8


class ContinueWatching(Menu):
	def _resume_source(self):
		"""Episodes holding a resume point, most recently played first.

		Built straight off the bookmark rows instead of through
		get_in_progress_items(): that helper re-sorts by title when the user's
		list-sort setting says so, and this row has to be ordered by recency.
		GET_BM already returns the rows last_played DESC, so the enumeration
		order IS the recency order -- handed to the builder as `sort`, which is
		what it sorts the in-progress half by.
		"""
		out = []
		try:
			rows = (self.bookmarks or {}).values()
		except Exception:
			return out
		for row in rows:
			try:
				if row[_DB_TYPE] != 'episode':
					continue
				out.append({
					'media_ids': {'tmdb': row[_MEDIA_ID]}, 'media_id': row[_MEDIA_ID],
					'title': row[_TITLE], 'season': row[_SEASON], 'episode': row[_EPISODE],
					'last_played': row[_LAST_PLAYED] or '', 'sort': len(out)})
			except Exception:
				pass
		return out

	@staticmethod
	def _show_id(item):
		try:
			return str(item.get('media_id') or (item.get('media_ids') or {}).get('tmdb') or '')
		except Exception:
			return ''

	@classmethod
	def _dedupe(cls, resume, nexts):
		"""A show with a resume point must not also appear as a next episode."""
		seen = {cls._show_id(i) for i in resume}
		seen.discard('')
		return [i for i in nexts if cls._show_id(i) not in seen]

	def _build(self, list_type, source):
		"""Run POV's own builder over one source list and hand back its items."""
		self.items = []
		self.append = self.items.append      # rebind: build_episode_content uses it
		self.list_type, self.list = list_type, source
		if not source:
			return []
		try:
			return list(self.worker())
		except Exception:
			return []

	@staticmethod
	def _stamp_recency(items, source):
		"""Give the resume half the same recency property the next half carries.

		The in-progress branch of build_episode_content only writes
		`pov_sort_order` -- which is the index we handed it -- so the timestamp
		is copied across from our own source list rather than guessed.
		"""
		for entry in items:
			try:
				idx = int(entry[1].getProperty('pov_sort_order'))
				entry[1].setProperty('pov_last_played', source[idx].get('last_played') or '')
			except Exception:
				pass
		return items

	@staticmethod
	def _order(items):
		"""Newest first; unaired always last; no timestamp after everything else.

		One sort, so it cannot fight itself: the key ranks aired-before-unaired
		first, then has-a-timestamp, then the timestamp. Python's sort is stable,
		so ties keep the order each half already had (recency for the resume
		half, POV's own next-episode sort for the other)."""
		def key(entry):
			try:
				li = entry[1]
				stamp = li.getProperty('pov_last_played') or ''
				return (li.getProperty('pov_unaired') != 'true', stamp != '', stamp)
			except Exception:
				return (False, False, '')
		items.sort(key=key, reverse=True)
		return items

	def run(self):
		handle = int(kodi_utils.argv1())
		resume = self._resume_source()
		resume_items = self._stamp_recency(self._build('in_progress', resume), resume)

		# nextep_* settings must be set up before the next half is built
		self._setup_next_episode(self.params.get)
		next_items = self._build('next_episode_pov', self._dedupe(resume, self.list or []))

		items = self._order(resume_items + next_items)
		if items:
			kodi_utils.add_items(handle, items)
		kodi_utils.set_category(handle, ls(self.params.get('name')))
		kodi_utils.set_sort_method(handle, 'unsorted')
		kodi_utils.set_content(handle, 'episodes')
		kodi_utils.end_directory(handle, False)
		kodi_utils.set_view_mode('view.episodes_lists', 'episodes', self.is_widget)
