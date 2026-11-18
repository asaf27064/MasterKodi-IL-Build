# ported from Starfleet's torrent_sources.py for gearsscrapers
"""
	gearsscrapers Project
"""

import re
import html as _html
from urllib.parse import quote_plus
from gearsscrapers.modules import client
from gearsscrapers.modules import sfport

# bitcq.com -- a DHT magnet index: the search page lists name, size and peers, with the info hash in each row.

RE_ROW = re.compile(r'<tr id="[^"]*">(.*?)</tr>', re.S)
RE_HASH = re.compile(r'magnet:\?xt=urn:btih:([0-9A-Fa-f]{40})')
RE_NAME = re.compile(r'<a href="/torrent/\d+">\s*(.*?)\s*</a>', re.S)
RE_CELLS = re.compile(r'<td>\s*([0-9.,]+\s*[KMGT]?i?B)\s*</td>\s*<td>\s*(\d+)\s*</td>', re.I)


class source(sfport.Provider):
	name = 'bitcq'
	log = 'BITCQ'
	priority = 6
	pack_capable = True

	def fetch(self, query):
		page = client.request('https://bitcq.com/search?q=%s' % quote_plus(query), timeout=10)
		if not page: return []
		out = []
		for row in RE_ROW.findall(page):
			hm, nm = RE_HASH.search(row), RE_NAME.search(row)
			if not hm or not nm: continue
			cells = RE_CELLS.search(row)
			out.append({'name': _html.unescape(re.sub(r'<[^>]+>', '', nm.group(1))).strip(), 'hash': hm.group(1),
						'seeders': int(cells.group(2)) if cells else 0, 'size': cells.group(1).strip() if cells else ''})
		return out
