import argparse
import os
import re
import shutil
import subprocess
from pathlib import Path
import tempfile
import zipfile

DEFAULT_OLD_PKG = "org.xbmc.kodi"

DEPENDENCY_IDS = [
    "script.module.requests",
    "script.module.urllib3",
    "script.module.certifi",
    "script.module.idna",
    "script.module.chardet",
]

DENSITY_SIZES = {
    "mipmap-mdpi": 48,
    "mipmap-hdpi": 72,
    "mipmap-xhdpi": 96,
    "mipmap-xxhdpi": 144,
    "mipmap-xxxhdpi": 192,
}

ICON_FILENAMES = {
    "ic_launcher.png",
    "ic_launcher_round.png",
    "ic_launcher_foreground.png",
    "ic_launcher_background.png",
    "ic_launcher_foreground.webp",
    "ic_launcher_background.webp",
    "ic_launcher.webp",
    "ic_launcher_round.webp",
}

MEDIA_ICON_SIZES = {
    "icon16x16.png": 16,
    "icon32x32.png": 32,
    "icon48x48.png": 48,
    "icon80x80.png": 80,
    "icon120x120.png": 120,
    "icon256x256.png": 256,
}

def run(cmd, cwd=None):
    p = subprocess.run(cmd, cwd=cwd, shell=False, text=True, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(
            f"Command failed: {' '.join(map(str, cmd))}\n\nSTDOUT:\n{p.stdout}\n\nSTDERR:\n{p.stderr}"
        )
    return p.stdout

def safe_read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1")

def safe_write(path: Path, s: str):
    path.write_text(s, encoding="utf-8")

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def extract_zip(zip_path: Path, out_dir: Path):
    ensure_dir(out_dir)
    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall(out_dir)

def find_addon_root(root: Path) -> Path:
    for p in root.rglob("addon.xml"):
        return p.parent
    raise RuntimeError(f"addon.xml not found inside {root}")

def copytree_clean(src: Path, dst: Path):
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)

def set_app_name(decoded: Path, app_name: str):
    strings = decoded / "res" / "values" / "strings.xml"
    if not strings.exists():
        return
    txt = safe_read(strings)
    if re.search(r'<string\s+name="app_name">.*?</string>', txt, flags=re.DOTALL):
        txt = re.sub(
            r'(<string\s+name="app_name">)(.*?)(</string>)',
            r"\1" + app_name + r"\3",
            txt,
            flags=re.DOTALL,
        )
    else:
        txt = re.sub(
            r"(<resources[^>]*>)",
            r'\1\n    <string name="app_name">' + app_name + r"</string>\n",
            txt,
            count=1,
        )
    safe_write(strings, txt)

def _open_rgba(path: Path):
    try:
        from PIL import Image
    except Exception:
        raise RuntimeError("Pillow is required. Install:  python -m pip install pillow")
    return Image.open(path).convert("RGBA")

def _save_image(img, target: Path):
    ext = target.suffix.lower()
    if ext == ".webp":
        img.save(target, format="WEBP", quality=95, method=6)
    elif ext == ".jpg" or ext == ".jpeg":
        rgb = img.convert("RGB")
        rgb.save(target, format="JPEG", quality=95)
    else:
        img.save(target, format="PNG", optimize=True)

def _resize_exact(img, w, h):
    from PIL import Image
    return img.resize((w, h), Image.LANCZOS)

def install_icon_everywhere(decoded: Path, icon_png: Path):
    try:
        from PIL import Image
    except Exception:
        raise RuntimeError("Pillow is required. Install:  python -m pip install pillow")

    if not icon_png.exists():
        raise FileNotFoundError(icon_png)

    base = _open_rgba(icon_png)
    count = 0

    res_dir = decoded / "res"
    if res_dir.exists():
        for folder, size in DENSITY_SIZES.items():
            d = res_dir / folder
            if not d.exists():
                continue
            for name in ("ic_launcher.png", "ic_launcher_round.png", 
                        "ic_launcher_foreground.png", "ic_launcher_background.png"):
                target = d / name
                if target.exists():
                    out = base.resize((size, size), Image.LANCZOS)
                    out.save(target, format="PNG", optimize=True)
                    count += 1

        for p in res_dir.rglob("*"):
            if not p.is_file():
                continue
            if p.name in ICON_FILENAMES:
                parent = p.parent.name
                size = DENSITY_SIZES.get(parent, 192)
                out = base.resize((size, size), Image.LANCZOS)
                _save_image(out, p)
                count += 1

    media_dir = decoded / "assets" / "media"
    if media_dir.exists():
        for name, size in MEDIA_ICON_SIZES.items():
            target = media_dir / name
            if target.exists():
                out = base.resize((size, size), Image.LANCZOS)
                _save_image(out, target)
                count += 1
                print(f"      {name} ({size}x{size})")

    print(f"    Replaced {count} icon files")

