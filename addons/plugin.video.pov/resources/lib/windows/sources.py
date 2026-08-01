import json
from windows import BaseDialog
from modules.debrid import Source
from modules.kodi_utils import media_path, hide_busy_dialog, dialog, select_dialog, ok_dialog, local_string as ls
from modules.kodi_utils import get_setting
from modules.settings import get_art_provider, info_icons, provider_sort_ranks
# from modules.kodi_utils import logger

fanart_empty = BaseDialog.fanart
poster_empty = media_path('box_office.png')
info_icons_dict = {k: media_path(v) for k, v in info_icons()}
extra_info_choices = (
	('PACK', '[B]PACK[/B]'), ('DOLBY VISION', '[B]D/VISION[/B]'), ('HIGH DYNAMIC RANGE (HDR)', '[B]HDR[/B]'), ('HYBRID', '[B]HYBRID[/B]'), ('AV1', '[B]AV1[/B]'),
	('HEVC (X265)', '[B]HEVC[/B]'), ('REMUX', 'REMUX'), ('BLURAY', 'BLURAY'), ('SDR', 'SDR'), ('3D', '3D'), ('DOLBY ATMOS', 'ATMOS'), ('DOLBY TRUEHD', 'TRUEHD'),
	('DOLBY DIGITAL EX', 'DD-EX'), ('DOLBY DIGITAL PLUS', 'DD+'), ('DOLBY DIGITAL', 'DD'), ('DTS-HD MASTER AUDIO', 'DTS-HD MA'), ('DTS-X', 'DTS-X'),
	('DTS-HD', 'DTS-HD'), ('DTS', 'DTS'), ('ADVANCED AUDIO CODING (AAC)', 'AAC'), ('MP3', 'MP3'), ('8 CHANNEL AUDIO', '8CH'), ('7 CHANNEL AUDIO', '7CH'),
	('6 CHANNEL AUDIO', '6CH'), ('2 CHANNEL AUDIO', '2CH'), ('DVD SOURCE', 'DVD'), ('WEB SOURCE', 'WEB'), ('MULTIPLE LANGUAGES', 'MULTI-LANG'), ('SUBTITLES', 'SUBS')
)
quality_choices, pack_check = ('4K', '1080P', '720P', 'SD', 'TELE', 'CAM', 'SCR'), ('true', 'show', 'season')
extra_info_str, down_file_str, browse_pack_str, down_pack_str, cloud_str = ls(32605), ls(32747), ls(32746), ls(32007), ls(32016)
filter_str, clr_filter_str, filters_ignored, start_full_scrape = ls(32152), ls(32153), ls(32686), ls(32529)
filter_quality, filter_provider, filter_title, filter_extraInfo = ls(32154), ls(32157), ls(32679), ls(32169)
run_plugin_str, ignored_str = 'RunPlugin(%s)', '[B][COLOR dodgerblue](%s)[/COLOR][/B]'
en_seek_str, check_str = '[B]EN: PLAY (SEEK ENABLED)[/B]', '[B]CHECK CACHE STATUS[/B]'
cache_str, airlock_str = ('CACHED', 'CACHED [B]%s[/B]'), ls(32016).replace('Add', 'Airlock')
string, upper, lower = str, str.upper, str.lower

########### KODIRDIL - Hebrew Subtitles Integration ###########
from kodirdil import hebrew_subtitles_search_utils
from kodirdil import db_utils
from kodirdil.websites import hebrew_embedded
from modules.kodi_utils import set_property as set_window_property

def publish_player_release(source):
	"""Publish the chosen source's release name on Window(10000) as
	`subs.player_filename`.

	Subtitle services (GearsAI/DarkSubs) read this property to learn WHICH
	release is playing. POV never set it -- and for debrid streams the player's
	own file is a bare UUID (e.g. 997ec702-0c77-...), so there is no other
	channel to recover the name from. Without it GearsAI fell back to
	VideoPlayer.Tagline (the movie's marketing tagline) and matched subtitles
	with an EMPTY release, which is how an out-of-sync pool subtitle got served.
	Gears publishes the same property in modules/player.py.
	"""
	try:
		if not isinstance(source, dict): return
		name = source.get('display_name') or source.get('name') or ''
		if name: set_window_property('subs.player_filename', string(name))
	except: pass

