
import zipfile
import xbmcvfs
import os,gzip,shutil
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


def extract(archive_file, MySubFolder):
    try:
        with zipfile.ZipFile(archive_file, 'r') as zip_ref:
            zip_ref.extractall(MySubFolder)
        os.remove(archive_file)
        picked = _pick_best(MySubFolder)
        return picked if picked else '0'
    except Exception as e:
        log.warning('Error Extract:' + str(e))
        return archive_file


def g_extract(archive_file, dest, MySubFolder):
    log.warning(archive_file)
    with gzip.open(archive_file, 'rb') as f_in:
        with open(dest, 'wb') as f_out:
            shutil.copyfileobj(f_in, f_out)
    os.remove(archive_file)
    return _pick_best(MySubFolder)