def install_splash(decoded: Path, splash_path: Path):
    if splash_path is None:
        return
    if not splash_path.exists():
        raise FileNotFoundError(splash_path)

    src = _open_rgba(splash_path)

    media_dir = decoded / "assets" / "media"
    if not media_dir.exists():
        print("    assets/media/ not found!")
        return

    splash_jpg = media_dir / "splash.jpg"
    if splash_jpg.exists():
        orig = _open_rgba(splash_jpg)
        orig_w, orig_h = orig.size
        out = _resize_exact(src, orig_w, orig_h)
        _save_image(out, splash_jpg)
        print(f"    Replaced: splash.jpg ({orig_w}x{orig_h})")
    else:
        print("    splash.jpg not found!")

def install_banner(decoded: Path, banner_path: Path):
    if banner_path is None:
        return
    if not banner_path.exists():
        raise FileNotFoundError(banner_path)

    src = _open_rgba(banner_path)
    count = 0

    media_dir = decoded / "assets" / "media"
    if media_dir.exists():
        banner_file = media_dir / "banner.png"
        if banner_file.exists():
            orig = _open_rgba(banner_file)
            w, h = orig.size
            out = _resize_exact(src, w, h)
            _save_image(out, banner_file)
            count += 1
            print(f"    Replaced: assets/media/banner.png ({w}x{h})")

    res_dir = decoded / "res"
    if res_dir.exists():
        for drawable_dir in res_dir.iterdir():
            if not drawable_dir.is_dir():
                continue
            if not drawable_dir.name.startswith("drawable"):
                continue
            
            for banner_name in ["banner.png", "banner.webp", "banner.jpg", 
                               "app_banner.png", "tv_banner.png"]:
                banner_file = drawable_dir / banner_name
                if banner_file.exists():
                    try:
                        orig = _open_rgba(banner_file)
                        w, h = orig.size
                        out = _resize_exact(src, w, h)
                        _save_image(out, banner_file)
                        count += 1
                        print(f"    Replaced: res/{drawable_dir.name}/{banner_name} ({w}x{h})")
                    except Exception as e:
                        print(f"    Warning: Could not replace {banner_file}: {e}")

    if count == 0:
        print("    No banner files found to replace")
    else:
        print(f"    Total: {count} banner files replaced")

def update_addon_manifest(decoded: Path, addon_ids: list):
    """Add addons to manifest - ONLY service and dependencies!
    
    Do NOT add wizard and repo to manifest - this prevents Kodi from
    registering them as APK built-in addons with APK UUID as origin.
    """
    manifest_xml = decoded / "assets" / "system" / "addon-manifest.xml"
    if not manifest_xml.exists():
        print(f"    WARNING: addon-manifest.xml not found!")
        return
    
    txt = safe_read(manifest_xml)
    added = []
    for addon_id in addon_ids:
        if addon_id not in txt:
            txt = txt.replace("</addons>", f"    <addon>{addon_id}</addon>\n</addons>")
            added.append(addon_id)
    
    safe_write(manifest_xml, txt)
    
    if added:
        print(f"    Added to manifest: {', '.join(added)}")