def is_hebrew_subtitles_enabled():
	return get_setting('hebrew_subtitles.enable_matching', 'true') == 'true'

def get_minimum_sync_percent():
	return int(get_setting('hebrew_subtitles.minimum_sync_percent', '70') or '70')

def is_embedded_search_enabled():
	return get_setting('hebrew_subtitles.match_embedded', 'true') == 'true'
###############################################################

class SourceResults(BaseDialog):
	def __init__(self, *args, **kwargs):
		BaseDialog.__init__(self, args)
		self.window_style = kwargs.get('window_style', 'list contrast details')
		self.window_id = kwargs.get('window_id')
		self.results = kwargs.get('results')
		self._results = {}
		self.meta = kwargs.get('meta')
		########### KODIRDIL - Tried sources tracking key (per media item) ###########
		try:
			_mg = self.meta.get
			self.tried_sources_key = 'tried_sources_%s_%s_%s' % (_mg('tmdb_id', ''), _mg('season', ''), _mg('episode', ''))
		except Exception:
			self.tried_sources_key = 'tried_sources_unknown'
		##############################################################################
		self.info_highlights_dict = kwargs.get('scraper_settings')
		########### KODIRDIL - Drop the previous item's release name ###########
		# Gears clears subs.player_filename when playback stops; POV has no such
		# hook, so clear it as the source list opens. A STALE name is worse than
		# none -- GearsAI would match subtitles against the previous release.
		# ONLY when nothing is playing: browsing the source list MID-PLAYBACK
		# and backing out must keep the ACTIVE item's release, or every later
		# subtitle ranking falls back to the marketing tagline and the match
		# percentages flip (Asaf, 2026-08-01: 44/40/38 became 25/25/23).
		try:
			import xbmc as _xbmc
			if not _xbmc.Player().isPlaying():
				set_window_property('subs.player_filename', '')
		except: pass
		########################################################################
		self.prescrape = kwargs.get('prescrape')
		if kwargs.get('filters_ignored'): self.filters_ignored = ignored_str % filters_ignored
		else: self.filters_ignored = ''
		self.make_items()
		self.set_properties()

	def onInit(self):
		self.filter_applied = False
		self.win = self.getControl(self.window_id)
		self.win.addItems(self.item_list)
		self.setFocusId(self.window_id)

	def run(self):
		self.doModal()
		self.clearProperties()
		hide_busy_dialog()
		return self.selected

	########### KODIRDIL - Tried sources tracking + HDR detection ###########
	def _home_prop(self, key):
		from xbmcgui import Window
		return Window(10000).getProperty(key)

	def _set_home_prop(self, key, value):
		from xbmcgui import Window
		Window(10000).setProperty(key, value)

	def _get_tried_sources(self):
		try:
			data = self._home_prop(self.tried_sources_key)
			return set(data.split(',')) if data else set()
		except: return set()

	def _add_tried_source(self, source):
		try:
			identifier = source.get('hash') or source.get('display_name', '')
			if identifier:
				tried = self._get_tried_sources()
				tried.add(identifier)
				if len(tried) > 50:
					tried = set(list(tried)[-50:])
				self._set_home_prop(self.tried_sources_key, ','.join(tried))
		except: pass

	def _is_tried_source(self, source):
		try:
			identifier = source.get('hash') or source.get('display_name', '')
			return identifier in self._get_tried_sources()
		except: return False

	_hdr_tags = ('[B]HDR[/B]', '[B]D/VISION[/B]')
	_hdr_words = ('.HDR.', '.HDR10.', '.HDR10PLUS.', '.HDR10P.', '.DV.', '.DOVI.', '.DVHE.', '.DOLBY.VISION.', '.DOLBYVISION.', '.HLG.')

	def _is_hdr_item(self, item):
		if any(x in item.getProperty('tikiskins.extra_info') for x in self._hdr_tags): return True
		n = item.getProperty('tikiskins.name')
		for c in ' -_[](){}': n = n.replace(c, '.')
		n = '.' + n.replace('+', 'PLUS') + '.'
		return any(x in n for x in self._hdr_words)
	##########################################################################
	# (get_provider_and_path / get_quality_and_path moved earlier by upstream
	#  6.08.01 -- the clean-merged copies above are the only definitions.)

	def onAction(self, action):
		chosen_listitem = self.get_listitem(self.window_id)
		if action in self.closing_actions:
			if self.filter_applied: return self.clear_filter()
			self.selected = (None, '')
			return self.close()
		if action in self.selection_actions:
			if chosen_listitem.getProperty('tikiskins.perform_full_search') == 'true':
				self.selected = ('perform_full_search', '')
				return self.close()
			if 'UNCACHED' not in chosen_listitem.getProperty('tikiskins.source_type'):
