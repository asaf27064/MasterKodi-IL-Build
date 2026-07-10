# -*- coding: utf-8 -*-
import json
from windows.base_window import BaseDialog
from caches.settings_cache import set_setting, get_setting
from modules.debrid import debrid_for_ext_cache_check
from modules.source_utils import source_filters
from modules.settings import provider_sort_ranks, avoid_episode_spoilers
from modules.kodi_utils import get_icon, kodi_dialog, hide_busy_dialog, addon_fanart, select_dialog, ok_dialog, notification
# from modules.kodi_utils import logger
########### KODIRDIL - Hebrew Subtitles Integration ###########
from kodirdil import hebrew_subtitles_search_utils
from kodirdil import db_utils
from kodirdil.websites import hebrew_embedded

# Hebrew subtitles settings
def is_hebrew_subtitles_enabled():
    return get_setting('gears.hebrew_subtitles.enable_matching', 'true') == 'true'

def get_minimum_sync_percent():
    return int(get_setting('gears.hebrew_subtitles.minimum_sync_percent', '70') or '70')

def is_embedded_search_enabled():
    return get_setting('gears.hebrew_subtitles.match_embedded', 'true') == 'true'
###############################################################


class SourcesResults(BaseDialog):
	def __init__(self, *args, **kwargs):
		BaseDialog.__init__(self, *args)
		self.window_format = kwargs.get('window_format', 'list')
		self.window_id = kwargs.get('window_id', 2000)
		self.filter_window_id = 2100
		self.results = kwargs.get('results')
		self.uncached_results = kwargs.get('uncached_results', [])
		self.info_highlights_dict = kwargs.get('scraper_settings')
		self.episode_group_label = kwargs.get('episode_group_label', '')
		self.prescrape = kwargs.get('prescrape')
		self.meta = kwargs.get('meta')
		self.filters_ignored = kwargs.get('filters_ignored', False)
		self.meta_get = self.meta.get
		self.make_poster = self.window_format in ('list', 'medialist')
		self.empty_poster = get_icon('box_office')
		self.addon_fanart = addon_fanart()
		self.poster = self.meta_get('poster') or self.empty_poster
		self.external_cache_check = kwargs.get('external_cache_check')
		self.prerelease_values, self.prerelease_key = ('CAM', 'SCR', 'TELE'), 'CAM/SCR/TELE'
		self.info_icons_dict = {'easynews': get_icon('easynews'), 'alldebrid': get_icon('alldebrid'), 'real-debrid': get_icon('realdebrid'), 'premiumize': get_icon('premiumize'),
		'offcloud': get_icon('offcloud'), 'easydebrid': get_icon('easydebrid'), 'torbox': get_icon('torbox'), 'ad_cloud': get_icon('alldebrid'), 'rd_cloud': get_icon('realdebrid'),
		'pm_cloud': get_icon('premiumize'), 'oc_cloud': get_icon('offcloud'), 'tb_cloud': get_icon('torbox')}
		self.info_quality_dict = {'4k': get_icon('flag_4k', 'flags'), '1080p': get_icon('flag_1080p', 'flags'), '720p': get_icon('flag_720p', 'flags'),
		'sd': get_icon('flag_sd', 'flags'), 'cam': get_icon('flag_sd', 'flags'), 'tele': get_icon('flag_sd', 'flags'), 'scr': get_icon('flag_sd', 'flags')}
		########### KODIRDIL - Tried sources tracking key (per media item) ###########
		# Builds a per-media key so each movie/episode has its own "already tried" set.
		# This lets _is_tried_source flag items in the list with a red "הופעל" badge so
		# the user knows which sources they already attempted on this title.
		self.tried_sources_key = 'tried_sources_%s_%s_%s' % (self.meta_get('tmdb_id', ''), self.meta_get('season', ''), self.meta_get('episode', ''))
		##############################################################################
		self.make_items()
		self.make_filter_items()
		self.set_properties()

	def onInit(self):
		self.filter_applied = False
		if self.make_poster: self.set_poster()
		self.add_items(self.window_id, self.item_list)
		self.add_items(self.filter_window_id, self.filter_list)
		self.setFocusId(self.window_id)

	def run(self):
		self.doModal()
		self.clearProperties()
		hide_busy_dialog()
		return self.selected

	########### KODIRDIL - Tried sources tracking + HDR detection ###########
	# Persistent set of sources the user has already attempted for this media item,
	# stored as a comma-joined identifier list in a Home window property. The identifier
	# prefers `hash` (torrent infohash) and falls back to `name`. Capped at 50 to bound
	# memory; the oldest entries roll off.
	def _get_tried_sources(self):
		try:
			data = self.get_home_property(self.tried_sources_key)
			return set(data.split(',')) if data else set()
		except: return set()

	def _add_tried_source(self, source):
		try:
			identifier = source.get('hash') or source.get('name', '')
			if identifier:
				tried = self._get_tried_sources()
				tried.add(identifier)
				if len(tried) > 50:
					tried = set(list(tried)[-50:])
				self.set_home_property(self.tried_sources_key, ','.join(tried))
		except: pass

	def _is_tried_source(self, source):
		try:
			identifier = source.get('hash') or source.get('name', '')
			return identifier in self._get_tried_sources()
		except: return False

	# HDR / Dolby Vision detection. Used for the "SDR only" filter button so the user
	# can hide HDR/DV variants when their display can't tone-map them properly.
	_hdr_tags = ('[B]HDR[/B]', '[B]D/VISION[/B]')
	_hdr_words = ('.HDR.', '.HDR10.', '.HDR10PLUS.', '.HDR10P.', '.DV.', '.DOVI.', '.DVHE.', '.DOLBY.VISION.', '.DOLBYVISION.', '.HLG.')

	def _is_hdr_item(self, item):
		if any(x in item.getProperty('extraInfo') for x in self._hdr_tags): return True
		n = item.getProperty('name')
		for c in ' -_[](){}': n = n.replace(c, '.')
		n = '.' + n.replace('+', 'PLUS') + '.'
		return any(x in n for x in self._hdr_words)
	##########################################################################

	def get_provider_and_path(self, provider):
		try: return provider, self.info_icons_dict[provider]
		except: return 'folders', get_icon('folder')

	def get_quality_and_path(self, quality):
		try: return quality, self.info_quality_dict[quality]
		except: return 'sd', get_icon('flag_sd')

	def filter_action(self, action):
		if action == self.right_action or action in self.closing_actions:
			self.select_item(self.filter_window_id, 0)
			self.setFocusId(self.window_id)
		if action in self.selection_actions:
			chosen_listitem = self.get_listitem(self.filter_window_id)
			filter_type, filter_value = chosen_listitem.getProperty('filter_type'), chosen_listitem.getProperty('filter_value')
			if filter_type in ('quality', 'provider'):
				if filter_value == self.prerelease_key: filtered_list = [i for i in self.item_list if i.getProperty(filter_type) in self.prerelease_values]
				else: filtered_list = [i for i in self.item_list if i.getProperty(filter_type) == filter_value]
			elif filter_type == 'special':
				if filter_value == 'title':
					keywords = kodi_dialog().input('Enter Keyword (Comma Separated for Multiple)')
					if not keywords: return
					keywords.replace(' ', '')
					keywords = keywords.split(',')
					choice = [i.upper() for i in keywords]
					filtered_list = [i for i in self.item_list if all(x in i.getProperty('name') for x in choice)]
				elif filter_value == 'extraInfo':
					filters = source_filters()
					list_items = [{'line1': item[0], 'icon': self.poster} for item in filters]
					kwargs = {'items': json.dumps(list_items), 'heading': 'Filter Results', 'multi_choice': 'true'}
					choice = select_dialog(filters, **kwargs)
					if choice == None: return
					choice = [i[1] for i in choice]
					filtered_list = [i for i in self.item_list if all(x in i.getProperty('extraInfo') for x in choice)]
				elif filter_value == 'showuncached': filtered_list = self.make_items(self.uncached_results)
				########### KODIRDIL - Hebrew Subtitles Filters ###########
				elif filter_value == 'hebrew_subs_only':
					filtered_list = [i for i in self.item_list if i.getProperty('has_hebrew_subs') == 'true']
				elif filter_value == 'sort_hebrew_subs':
					with_subs = [i for i in self.item_list if i.getProperty('has_hebrew_subs') == 'true']
					without_subs = [i for i in self.item_list if i.getProperty('has_hebrew_subs') != 'true']
					filtered_list = with_subs + without_subs
				##########################################################
				########### KODIRDIL - SDR Only Filter (hide HDR/DV) ###########
				elif filter_value == 'sdr_only':
					filtered_list = [i for i in self.item_list if not self._is_hdr_item(i)]
				################################################################
				else: #cache_check_rescrape
					self.selected = ('cache_change_rescrape', 'false' if self.external_cache_check else 'true')
					return self.close()
			if not filtered_list: return ok_dialog(text='No Results')
			self.set_filter(filtered_list)

	def onAction(self, action):
		if self.get_visibility('Control.HasFocus(%s)' % self.filter_window_id): return self.filter_action(action)
		chosen_listitem = self.get_listitem(self.window_id)
		if action in self.closing_actions:
			if self.filter_applied: return self.clear_filter()
			self.selected = (None, '')
			return self.close()
		if action == self.info_action:
			self.open_window(('windows.sources', 'SourcesInfo'), 'sources_info.xml', item=chosen_listitem)
		elif action in self.selection_actions:
			if self.prescrape and chosen_listitem.getProperty('perform_full_search') == 'true':
				self.selected = ('perform_full_search', '')
				return self.close()
			chosen_source = json.loads(chosen_listitem.getProperty('source'))
			if 'Uncached' in chosen_source.get('cache_provider', ''):
				from modules.debrid import manual_add_magnet_to_cloud
				return manual_add_magnet_to_cloud({'mode': 'manual_add_magnet_to_cloud', 'provider': chosen_source['debrid'], 'magnet_url': chosen_source['url']})
			########### KODIRDIL - Mark source as tried so it shows the "הופעל" badge next time ###########
			self._add_tried_source(chosen_source)
			################################################################################################
			self.selected = ('play', chosen_source)
			return self.close()
		elif action in self.context_actions:
			source = json.loads(chosen_listitem.getProperty('source'))
			choice = self.context_menu(source)
			if choice:
				if isinstance(choice, dict): return self.execute_code('RunPlugin(%s)' % self.build_url(choice))
				if choice == 'results_info': return self.open_window(('windows.sources', 'SourcesInfo'), 'sources_info.xml', item=chosen_listitem)
				if choice == 'rd_cloud_delete':
					from apis.real_debrid_api import RealDebridAPI
					rd_api = RealDebridAPI()
					function = rd_api.delete_torrent if source['cache_type'] == 'torrent' else rd_api.delete_download
					result = function(source['folder_id'])
					if result.status_code in (401, 403, 404): return notification('Error', 1200)
					rd_api.clear_cache()
					self.delete_single_source(source)

	def delete_single_source(self, single_source):
		self.results.remove(single_source)
		self.make_items()
		self.total_results = str(len(self.item_list))
		self.reset_window(self.window_id)
		self.add_items(self.window_id, self.item_list)
		self.setFocusId(self.window_id)
		self.set_properties()

	def make_items(self, filtered_list=None):
		########### KODIRDIL - Hebrew Subtitles Matching Setup ###########
		enable_hebrew_subtitles = is_hebrew_subtitles_enabled()

		total_subtitles_found_list = []
		hebrew_embedded_taglines = None
		minimum_sync_percent = 70

		if enable_hebrew_subtitles:
			try:
				total_subtitles_found_list = db_utils.get_total_subtitles_found_list_from_hebrew_subtitles_db() or []

				if is_embedded_search_enabled():
					media_type_from_cache = db_utils.get_media_type_from_media_metadata_db()
					if media_type_from_cache:
						hebrew_embedded_taglines = hebrew_embedded.get_hebrew_embedded_taglines(media_type_from_cache)

				minimum_sync_percent = get_minimum_sync_percent()
			except:
				pass

		self.total_external_subtitles_found_count = len(total_subtitles_found_list)
		self.total_subtitles_matches_count = 0
		self.total_hebrew_embedded_subtitles_matches_count = 0
		self.total_quality_counts = {"4K": 0, "1080p": 0, "720p": 0, "SD": 0}
		##################################################################

		def builder(results):
			for count, item in enumerate(results, 1):
				try:
					get = item.get
					listitem = self.make_listitem()
					set_properties = listitem.setProperties
					scrape_provider, source, quality, name = get('scrape_provider'), get('source'), get('quality', 'SD'), get('display_name')
					basic_quality, quality_icon = self.get_quality_and_path(quality.lower())
					pack = get('package', 'false') in ('true', 'show', 'season')
					extraInfo = get('extraInfo', '')
					extraInfo = extraInfo.rstrip('| ')
					if pack: extraInfo = '[B]%s PACK[/B] | %s' % (get('package'), extraInfo)
					if self.episode_group_label: extraInfo = '%s | %s' % (self.episode_group_label, extraInfo)
					if not extraInfo: extraInfo = 'N/A'
					if scrape_provider == 'external':
						source_site = get('provider').upper()
						provider = get('debrid', source_site).replace('.me', '').upper()
						provider_lower = provider.lower()
						provider_icon = self.get_provider_and_path(provider_lower)[1]
						if 'Uncached' in item['cache_provider']:
							if 'seeders' in item: set_properties({'source_type': 'UNCACHED (%d SEEDERS)' % get('seeders', 0)})
							else: set_properties({'source_type': 'UNCACHED'})
							set_properties({'highlight': 'FF7C7C7C'})
						else:
							if provider in ('REAL-DEBRID', 'ALLDEBRID'):
								if self.external_cache_check: cache_flag = '[B]CACHED[/B]'
								else: cache_flag = 'UNCHECKED'
							else: cache_flag = '[B]CACHED[/B]'
							if highlight_type == 0: key = provider_lower
							else: key = basic_quality
							set_properties({'highlight': self.info_highlights_dict[key]})
							if pack: set_properties({'source_type': '%s [B]PACK[/B]' % cache_flag})
							else: set_properties({'source_type': '%s' % cache_flag})
						set_properties({'provider': provider})
					else:
						source_site = source.upper()
						provider, provider_icon = self.get_provider_and_path(source.lower())
						if highlight_type == 0: key = provider
						else: key = basic_quality
						set_properties({'highlight': self.info_highlights_dict[key], 'source_type': 'DIRECT', 'provider': provider.upper()})

					########### KODIRDIL - Match Hebrew subtitles per source ###########
					subtitle_matches_text = ''
					if enable_hebrew_subtitles and (total_subtitles_found_list or hebrew_embedded_taglines):
						try:
							original_video_tagline = get('name') or name or ''

							external_matched, embedded_matched, subtitle_matches_text, quality_counts = \
								hebrew_subtitles_search_utils.calculate_highest_sync_percent_and_set_match_text(
									total_subtitles_found_list,
									original_video_tagline,
									quality,
									hebrew_embedded_taglines
								)

							self.total_subtitles_matches_count += external_matched + embedded_matched
							self.total_hebrew_embedded_subtitles_matches_count += embedded_matched

							for quality_name, quality_count in quality_counts.items():
								if quality_count > 0 and quality_name in self.total_quality_counts:
									self.total_quality_counts[quality_name] += quality_count
						except:
							subtitle_matches_text = ''
					####################################################################

					########### KODIRDIL - Add subtitle text to size_label ###########
					size_label = get('size_label', 'N/A')
					has_hebrew_subs = 'false'
					if enable_hebrew_subtitles and subtitle_matches_text:
						size_label = size_label + subtitle_matches_text
						has_hebrew_subs = 'true'
					#################################################################

					########### KODIRDIL - Prefix tried sources with red "הופעל" badge in extraInfo ###########
					# `item` here is the source dict, not a listitem — _is_tried_source accepts dicts.
					display_extraInfo = ('[B][COLOR red]הופעל[/COLOR][/B] | ' if self._is_tried_source(item) else '') + extraInfo
					##########################################################################################

					set_properties({'name': name.upper(), 'source_site': source_site, 'provider_icon': provider_icon, 'quality_icon': quality_icon, 'count': '%02d.' % count,
							'size_label': size_label, 'extraInfo': display_extraInfo, 'quality': quality.upper(), 'hash': get('hash', 'N/A'), 'source': json.dumps(item),
							'has_hebrew_subs': has_hebrew_subs})
					yield listitem
				except: pass
		try:
			highlight_type = self.info_highlights_dict['highlight_type']
			if filtered_list: return list(builder(filtered_list))
			self.item_list = list(builder(self.results))
			if self.prescrape:
				prescrape_listitem = self.make_listitem()
				prescrape_listitem.setProperty('perform_full_search', 'true')
			self.total_results = str(len(self.item_list))
			if self.prescrape: self.item_list.append(prescrape_listitem)
		except: pass

	def make_filter_items(self):
		def builder(data):
			for item in data:
				listitem = self.make_listitem()
				listitem.setProperties({'label': item[0], 'filter_type': item[1], 'filter_value': item[2]})
				yield listitem
		duplicates = set()
		qualities = [i.getProperty('quality') for i in self.item_list \
							if not (i.getProperty('quality') in duplicates or duplicates.add(i.getProperty('quality'))) \
							and not i.getProperty('quality') == '']
		if any(i in self.prerelease_values for i in qualities): qualities = [i for i in qualities if not i in self.prerelease_values] + [self.prerelease_key]
		qualities.sort(key=('4K', '1080P', '720P', 'SD', 'CAM/SCR/TELE').index)
		quality_totals = {i: len([x for x in self.item_list if x.getProperty('quality') == i]) for i in qualities}
		if 'CAM/SCR/TELE' in qualities: quality_totals['CAM/SCR/TELE'] = len([i for i in self.item_list if i.getProperty('quality') in self.prerelease_values])
		duplicates = set()
		providers = [i.getProperty('provider') for i in self.item_list \
							if not (i.getProperty('provider') in duplicates or duplicates.add(i.getProperty('provider'))) \
							and not i.getProperty('provider') == '']
		provider_totals = {i: len([x for x in self.item_list if x.getProperty('provider') == i]) for i in providers}
		sort_ranks = provider_sort_ranks()
		cache_functions_debrid = debrid_for_ext_cache_check()
		sort_ranks['premiumize'] = sort_ranks.pop('premiumize.me')
		provider_choices = sorted(sort_ranks.keys(), key=sort_ranks.get)
		provider_choices = [i.upper() for i in provider_choices]
		providers.sort(key=provider_choices.index)
		qualities = [('Show [B]%s[/B] Only | [B]%d[/B] Results' % (i, quality_totals[i]), 'quality', i) for i in qualities]
		providers = [('Show [B]%s[/B] Only | [B]%d[/B] Results' % (i, provider_totals[i]), 'provider', i) for i in providers]
		data = []
		if cache_functions_debrid: data.append(('Rescrape with External Cache Check [B]%s[/B]' % ('OFF' if self.external_cache_check else 'ON'), 'special', 'cache_check_rescrape'))
		if self.uncached_results: data.append(('Show [B]Uncached[/B] Only | [B]%d[/B] Results' % len(self.uncached_results), 'special', 'showuncached'))
		########### KODIRDIL - Hebrew Subtitles Filter ###########
		if hasattr(self, 'total_subtitles_matches_count') and self.total_subtitles_matches_count > 0:
			data.append(('[COLOR lime]הצג עם כתוביות עבריות בלבד[/COLOR] | [B]%d[/B] תוצאות' % self.total_subtitles_matches_count, 'special', 'hebrew_subs_only'))
			data.append(('[COLOR cyan]מיין לפי כתוביות עבריות[/COLOR]', 'special', 'sort_hebrew_subs'))
		##########################################################
		########### KODIRDIL - SDR Only Filter (hide HDR/DV variants) ###########
		# Some users have displays that don't tone-map HDR/DV properly. This filter lets
		# them hide HDR/DV variants if at least one SDR variant exists.
		sdr_count = len([i for i in self.item_list if not self._is_hdr_item(i)])
		if 0 < sdr_count < len(self.item_list):
			data.append(('[COLOR yellow]הצג SDR בלבד (ללא HDR/DV)[/COLOR] | [B]%d[/B] תוצאות' % sdr_count, 'special', 'sdr_only'))
		#########################################################################
		data.extend(qualities)
		data.extend(providers)
		data.extend([('Filter by [B]Title[/B]...', 'special', 'title'), ('Filter by [B]Info[/B]...', 'special', 'extraInfo')])
		self.filter_list = list(builder(data))

	def set_properties(self):
		self.setProperty('window_format', self.window_format)
		self.setProperty('fanart', self.meta_get('fanart') or self.addon_fanart)
		self.setProperty('clearlogo', self.meta_get('clearlogo') or '')
		self.setProperty('title', self.meta_get('title'))

		########### KODIRDIL - Add Hebrew subtitles panel text ###########
		enable_hebrew_subtitles = is_hebrew_subtitles_enabled()
		hebrew_subtitles_panel_text = ''

		if enable_hebrew_subtitles:
			try:
				total_subtitles_found_text, subtitles_matched_count_text = \
					hebrew_subtitles_search_utils.generate_subtitles_match_top_panel_text_for_sync_percent_match(
						getattr(self, 'total_external_subtitles_found_count', 0),
						getattr(self, 'total_hebrew_embedded_subtitles_matches_count', 0),
						getattr(self, 'total_subtitles_matches_count', 0),
						getattr(self, 'total_quality_counts', {"4K": 0, "1080p": 0, "720p": 0, "SD": 0})
					)

				if subtitles_matched_count_text:
					hebrew_subtitles_panel_text = f" | {total_subtitles_found_text} | {subtitles_matched_count_text}\n"
				else:
					hebrew_subtitles_panel_text = f" | {total_subtitles_found_text}\n"
			except Exception as e:
				from modules.kodi_utils import logger
				logger("Gears-HEBSUBS", f"Error setting panel text: {str(e)}")
				hebrew_subtitles_panel_text = ''

		self.setProperty('total_results', self.total_results + hebrew_subtitles_panel_text)
		##################################################################

		self.setProperty('filters_ignored', '| Filters Ignored' if self.filters_ignored else '')

	def set_poster(self):
		if self.window_id == 2000: self.set_image(200, self.poster)

	def context_menu(self, item):
		down_file_params, down_pack_params, browse_pack_params, add_magnet_to_cloud_params, uncached_download = None, None, None, None, None
		item_get = item.get
		item_id, name, magnet_url, info_hash = item_get('id', None), item_get('name'), item_get('url', 'None'), item_get('hash', 'None')
		provider_source, scrape_provider, cache_provider = item_get('source'), item_get('scrape_provider'), item_get('cache_provider', 'None')
		uncached = 'Uncached' in cache_provider
		source, meta_json = json.dumps(item), json.dumps(self.meta)
		choices = []
		choices_append = choices.append
		if not uncached and scrape_provider != 'folders':
			down_file_params = {'mode': 'downloader.runner', 'action': 'meta.single', 'name': self.meta.get('rootname', ''), 'source': source,
								'url': None, 'provider': scrape_provider, 'meta': meta_json}
		if 'package' in item and not uncached and cache_provider != 'EasyDebrid':
			down_pack_params = {'mode': 'downloader.runner', 'action': 'meta.pack', 'name': self.meta.get('rootname', ''), 'source': source, 'url': None,
								'provider': cache_provider, 'meta': meta_json, 'magnet_url': magnet_url, 'info_hash': info_hash}
		if provider_source == 'torrent':
			browse_pack_params = {'mode': 'debrid.browse_packs', 'provider': cache_provider, 'name': name,
								'magnet_url': magnet_url, 'info_hash': info_hash}
			if cache_provider != 'EasyDebrid': add_magnet_to_cloud_params = {'mode': 'manual_add_magnet_to_cloud', 'provider': cache_provider, 'magnet_url': magnet_url}
		choices_append(('Info', 'results_info'))
		if add_magnet_to_cloud_params: choices_append(('Add to Cloud', add_magnet_to_cloud_params))
		if browse_pack_params: choices_append(('Browse', browse_pack_params))
		if down_pack_params: choices_append(('Download Pack', down_pack_params))
		if down_file_params: choices_append(('Download File', down_file_params))
		if provider_source == 'rd_cloud': choices_append(('Delete from RD Cloud', 'rd_cloud_delete'))
		list_items = [{'line1': i[0], 'icon': self.poster} for i in choices]
		kwargs = {'items': json.dumps(list_items)}
		choice = select_dialog([i[1] for i in choices], **kwargs)
		return choice

	def set_filter(self, filtered_list):
		self.filter_applied = True
		self.reset_window(self.window_id)
		self.add_items(self.window_id, filtered_list)
		self.setFocusId(self.window_id)
		self.setProperty('total_results', str(len(filtered_list)))
		self.setProperty('filter_applied', 'true')
		self.setProperty('filter_info', '| Press [B]BACK[/B] to Cancel')

	def clear_filter(self):
		self.filter_applied = False
		self.reset_window(self.window_id)
		self.add_items(self.window_id, self.item_list)
		self.setFocusId(self.window_id)
		self.select_item(self.filter_window_id, 0)
		self.setProperty('total_results', self.total_results)
		self.setProperty('filter_applied', 'false')
		self.setProperty('filter_info', '')

