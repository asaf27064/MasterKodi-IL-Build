"""EZTV's IMDb-based JSON API, replacing the website scraper."""
import re
from json import loads
from time import monotonic
from urllib.parse import urlencode
from cocoscrapers.modules import client, source_utils


class source:
    priority = 6
    pack_capable = True
    hasMovies = False
    hasEpisodes = True

    def __init__(self):
        self.language = ['en']
        self.base_link = 'https://eztvx.to'
        self.min_seeders = 0
        self.timeout = 12
        self.page_limit = 100
        self.max_pages = 100

    def get_torrents(self, data):
        """Read show-specific pages within a bounded request budget."""
        imdb = str(data.get('imdb') or '')
        if not re.fullmatch(r'(?:tt)?\d+', imdb): return
        imdb = imdb[2:] if imdb.startswith('tt') else imdb
        if not int(imdb): return
        deadline = monotonic() + self.timeout
        seen = set()
        for page in range(1, self.max_pages + 1):
            remaining = deadline - monotonic()
            if remaining <= 0: return
            url = self.base_link + '/api/get-torrents?' + urlencode(
                {'imdb_id': imdb, 'limit': self.page_limit, 'page': page})
            try:
                payload = loads(client.request(url, timeout=min(5, remaining)))
                if not isinstance(payload, dict): return
                response_imdb = str(payload.get('imdb_id') or '').replace('tt', '', 1)
                if response_imdb and response_imdb.lstrip('0') != imdb.lstrip('0'): return
                files = payload.get('torrents')
                if not isinstance(files, list) or not files: return
                added = False
                for item in files:
                    if not isinstance(item, dict): continue
                    item_imdb = str(item.get('imdb_id') or '').replace('tt', '', 1)
                    if item_imdb and item_imdb.lstrip('0') != imdb.lstrip('0'): continue
                    key = str(item.get('hash') or item.get('magnet_url') or item.get('id') or '')
                    if not key or key.lower() in seen: continue
                    seen.add(key.lower())
                    added = True
                    yield item
                if not added: return  # Guard against an API that repeats the same page.
                count = payload.get('torrents_count')
                if count is not None and page * self.page_limit >= int(count): return
                if len(files) < self.page_limit: return
            except Exception:
                source_utils.scraper_error('EZTV')
                return  # Keep results already yielded if a later page fails.

    def sources(self, data, hostDict):
        return self._sources(data)

    def sources_packs(self, data, hostDict, search_series=False, total_seasons=None, bypass_filter=False):
        if search_series: return []
        return self._sources(data, pack=True, bypass_filter=bypass_filter)

    def _sources(self, data, pack=False, bypass_filter=False):
        results = []
        if not data or not data.get('tvshowtitle'): return results
        try:
            title = data['tvshowtitle'].replace('&', 'and').replace('Special Victims Unit', 'SVU').replace('/', ' ').replace('$', 's')
            aliases = data.get('aliases', [])
            year = data['year']
            season = str(data['season'])
            hdlr = 'S%02dE%02d' % (int(season), int(data['episode'])) if not pack else None
            airdate = source_utils.episode_airdate(data)
            undesirables = source_utils.get_undesirables()
            foreign_audio = source_utils.check_foreign_audio()
        except Exception:
            source_utils.scraper_error('EZTV')
            return results
        for file in self.get_torrents(data):
            try:
                # The filename retains release identifiers and tags more reliably than title.
                name = source_utils.clean_name(file.get('filename') or file['title'])
                hash_value = str(file.get('hash') or '')
                if not hash_value:
                    hash_value = re.search(r'btih:([^&]+)', file.get('magnet_url', ''), re.I).group(1)
                if len(hash_value) == 32:
                    hash_value = source_utils.base32_to_hex(hash_value, 'EZTV')
                if not re.fullmatch(r'[a-fA-F0-9]{40}', hash_value): continue
                hash_value = hash_value.lower()
                episode_start, episode_end = 0, 0
                if pack:
                    if not bypass_filter:
                        valid, episode_start, episode_end = source_utils.filter_season_pack(title, aliases, year, season, name)
                        if not valid: continue
                    name_info = source_utils.info_from_name(name, title, year, season=season, pack='season')
                else:
                    if not source_utils.check_title(title, aliases, name, hdlr, year, airdate=airdate): continue
                    name_info = source_utils.info_from_name(name, title, year, hdlr, data.get('title'), airdate=airdate)
                if source_utils.remove_lang(name_info, foreign_audio): continue
                if undesirables and source_utils.remove_undesirables(name_info, undesirables): continue
                try: seeders = int(file.get('seeds') or 0)
                except (TypeError, ValueError): seeders = 0
                if seeders < self.min_seeders: continue
                url = 'magnet:?xt=urn:btih:%s' % hash_value
                quality, info = source_utils.get_release_quality(name_info, url)
                try:
                    size, label = source_utils.convert_size(float(file['size_bytes']))
                    info.insert(0, label)
                except (KeyError, TypeError, ValueError): size = 0
                item = {'provider': 'eztv', 'source': 'torrent', 'seeders': seeders,
                        'hash': hash_value, 'name': name, 'name_info': name_info,
                        'quality': quality, 'language': 'en', 'url': url,
                        'info': ' | '.join(info), 'direct': False, 'debridonly': True, 'size': size}
                if pack:
                    item['package'] = 'season'
                    if episode_start:
                        item.update(episode_start=episode_start, episode_end=episode_end)
                results.append(item)
            except Exception:
                source_utils.scraper_error('EZTV')
        return results