#				self.selected = ('play', json.loads(chosen_listitem.getProperty('source')))
				self.selected = ('play', self._results[chosen_listitem.getProperty('source')])
				########### KODIRDIL - Mark source as tried (red badge next visit) ###########
				try: self._add_tried_source(self.selected[1])
				except: pass
				publish_player_release(self.selected[1])
				##############################################################################
				return self.close()
#			source = json.loads(chosen_listitem.getProperty('source'))
			source = self._results[chosen_listitem.getProperty('source')]
			# Upstream 6.08.01: uncached click now only ADDS the magnet to the
			# cloud (no immediate resolve+play), so there is no playback to
			# publish a release name for -- publish_player_release not needed.
			if not str(source.get('url')).startswith('magnet'): return
			Source(source, self.meta).manual_add_magnet_to_cloud()
		elif action == self.info_actions:
			self.open_info_window(chosen_listitem)
		elif action in self.context_actions:
			highlight = chosen_listitem.getProperty('tikiskins.highlight')
#			source = json.loads(chosen_listitem.getProperty('source'))
			source = self._results[chosen_listitem.getProperty('source')]
			kwargs = {'item': source, 'meta': self.meta, 'highlight': highlight, 'filter_applied': self.filter_applied}
			choice = self.open_window(('windows.sources', 'ResultsContextMenu'), 'contextmenu.xml', **kwargs)
			if choice is None: return
			if 'clear_results_filter' in choice: return self.clear_filter()
			elif 'results_filter' in choice: return self.filter_results()
			elif 'results_info' in choice:
				self.open_info_window(chosen_listitem)
			elif 'seekable_easynews' in choice:
				link = Source(source, self.meta).resolve_internal_sources(True)
				if link is None: return
				self.selected = ('play', {**source, 'unrestricted_link': link})
				publish_player_release(source)
				return self.close()
			elif 'browse_packs' in choice:
				link = Source(source, self.meta).browse_packs(highlight)
				if link == 'cancel': return
				source['unrestricted_link'] = link
				self.selected = ('play', source)
				publish_player_release(source)
				return self.close()
			elif 'manual_add_magnet_to_cloud' in choice: Source(source, self.meta).manual_add_magnet_to_cloud()
			elif 'manual_airlock_to_cloud' in choice: Source(source, self.meta).manual_airlock_to_cloud()
			elif 'unchecked_magnet_status' in choice: Source(source, self.meta).unchecked_magnet_status()
			elif 'aio_add_to_cloud' in choice: Source(source, self.meta).aio_add_to_cloud()
			else: self.execute_code(choice)

	def get_provider_and_path(self, provider):
		if provider in info_icons_dict: provider_path = info_icons_dict[provider]
		else: provider, provider_path = 'folders', info_icons_dict['folders']
		return provider, provider_path

	def get_quality_and_path(self, quality):
		quality_path = info_icons_dict[quality]
		return quality, quality_path

	def make_items(self):
		########### KODIRDIL - Hebrew Subtitles Matching Setup ###########
		enable_hebrew_subtitles = is_hebrew_subtitles_enabled()
		total_subtitles_found_list = []
		hebrew_embedded_taglines = None
		if enable_hebrew_subtitles:
			try:
				total_subtitles_found_list = db_utils.get_total_subtitles_found_list_from_hebrew_subtitles_db() or []
				if is_embedded_search_enabled():
					media_type_from_cache = db_utils.get_media_type_from_media_metadata_db()
					if media_type_from_cache:
						hebrew_embedded_taglines = hebrew_embedded.get_hebrew_embedded_taglines(media_type_from_cache)
			except:
				pass
		self.total_external_subtitles_found_count = len(total_subtitles_found_list)
		self.total_subtitles_matches_count = 0
		self.total_hebrew_embedded_subtitles_matches_count = 0
		self.total_quality_counts = {"4K": 0, "1080p": 0, "720p": 0, "SD": 0}
		##################################################################
		def builder():
			for count, item in enumerate(self.results, 1):
				self._results[str(count)] = item
				try:
					get = item.get
					listitem = self.make_listitem()
					set_property = listitem.setProperty
					scrape_provider = item['scrape_provider']
					source = get('source')
					quality = get('quality', 'SD')
					basic_quality, quality_icon = self.get_quality_and_path(lower(quality))
					name = get('display_name') or 'N/A'
					name = upper(name)
					pack = get('package', 'false') in pack_check
