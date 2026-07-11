# Hebrew overlays — the one place for Hebrew

The Hebrew-modified addons (`plugin.video.gears`, `skin.arctic.fuse.3`) are **not**
committed as merged trees. Instead this folder holds only what we actually change
on top of clean upstream, plus a `base.json` per addon. CI reconstructs the merged
addon on demand (`tools/apply_overlay.py`) so the Hebrew stays isolated and
re-appliable when upstream ships a new version.

```
overlays/
  plugin.video.gears/
    base.json                     # where clean upstream comes from + versions
    base/plugin.video.gears-2.3.1.zip   # committed clean base (upstream deletes old zips)
    files/                        # every file that differs from clean upstream (137)
  skin.arctic.fuse.3/
    base.json                     # base fetched live from the permanent GitHub tag archive
    files/                        # 13 Hebrew files (fonts, OSD button, strings)
```

## How a build works
1. `apply_overlay.py overlays addons` — for each overlay: get the clean base
   (committed `base_zip_local`, or download `base_zip_url`), strip git junk,
   de-version the top folder, copy `files/` on top → writes `addons/<id>/`.
2. `build_addons.py` zips it reproducibly like any other addon.

The reconstructed tree is **byte-identical** to what we used to commit — verify any
time with `python tools/apply_overlay.py --verify overlays addons`.

## When upstream releases a new version
`upstream-watch.yml` (every 6h) runs `check_upstream.py` and files an issue:
- **SAFE** — none of the files we overlay changed upstream; bump `base_version`
  (for gears also drop the new `base/<id>-<ver>.zip` and update `base_zip_local`),
  push, done.
- **MANUAL** — an upstream file we overlay changed; re-merge just those files into
  `files/` before bumping.

## Editing the Hebrew
Edit files under `overlays/<id>/files/` — that is the single source of truth.
Never edit a reconstructed `addons/plugin.video.gears/` or `addons/skin.arctic.fuse.3/`
tree; those are generated and git-ignored.
