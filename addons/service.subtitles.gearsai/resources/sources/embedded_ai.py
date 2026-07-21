# -*- coding: utf-8 -*-
# MASTERKODI: "תרגום מובנה ← עברית (AI)" source -- translates the playing
# file's embedded foreign subtitle track to Hebrew via ai_bridge. Plugged into
# the normal source dispatch (download_sub imports this module by name), so
# caching, row handling and both subtitle windows work unchanged.
import os

from resources.modules import log


def download(download_data, MySubFolder):
    from resources.aisubs import ai_bridge
    hebrew = ai_bridge.translate_embedded(download_data)
    if hebrew == 'DECLINED':
        # User said no (auto re-pick asked first). Raising routes back as a
        # normal failed download; ai_bridge already skipped every side effect.
        raise Exception('embedded AI translation declined')
    if not hebrew or not hebrew.strip():
        raise Exception('embedded AI translation produced nothing')
    path = os.path.join(MySubFolder, 'embedded_ai_he.srt')
    with open(path, 'w', encoding='utf-8', newline='') as f:
        f.write(hebrew)
    log.warning('[gearsai-emb] embedded AI subtitle written: %s' % path)
    return path