#					if pack: extra_info = '[B]PACK[/B] | %s' % get('extraInfo', '')
#					else: extra_info = get('extraInfo', 'N/A')
#					if not extra_info: extra_info = 'N/A'
					extra_info = get('extraInfo', '') or 'N/A'
					extra_info = extra_info.rstrip('| ')
					if scrape_provider == 'external':
						source_site = get('provider')
						source_site = upper(source_site)
						provider = upper(get('debrid', source_site))
						provider_lower = lower(provider)
						provider_icon = self.get_provider_and_path(provider_lower)[1]
						if 'cache_provider' in item and 'Uncached' in item['cache_provider']:
							key = 'uncached'
							try: seeders = 'uncached (%d seeders)' % get('seeders')
							except: seeders = 'uncached'
							set_property('tikiskins.source_type', upper(seeders))
							set_property('tikiskins.highlight', self.info_highlights_dict[key])
						elif 'cache_provider' in item:
							if highlight_type == 0: key = 'torrent_highlight'
							elif highlight_type == 1: key = provider_lower
							else: key = basic_quality
							status = cache_str[1] % upper(get('package')) if pack else cache_str[0]
							if 'Unchecked' in item['cache_provider']:
								status = status.replace('CACHED', 'UNCHECKED')
							set_property('tikiskins.source_type', status)
							set_property('tikiskins.highlight', self.info_highlights_dict[key])
						else:
							if highlight_type == 0: key = 'hoster_highlight'
							elif highlight_type == 1: key = provider_lower
							else: key = basic_quality
							set_property('tikiskins.source_type', source)
							set_property('tikiskins.highlight', self.info_highlights_dict[key])
					elif scrape_provider == 'aiostreams':
						if 'usenet' in source: source_site = get('tracker')
						else: source_site = get('provider') or source
						source_site = upper(source_site)
						provider = upper(get('debrid', source_site))
						provider_lower = lower(provider)
						provider_icon = self.get_provider_and_path(provider_lower)[1]
						status = upper(source)
						if get('cached'): status = cache_str[1] % upper(get('package')) if pack else cache_str[0]
						if get('library'): status = '[B]LIBRARY[/B]'
						if highlight_type == 0:
							if 'debrid' in get('source'): key = 'torrent_highlight'
							else: key = 'hoster_highlight'
						elif highlight_type == 1:
							if provider_lower in self.info_highlights_dict: key = provider_lower
							else: key = 'hoster_highlight'
						else: key = basic_quality
						set_property('tikiskins.source_type', status)
						set_property('tikiskins.highlight', self.info_highlights_dict[key])
					else:
						source_site = upper(source)
						provider, provider_icon = self.get_provider_and_path(lower(source))
						provider = upper(provider)
						if highlight_type in (0, 1): key = lower(provider)
						else: key = basic_quality
						set_property('tikiskins.source_type', 'DIRECT')
						set_property('tikiskins.highlight', self.info_highlights_dict[key])
					set_property('tikiskins.name', name)
					set_property('tikiskins.provider', provider)
					set_property('tikiskins.source_site', source_site)
					set_property('tikiskins.provider_icon', provider_icon)
					set_property('tikiskins.quality_icon', quality_icon)
					########### KODIRDIL - Match Hebrew subtitles per source ###########
					subtitle_matches_text = ''
					if enable_hebrew_subtitles and (total_subtitles_found_list or hebrew_embedded_taglines):
						try:
							original_video_tagline = get('display_name') or name or ''
							external_matched, embedded_matched, subtitle_matches_text, quality_counts = hebrew_subtitles_search_utils.calculate_highest_sync_percent_and_set_match_text(total_subtitles_found_list, original_video_tagline, quality, hebrew_embedded_taglines)
							self.total_subtitles_matches_count += external_matched + embedded_matched
							self.total_hebrew_embedded_subtitles_matches_count += embedded_matched
							for quality_name, quality_count in quality_counts.items():
								if quality_count > 0 and quality_name in self.total_quality_counts:
									self.total_quality_counts[quality_name] += quality_count
						except:
							subtitle_matches_text = ''
					size_label_val = get('size_label', 'N/A')
					has_hebrew_subs = 'false'
					if enable_hebrew_subtitles and subtitle_matches_text:
						size_label_val = size_label_val + subtitle_matches_text
						has_hebrew_subs = 'true'
					display_extra_info = ('[B][COLOR red]הופעל[/COLOR][/B] | ' if self._is_tried_source(item) else '') + extra_info
					####################################################################
					set_property('tikiskins.size_label', size_label_val)
					set_property('tikiskins.extra_info', display_extra_info)
					set_property('tikiskins.has_hebrew_subs', has_hebrew_subs)
					set_property('tikiskins.quality', upper(quality))
					set_property('tikiskins.count', '%02d.' % count)
					set_property('tikiskins.hash', get('hash', 'N/A'))
