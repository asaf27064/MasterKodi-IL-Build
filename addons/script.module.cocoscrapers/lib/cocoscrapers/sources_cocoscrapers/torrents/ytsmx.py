# modified by kodifitzwell for Fenomscrapers
"""
	Fenomscrapers Project
"""

import re
from json import loads as jsloads
from cocoscrapers.modules import coco_client as client, coco_compat as source_utils


class source:
	priority = 3
	pack_capable = False
	hasMovies = True
	hasEpisodes = False
	def __init__(self):
		self.language = ['en']
		self.base_link = "https://movies-api.accel.li" # "https://yts.mx"
		self.search_link = '/api/v2/list_movies.json?query_term=%s' # accepts imdb_id as query_term
		self.min_seeders = 0

	def sources(self, data, hostDict):
		sources = []
		if not data: return sources
		sources_append = sources.append
		try:
			aliases = source_utils.aliases_to_array(data['aliases'])
			title = data['title'].replace('&', 'and').replace('/', ' ').replace('$', 's')
			hdlr = year = data['year']
			years = [str(int(year)-1), str(year), str(int(year)+1)]
			imdb = data['imdb']
			url = '%s%s' % (self.base_link, self.search_link % imdb)
			# log_utils.log('url = %s' % url)
			results = client.session.request('get', url, timeout=7).text
			if not results: return sources
			files = jsloads(results)
			if files.get('status') == 'error' or files.get('data').get('movie_count') == 0: return sources
			title_long = files.get('data').get('movies')[0].get('title_long').replace(' ', '.')
			torrents = files.get('data').get('movies')[0].get('torrents')
			undesirables = source_utils.get_undesirables()
			check_foreign_audio = source_utils.check_foreign_audio()
		except:
			source_utils.scraper_error('YTSMX')
			return sources

		for torrent in torrents:
			try:
				quality = torrent.get('quality')
				type = torrent.get('type')
				hash = torrent.get('hash')
				name = '%s.[%s].[%s].[YTS.MX]' % (title_long, quality, type)

				if not source_utils.check_title(title, aliases, name, hdlr, year, years): continue
				name_info = source_utils.info_from_name(name, title, year, hdlr)
				if source_utils.remove_lang(name_info, check_foreign_audio): continue
				if undesirables and source_utils.remove_undesirables(name_info, undesirables): continue

				url = 'magnet:?xt=urn:btih:%s&dn=%s' % (hash, name)

				try:
					seeders = torrent.get('seeds')
					if self.min_seeders > seeders: continue
				except: seeders = 0

				quality, info = source_utils.get_release_quality(name_info, url)
				try:
					size = torrent.get('size')
					dsize, isize = source_utils._size(size)
					info.insert(0, isize)
				except: dsize = 0
				info = ' | '.join(info)

				sources_append({
					'source': 'torrent', 'language': 'en', 'direct': False, 'debridonly': True,
					'provider': 'ytsmx', 'hash': hash, 'url': url, 'name': name, 'name_info': name_info,
					'quality': quality, 'info': info, 'size': dsize, 'seeders': seeders
				})
			except:
				source_utils.scraper_error('YTSMX')
		return sources