class SourcesPlayback(BaseDialog):
	def __init__(self, *args, **kwargs):
		BaseDialog.__init__(self, *args)
		self.meta = kwargs.get('meta')
		self.is_canceled, self.skip_resolve, self.resume_choice = False, False, None
		self.meta_get = self.meta.get
		self.addon_fanart = addon_fanart()
		self.enable_scraper()

	def run(self):
		self.doModal()
		self.clearProperties()
		self.clear_modals()

	def onClick(self, controlID):
		self.resume_choice = {10: 'resume', 11: 'start_over', 12: 'cancel'}[controlID]

	def onAction(self, action):
		if action in self.closing_actions: self.is_canceled = True
		elif action == self.right_action and self.window_mode == 'resolver': self.skip_resolve = True

	def iscanceled(self):
		return self.is_canceled

	def skip_resolved(self):
		status = self.skip_resolve
		self.skip_resolve = False
		return status

	def reset_is_cancelled(self):
		self.is_canceled = False

	def enable_scraper(self):
		self.window_mode = 'scraper'
		self.set_scraper_properties()

	def enable_resolver(self):
		self.window_mode = 'resolver'
		self.set_resolver_properties()

	def enable_resume(self, percent):
		self.window_mode = 'resume'
		self.set_resume_properties(percent)

	def busy_spinner(self, toggle='true'):
		self.setProperty('enable_busy_spinner', toggle)

	def set_scraper_properties(self):
		title, genre = self.meta_get('title'), self.meta_get('genre', '')
		fanart, clearlogo = self.meta_get('fanart') or self.addon_fanart, self.meta_get('clearlogo') or ''
		self.setProperty('window_mode', self.window_mode)
		self.setProperty('fanart', fanart)
		self.setProperty('clearlogo', clearlogo)
		self.setProperty('title', title)
		self.setProperty('genre', ', '.join(genre))

	def set_resolver_properties(self):
		if self.meta_get('media_type') == 'movie': self.text = self.meta_get('plot')
		else:
			if avoid_episode_spoilers(): plot = self.meta_get('tvshow_plot') or '* Hidden to Prevent Spoilers *'
			else: plot = self.meta_get('plot', '') or self.meta_get('tvshow_plot', '')
			self.text = '[B]%02dx%02d - %s[/B][CR][CR]%s' % (self.meta_get('season'), self.meta_get('episode'), self.meta_get('ep_name', 'N/A').upper(), plot)
		self.setProperty('window_mode', self.window_mode)
		self.setProperty('text', self.text)

	def set_resume_properties(self, percent):
		self.setProperty('window_mode', self.window_mode)
		self.setProperty('resume_percent', percent)
		self.setFocusId(10)
		self.update_resumer()

	def update_scraper(self, results_sd, results_720p, results_1080p, results_4k, results_total, content='', percent=0):
		self.setProperty('results_4k', str(results_4k))
		self.setProperty('results_1080p', str(results_1080p))
		self.setProperty('results_720p', str(results_720p))
		self.setProperty('results_sd', str(results_sd))
		self.setProperty('results_total', str(results_total))
		self.setProperty('percent', str(percent))
		self.set_text(2001, content)

	def update_resolver(self, text='', percent=0):
		try: self.setProperty('percent', str(percent))
		except: pass
		if text: self.set_text(2002, text)

	def update_resumer(self):
		count = 0
		while self.resume_choice is None:
			percent = int((float(count)/10000)*100)
			if percent >= 100: self.resume_choice = 'resume'
			self.setProperty('percent', str(percent))
			count += 100
			self.sleep(100)