#					set_property('source', json.dumps(item))
					set_property('source', str(count))
					yield listitem
				except: pass
		try:
			highlight_type = self.info_highlights_dict['highlight_type']
			self.item_list = list(builder())
			self.total_results = string(len(self.item_list))
			if not self.prescrape: return
			count = len(self.item_list)
			self._results[str(count + 1)] = {}
			prescrape_listitem = self.make_listitem()
			prescrape_listitem.setProperty('source', str(count + 1))
			prescrape_listitem.setProperty('tikiskins.perform_full_search', 'true')
			prescrape_listitem.setProperty('tikiskins.start_full_scrape', '[B]***%s***[/B]' % upper(start_full_scrape))
			self.item_list.append(prescrape_listitem)
		except: pass

	def set_properties(self):
		poster_main, poster_backup, fanart_main, fanart_backup = get_art_provider()
		self.poster = self.meta.get(poster_main) or self.meta.get(poster_backup) or poster_empty
		self.fanart = self.meta.get(fanart_main) or self.meta.get(fanart_backup) or fanart_empty
		self.setProperty('tikiskins.window_style', self.window_style)
		self.setProperty('tikiskins.poster', self.poster)
		self.setProperty('tikiskins.fanart', self.fanart)
		self.setProperty('tikiskins.clearlogo', self.meta.get('clearlogo') or '')
		self.setProperty('tikiskins.title', self.meta['title'])
		self.setProperty('tikiskins.plot', self.meta['plot'])
		########### KODIRDIL - Hebrew subtitles panel text ###########
		hebrew_subtitles_panel_text = ''
		if is_hebrew_subtitles_enabled():
			try:
				total_subtitles_found_text, subtitles_matched_count_text = hebrew_subtitles_search_utils.generate_subtitles_match_top_panel_text_for_sync_percent_match(getattr(self, 'total_external_subtitles_found_count', 0), getattr(self, 'total_hebrew_embedded_subtitles_matches_count', 0), getattr(self, 'total_subtitles_matches_count', 0), getattr(self, 'total_quality_counts', {"4K": 0, "1080p": 0, "720p": 0, "SD": 0}))
				if subtitles_matched_count_text:
					hebrew_subtitles_panel_text = ' | %s | %s' % (total_subtitles_found_text, subtitles_matched_count_text)
				else:
					hebrew_subtitles_panel_text = ' | %s' % total_subtitles_found_text
			except Exception:
				hebrew_subtitles_panel_text = ''
		self.setProperty('tikiskins.total_results', self.total_results + hebrew_subtitles_panel_text)
		##############################################################
		self.setProperty('tikiskins.filters_ignored', self.filters_ignored)
		self.setProperty('tikiskins.scrape_time', '%.2f' % self.meta['scrape_time'])

	def open_info_window(self, chosen_listitem):
		kwargs = {'item': chosen_listitem, 'fanart': self.fanart}
		self.open_window(('windows.sources', 'ResultsInfo'), 'sources_info.xml', **kwargs)

	def filter_results(self):
		choices = [(filter_quality, 'quality'), (filter_provider, 'provider'), (filter_title, 'keyword_title'), (filter_extraInfo, 'extra_info')]
		########### KODIRDIL - Hebrew subtitles + SDR filter options ###########
		# ALL our filters go FIRST (they are the ones actually used day to day),
		# in a fixed order, each marked with a bullet so they read as one group
		# above POV's generic filters.
		#
		# NO [COLOR] markup here on purpose: select_dialog builds each row with
		# `line1.upper()`, which uppercases the tag itself ([COLOR cyan] ->
		# [COLOR CYAN]). Kodi then fails to resolve the colour, and a label that
		# is ENTIRELY wrapped in the tag renders as an EMPTY row -- which is
		# exactly why 'מיין לפי כתוביות עבריות' showed up blank (Asaf,
		# 2026-08-01). Plain text always renders.
		_ours = []
		if getattr(self, 'total_subtitles_matches_count', 0) > 0:
			_ours.append(('• הצג עם כתוביות עבריות בלבד | %d תוצאות' % self.total_subtitles_matches_count, 'hebrew_subs_only'))
			_ours.append(('• מיין לפי כתוביות עבריות (עברית קודם)', 'sort_hebrew_subs'))
		_sdr_count = len([i for i in self.item_list if not self._is_hdr_item(i)])
		if 0 < _sdr_count < len(self.item_list):
			_ours.append(('• הצג SDR בלבד (ללא HDR/DV) | %d תוצאות' % _sdr_count, 'sdr_only'))
		choices = _ours + choices
		########################################################################
		list_items = [{'line1': item[0]} for item in choices]
		heading = filter_str.replace('[B]', '').replace('[/B]', '')
		kwargs = {'items': json.dumps(list_items), 'heading': heading, 'multi_line': 'false'}
		main_choice = select_dialog([i[1] for i in choices], **kwargs)
		if main_choice is None: return
		########### KODIRDIL - Hebrew/SDR filters are SINGLE-ACTION choices ###########
		# They must be handled BEFORE the quality/provider fall-through: that
		# branch builds its option list from a 'tikiskins.<choice>' property,
		# which doesn't exist for these -- so it opened an EMPTY second dialog
		# (0/0) whose אישור returned None and bailed before the real filter code
		# below ever ran (Asaf, 2026-08-01, Windows POV sweep).
		if main_choice == 'hebrew_subs_only':
			filtered_list = [i for i in self.item_list if i.getProperty('tikiskins.has_hebrew_subs') == 'true']
		elif main_choice == 'sort_hebrew_subs':
			_with = [i for i in self.item_list if i.getProperty('tikiskins.has_hebrew_subs') == 'true']
			_without = [i for i in self.item_list if i.getProperty('tikiskins.has_hebrew_subs') != 'true']
			filtered_list = _with + _without
		elif main_choice == 'sdr_only':
			filtered_list = [i for i in self.item_list if not self._is_hdr_item(i)]
		###############################################################################
		elif main_choice == 'extra_info':
			list_items = [{'line1': item[0]} for item in extra_info_choices]
			kwargs = {'items': json.dumps(list_items), 'heading': heading, 'multi_choice': 'true', 'multi_line': 'false'}
			choice = select_dialog(extra_info_choices, **kwargs)
			if choice is None: return
			choice = [i[1] for i in choice]
			filtered_list = [i for i in self.item_list if all(x in i.getProperty('tikiskins.extra_info') for x in choice)]
		elif main_choice == 'keyword_title':
			keywords = dialog.input('Enter Keyword (Comma Separated for Multiple)')
			if not keywords: return
			keywords.replace(' ', '')
			keywords = keywords.split(',')
			choice = [upper(i) for i in keywords]
			filtered_list = [i for i in self.item_list if all(x in i.getProperty('tikiskins.name') for x in choice)]
		else:
			if main_choice == 'provider':
				sort_ranks = provider_sort_ranks()
				choice_sorter = sorted(sort_ranks.keys(), key=sort_ranks.get)
				choice_sorter = [upper(i) for i in choice_sorter]
			else: choice_sorter = quality_choices
			filter_property = 'tikiskins.%s' % main_choice
			provider_choices = list({i.getProperty(filter_property) for i in self.item_list if i.getProperty(filter_property)})
			provider_choices.sort(key=lambda x: choice_sorter.index(x) if x in choice_sorter else 999)
			list_items = [{'line1': item} for item in provider_choices]
			kwargs = {'items': json.dumps(list_items), 'heading': heading, 'multi_choice': 'true', 'multi_line': 'false'}
			choice = select_dialog(provider_choices, **kwargs)
			if choice is None: return
			filtered_list = [i for i in self.item_list if any(x in i.getProperty(filter_property) for x in choice)]
		if not filtered_list: return ok_dialog(text=32760)
		self.filter_applied = True
		self.win.reset()
		self.win.addItems(filtered_list)
		self.setFocusId(self.window_id)
		self.setProperty('tikiskins.total_results', string(len(filtered_list)))

	def clear_filter(self):
		self.win.reset()
		self.setProperty('tikiskins.total_results', self.total_results)
		self.onInit()

