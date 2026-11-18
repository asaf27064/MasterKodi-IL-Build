# ported from Starfleet's torrent_sources.py for gearsscrapers
"""
	gearsscrapers Project
"""

import re
import html as _html
from urllib.parse import quote_plus
from gearsscrapers.modules import client
from gearsscrapers.modules import sfport
from gearsscrapers.modules import workers

# 0mag.net -- a magnet index. The search page only lists names, so the first few rows' pages are read for the magnet (and its size).

BASE = 'https://0mag.net'
RE_ROW = re.compile(r'<a[^>]+href="(/![A-Za-z0-9]+)"[^>]*>(.*?)</a>', re.S)
RE_MAGNET = re.compile(r'magnet:\?xt=urn:btih:([0-9a-fA-F]{40})(?:&amp;|&)dn=[^"\'<\s]*?(?:&amp;|&)xl=(\d+)|magnet:\?xt=urn:btih:([0-9a-fA-F]{40})')


class source(sfport.Provider):
	name = 'zeromag'
	log = 'ZEROMAG'
	priority = 7
	pack_capable = True
	max_rows = 8

	def fetch(self, query):
		page = client.request('%s/search?q=%s&sort=relevance' % (BASE, quote_plus(query)), timeout=10)
		if not page: return []
		rows, seen = [], set()
		for path, label in RE_ROW.findall(page):
			name = _html.unescape(re.sub(r'<[^>]+>', '', label)).strip()
			if not name or path in seen: continue
			seen.add(path)
			rows.append((path, name))
			if len(rows) >= self.max_rows: break
		out = []

		def one(path, name):
			try:
				detail = client.request(BASE + path, timeout=8)
				m = RE_MAGNET.search(detail or '')
				if not m: return
				out.append({'name': name, 'hash': (m.group(1) or m.group(3)), 'seeders': 0, 'size': int(m.group(2)) if m.group(2) else 0})
			except: pass
		threads = [workers.Thread(one, p, n) for p, n in rows]
		[i.start() for i in threads]
		[i.join() for i in threads]
		return out