class SourcesInfo(BaseDialog):
	def __init__(self, *args, **kwargs):
		BaseDialog.__init__(self, *args)
		self.item = kwargs['item']
		self.item_get_property = self.item.getProperty
		self.set_properties()

	def run(self):
		self.doModal()

	def onAction(self, action):
		self.close()

	def set_properties(self):
		self.setProperty('name', self.item_get_property('name'))
		self.setProperty('source_type', self.item_get_property('source_type'))
		self.setProperty('source_site', self.item_get_property('source_site'))
		self.setProperty('size_label', self.item_get_property('size_label'))
		self.setProperty('extraInfo', self.item_get_property('extraInfo'))
		self.setProperty('highlight', self.item_get_property('highlight'))
		self.setProperty('hash', self.item_get_property('hash'))
		self.setProperty('provider', self.item_get_property('provider').lower())
		self.setProperty('quality', self.item_get_property('quality').lower())
		self.setProperty('provider_icon', self.item_get_property('provider_icon'))
		self.setProperty('quality_icon', self.item_get_property('quality_icon'))

class SourcesChoice(BaseDialog):
	def __init__(self, *args, **kwargs):
		BaseDialog.__init__(self, *args)
		self.window_id = 5001
		self.item_list = []
		self.make_items()

	def onInit(self):
		self.add_items(self.window_id, self.item_list)
		self.setFocusId(self.window_id)

	def run(self):
		self.doModal()
		return self.choice

	def onAction(self, action):
		if action in self.closing_actions:
			self.choice = None
			self.close()
		if action in self.selection_actions:
			chosen_listitem = self.get_listitem(self.window_id)
			self.choice = chosen_listitem.getProperty('name')
			self.close()

	def make_items(self):
		append = self.item_list.append
		for item in [('List', get_icon('results_list', 'results')), ('Rows', get_icon('results_row', 'results')), ('WideList', get_icon('results_widelist', 'results'))]:
			listitem = self.make_listitem()
			listitem.setProperties({'name': item[0], 'image': item[1]})
			append(listitem)