class ResultsInfo(BaseDialog):
	def __init__(self, *args, **kwargs):
		BaseDialog.__init__(self, args)
		self.item = kwargs['item']
		self.fanart = kwargs.get('fanart') or ''
		self.set_properties()

	def run(self):
		self.doModal()

	def onAction(self, action):
		self.close()

	def get_provider_and_path(self):
		provider = lower(self.item.getProperty('tikiskins.provider'))
		if provider in info_icons_dict: provider_path = info_icons_dict[provider]
		else: provider_path = info_icons_dict['folders']
		return provider, provider_path

	def get_quality_and_path(self):
		quality = lower(self.item.getProperty('tikiskins.quality'))
		quality_path = info_icons_dict[quality]
		return quality, quality_path

	def set_properties(self):
		provider, provider_path = self.get_provider_and_path()
		quality, quality_path = self.get_quality_and_path()
		self.setProperty('tikiskins.results.info.fanart', self.fanart)
		self.setProperty('tikiskins.results.info.name', self.item.getProperty('tikiskins.name'))
		self.setProperty('tikiskins.results.info.source_type', self.item.getProperty('tikiskins.source_type'))
		self.setProperty('tikiskins.results.info.source_site', self.item.getProperty('tikiskins.source_site'))
		self.setProperty('tikiskins.results.info.size_label', self.item.getProperty('tikiskins.size_label'))
		self.setProperty('tikiskins.results.info.extra_info', self.item.getProperty('tikiskins.extra_info'))
		self.setProperty('tikiskins.results.info.highlight', self.item.getProperty('tikiskins.highlight'))
		self.setProperty('tikiskins.results.info.hash', self.item.getProperty('tikiskins.hash'))
		self.setProperty('tikiskins.results.info.provider', provider)
		self.setProperty('tikiskins.results.info.quality', quality)
		self.setProperty('tikiskins.results.info.provider_icon', provider_path)
		self.setProperty('tikiskins.results.info.quality_icon', quality_path)

