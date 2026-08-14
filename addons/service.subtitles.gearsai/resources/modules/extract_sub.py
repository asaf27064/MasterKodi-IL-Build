
import zipfile
import xbmcvfs
import os,gzip,shutil,re
from resources.modules import log
# Priority order: prefer TEXT subs (.srt/.ass/.str/.sub) so Kodi renders them
# crisp with the user's configured font. Image-based VobSub/PGS (.idx/.sup) are
# a last resort only -- they're bitmaps that ignore Kodi's font and look blurry.
exts = [".srt", ".ass", ".str", ".sub", ".idx", ".sup"]
_TEXT_EXTS = (".srt", ".str", ".sub")


def convert_to_utf(file):
    """Normalise a text subtitle to UTF-8 WITHOUT mangling it. The old code
    always decoded as Windows-Hebrew (cp1255), which garbles subs that are
    already UTF-8. Now we detect: keep valid UTF-8 as-is, else try chardet and
    the common Hebrew legacy encodings."""
    try:
        with open(file, 'rb') as f:
            raw = f.read()
        if not raw:
            return
        text = None
        if raw[:3] == b'\xef\xbb\xbf':          # UTF-8 BOM
            text = raw[3:].decode('utf-8', 'replace')
        else:
            try:
                text = raw.decode('utf-8')       # already valid UTF-8 -> leave it
            except Exception:
                enc = None
                try:
                    import chardet
                    enc = (chardet.detect(raw) or {}).get('encoding')
                except Exception:
                    enc = None
                for cand in (enc, 'cp1255', 'windows-1255', 'iso-8859-8', 'utf-8'):
                    if not cand:
                        continue
                    try:
                        text = raw.decode(cand)
                        break
                    except Exception:
                        continue
                if text is None:
                    text = raw.decode('utf-8', 'replace')
        with open(file, 'w', encoding='utf-8', newline='') as output:
            output.write(text)
    except Exception:
        pass


def _has_idx_sibling(files, ufile):
    """True if this .sub has a same-name .idx next to it -> it's a binary VobSub
    pair (image-based), NOT a MicroDVD text .sub."""
    base = os.path.splitext(ufile)[0].lower()
    for other in files:
        if (os.path.splitext(other)[1].lower() == ".idx"
                and os.path.splitext(other)[0].lower() == base):
            return True
    return False


def _pick_best(MySubFolder):
    """Return the best extracted subtitle file, preferring real TEXT subs over
    image formats. Key subtlety: a `.sub` is MicroDVD *text* only when it has no
    `.idx` sibling; a `.sub` sitting next to a `.idx` is a binary VobSub stream --
    running convert_to_utf() on that corrupts it (which is why such subs 'download'
    but never render)."""
    try:
        files = xbmcvfs.listdir(MySubFolder)[1]
    except Exception:
        return None
    # Pass 1: genuine text subtitles.
    for ext in (".srt", ".ass", ".str", ".sub"):
        for ufile in files:
            if os.path.splitext(ufile)[1].lower() != ext:
                continue
            if ext == ".sub" and _has_idx_sibling(files, ufile):
                continue  # VobSub binary -> handle in the image pass, don't convert
            f = os.path.join(MySubFolder, ufile)
            if ext in _TEXT_EXTS:
                convert_to_utf(f)
            return f
    # Pass 2: image-based subs (VobSub .idx/.sub, PGS .sup) -- last resort only,
    # and NEVER text-converted.
    for ext in (".idx", ".sup", ".sub"):
        for ufile in files:
            if os.path.splitext(ufile)[1].lower() == ext:
                return os.path.join(MySubFolder, ufile)
    return None


def _looks_like_text_subtitle(raw):
    """Does this payload actually contain subtitle timing?

    SubRip/WebVTT use '-->', SSA/ASS carry a [Script Info] header or Dialogue:
    lines, MicroDVD uses {start}{end}. Anything else -- an HTML error page, a
    JSON error, a truncated download -- is not a subtitle, however it is named.
    """
    head = raw[:200000]
    if b'-->' in head or b'[Script Info]' in head or b'Dialogue:' in head:
        return True
    return bool(re.search(br'\{\d+\}\{\d+\}', head))


def _passthrough(archive_file):
    """Hand back a download that is NOT an archive -- carefully.

    Ktuvit serves plain .srt, so this is its NORMAL path, not an edge case. It
    used to return the file untouched, which skipped the convert_to_utf() the
    zip path performs. Ktuvit's files are cp1255 (windows-1255), so Kodi
    received a non-UTF-8 subtitle: the track loads and is selectable, and
    NOTHING renders (Asaf 2026-08-14, reproduced against the live site -- 5/5
    downloads were cp1255). It only appeared to work because the unrelated
    'auto_fix_sub_punctuation' feature happens to run chardet and rewrite the
    file as UTF-8; with that setting off, or chardet unsure, the subtitle is
    silently blank.

    So: convert the encoding here too, and refuse a payload that is not a
    subtitle at all instead of reporting a successful download. Binary
    image-based subs are passed through untouched -- text-converting those
    corrupts them, which is the same rule _pick_best already follows.
    """
    ext = os.path.splitext(archive_file)[1].lower()
    if ext in ('.idx', '.sup') or (ext == '.sub' and _has_idx_sibling(
            os.listdir(os.path.dirname(archive_file) or '.'), os.path.basename(archive_file))):
        return archive_file                     # VobSub/PGS bitmaps: never touch
    try:
        with open(archive_file, 'rb') as fh:
            raw = fh.read()
    except Exception as e:
        log.warning('Passthrough read failed: %s' % e)
        return archive_file                     # unreadable here -> old behaviour
    if not raw or not _looks_like_text_subtitle(raw):
        log.warning('Passthrough: not a subtitle (%d bytes, starts %r) -> failing'
                    % (len(raw), raw[:24]))
        return '0'                              # honest failure, not a blank sub
    convert_to_utf(archive_file)                # the zip path does this too
    return archive_file


def extract(archive_file, MySubFolder):
    try:
        with zipfile.ZipFile(archive_file, 'r') as zip_ref:
            zip_ref.extractall(MySubFolder)
        os.remove(archive_file)
        picked = _pick_best(MySubFolder)
        return picked if picked else '0'
    except Exception as e:
        log.warning('Error Extract:' + str(e))
        return _passthrough(archive_file)


def g_extract(archive_file, dest, MySubFolder):
    log.warning(archive_file)
    with gzip.open(archive_file, 'rb') as f_in:
        with open(dest, 'wb') as f_out:
            shutil.copyfileobj(f_in, f_out)
    os.remove(archive_file)
    return _pick_best(MySubFolder)