def main():
    ap = argparse.ArgumentParser(description="Build MasterKodi IL APK")
    ap.add_argument("--kodi-apk", required=True, help="Path to original Kodi APK")
    ap.add_argument("--apktool-jar", required=True, help="Path to apktool jar")
    ap.add_argument("--wizard-zip", required=True, help="Path to MasterKodi IL Wizard zip")
    ap.add_argument("--service-zip", required=True, help="Path to service.kodi.il.firstrun.zip")
    ap.add_argument("--repo-zip", default=None, help="Path to repository.masterkodi.il.zip (optional)")
    ap.add_argument("--kodi-addons-dir", required=True, help="Path to Kodi addons dir with dependencies")
    ap.add_argument("--build-tools-dir", required=True, help="Path to Android build-tools dir")
    ap.add_argument("--keystore", required=True, help="Path to keystore")
    ap.add_argument("--alias", required=True, help="Keystore alias")
    ap.add_argument("--storepass", required=True, help="Keystore password")
    ap.add_argument("--keypass", required=True, help="Key password")
    ap.add_argument("--output", default="MasterKodiIL.apk", help="Output APK name")
    ap.add_argument("--app-name", default="MasterKodi IL", help="App name in launcher")
    ap.add_argument("--icon", default=None, help="Path to icon PNG")
    ap.add_argument("--banner", default=None, help="Path to banner PNG")
    ap.add_argument("--splash", default=None, help="Path to splash image")

    args = ap.parse_args()

    kodi_apk = Path(args.kodi_apk)
    apktool_jar = Path(args.apktool_jar)
    wizard_zip = Path(args.wizard_zip)
    service_zip = Path(args.service_zip)
    repo_zip = Path(args.repo_zip) if args.repo_zip else None
    deps_dir = Path(args.kodi_addons_dir)
    build_tools = Path(args.build_tools_dir)
    keystore = Path(args.keystore)
    out_apk = Path(args.output)

    # Validate paths
    for p, name in [(kodi_apk, "Kodi APK"), (apktool_jar, "apktool"), 
                    (wizard_zip, "Wizard ZIP"), (service_zip, "Service ZIP"),
                    (deps_dir, "Dependencies dir"), (keystore, "Keystore")]:
        if not p.exists():
            raise FileNotFoundError(f"{name} not found: {p}")
    
    if repo_zip and not repo_zip.exists():
        raise FileNotFoundError(f"Repository ZIP not found: {repo_zip}")

    # cross-platform: Windows uses .exe/.bat, Linux/macOS (CI) uses bare names
    _is_win = os.name == "nt"
    zipalign = build_tools / ("zipalign.exe" if _is_win else "zipalign")
    apksigner = build_tools / ("apksigner.bat" if _is_win else "apksigner")
    if not zipalign.exists():
        raise FileNotFoundError(f"zipalign not found: {zipalign}")
    if not apksigner.exists():
        raise FileNotFoundError(f"apksigner not found: {apksigner}")

    work = Path("work_masterkodi_il")
    decoded = work / "decoded"
    unsigned_apk = work / "unsigned.apk"
    aligned_apk = work / "aligned.apk"

    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)

    print("\n" + "="*60)
    print("  MasterKodi IL APK Builder v3")
    print("="*60)

    print("\n[1/7] Decode APK")
    run(["java", "-jar", str(apktool_jar), "d", str(kodi_apk), "-o", str(decoded), "-f"])

    print("\n[2/7] Inject wizard + service + repository")
    target_addons = decoded / "assets" / "addons"
    ensure_dir(target_addons)

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        wz = td / "wiz"
        sz = td / "srv"
        extract_zip(wizard_zip, wz)
        extract_zip(service_zip, sz)

        wiz_root = find_addon_root(wz)
        srv_root = find_addon_root(sz)

        copytree_clean(wiz_root, target_addons / wiz_root.name)
        print(f"    Copied: {wiz_root.name}")
        copytree_clean(srv_root, target_addons / srv_root.name)
        print(f"    Copied: {srv_root.name}")
        
        # Add repository if provided
        if repo_zip:
            rz = td / "repo"
            extract_zip(repo_zip, rz)
            repo_root = find_addon_root(rz)
            copytree_clean(repo_root, target_addons / repo_root.name)
            print(f"    Copied: {repo_root.name}")

    print("\n[3/7] Copy dependencies")
    for dep in DEPENDENCY_IDS:
        src = deps_dir / dep
        if not src.exists():
            raise FileNotFoundError(f"Missing dependency: {src}")
        copytree_clean(src, target_addons / dep)
        print(f"    Copied: {dep}")

    print("\n[4/7] Update addon-manifest.xml")
    # IMPORTANT: Only add service and dependencies to manifest!
    # Do NOT add wizard and repo - prevents Kodi from registering them
    # as APK built-in addons with APK UUID as origin
    manifest_addons = [
        "service.kodi.il.firstrun",  # Service needs to run
    ]
    manifest_addons.extend(DEPENDENCY_IDS)  # Dependencies needed
    # NOTE: wizard and repo are NOT added to manifest!
    update_addon_manifest(decoded, manifest_addons)
    print("    NOTE: wizard and repo NOT added to manifest (to prevent APK UUID origin)")

    print("\n[5/7] Patch resources")
    print(f"  App name: {args.app_name}")
    set_app_name(decoded, args.app_name)
    
    if args.icon:
        print(f"  Icon: {args.icon}")
        install_icon_everywhere(decoded, Path(args.icon))
    
    if args.splash:
        print(f"  Splash: {args.splash}")
        install_splash(decoded, Path(args.splash))
    
    if args.banner:
        print(f"  Banner: {args.banner}")
        install_banner(decoded, Path(args.banner))

    print("\n[6/7] Build unsigned APK")
    run(["java", "-jar", str(apktool_jar), "b", str(decoded), "-o", str(unsigned_apk)])

    print("\n[7/7] Sign APK")
    run([str(zipalign), "-f", "4", str(unsigned_apk), str(aligned_apk)])
    
    if out_apk.exists():
        out_apk.unlink()

    run([
        str(apksigner), "sign",
        "--ks", str(keystore),
        "--ks-key-alias", args.alias,
        "--ks-pass", f"pass:{args.storepass}",
        "--key-pass", f"pass:{args.keypass}",
        "--out", str(out_apk),
        str(aligned_apk)
    ])

    print("\n" + "="*60)
    print("  [OK] SUCCESS!")
    print("="*60)
    print(f"  APK: {out_apk.resolve()}")
    print(f"  App Name: {args.app_name}")
    print(f"  Repository: {'Included' if repo_zip else 'Not included'}")
    print()

if __name__ == "__main__":
    main()