class ResultsContextMenu(BaseDialog):
	def __init__(self, *args, **kwargs):
		BaseDialog.__init__(self, args)
		self.window_id = 2020
		self.item = kwargs['item']
		self.highlight = kwargs['highlight']
		self.meta = kwargs['meta']
		self.filter_applied = kwargs['filter_applied']
		self.item_list = []
		self.selected = None
		self.make_menu()
		self.set_properties()

	def onInit(self):
		win = self.getControl(self.window_id)
		win.addItems(self.item_list)
		self.setFocusId(self.window_id)

	def run(self):
		self.doModal()
		return self.selected

	def onAction(self, action):
		if action in self.closing_actions: return self.close()
		if action in self.selection_actions:
			chosen_listitem = self.get_listitem(self.window_id)
			self.selected = chosen_listitem.getProperty('tikiskins.context.action')
			return self.close()
		if action in self.context_actions: return self.close()

	def make_menu(self):
		append = self.item_list.append
		source_json, meta_json = json.dumps(self.item), json.dumps(self.meta)
		name, provider_source = self.item.get('name'), self.item.get('source')
		magnet_url, info_hash = self.item.get('url', 'None'), self.item.get('hash', 'None')
		scrape_provider, cache_provider = self.item.get('scrape_provider'), self.item.get('cache_provider', 'None')
		if next((True for x in ('realdebrid', 'alldebrid') if x in cache_provider), False):
			append(self.make_contextmenu_item(check_str, run_plugin_str, {'mode': 'unchecked_magnet_status'}))
		if 'easynews' in scrape_provider:
			append(self.make_contextmenu_item(en_seek_str, run_plugin_str, {'mode': 'seekable_easynews'}))
		if self.filter_applied:
			append(self.make_contextmenu_item(clr_filter_str, run_plugin_str, {'mode': 'clear_results_filter'}))
		else: append(self.make_contextmenu_item(filter_str, run_plugin_str, {'mode': 'results_filter'}))
		append(self.make_contextmenu_item(extra_info_str, run_plugin_str, {'mode': 'results_info'}))
		if 'Uncached' in cache_provider: return
		if scrape_provider == 'aiostreams':
			down_params = {'mode': 'downloader', 'action': 'None', 'url': self.item.get('url_dl')}
			cloud_params = {'mode': 'aio_add_to_cloud'} if self.item.get('debrid') else {}
			append(self.make_contextmenu_item(down_file_str, run_plugin_str, down_params))
			if cloud_params: append(self.make_contextmenu_item(cloud_str, run_plugin_str, cloud_params))
			return
		down_params = {
			'mode': 'downloader', 'highlight': self.highlight, 'url': None,
			'source': source_json, 'meta': meta_json, 'name': self.meta.get('rootname', ''),
			'provider': cache_provider, 'magnet_url': magnet_url, 'info_hash': info_hash
		}
		if 'package' in self.item:
			append(self.make_contextmenu_item(browse_pack_str, run_plugin_str, {'mode': 'browse_packs'}))
			append(self.make_contextmenu_item(down_pack_str, run_plugin_str, {'action': 'meta.pack', **down_params}))
		if scrape_provider != 'folders':
			append(self.make_contextmenu_item(down_file_str, run_plugin_str, {'action': 'meta.file', **down_params}))
		if provider_source == 'torrent':
			append(self.make_contextmenu_item(cloud_str, run_plugin_str, {'mode': 'manual_add_magnet_to_cloud'}))
		if cache_provider in ('torbox',):
			append(self.make_contextmenu_item(airlock_str, run_plugin_str, {'mode': 'manual_airlock_to_cloud'}))

	def set_properties(self):
		self.setProperty('tikiskins.context.highlight', self.highlight)

