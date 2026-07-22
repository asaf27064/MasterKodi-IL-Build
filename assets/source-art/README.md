# Source art (pristine originals)

Full-resolution master copies of art that ships **downscaled** in the base
bundles. Kept here so the optimisation is reversible and any future re-derivation
starts from the best source rather than from an already-compressed copy.

Nothing in this folder is shipped to devices. It is not part of any bundle,
manifest or config zip — it is an archive.

## build_icons/

The 26 favourites/menu icons used by **Estuary** (`favourites.xml` +
`Home.xml`). Originals are RGBA PNGs up to **1056 px**, 33.8 MB total.

The bundles ship these capped at **720 px** (17.7 MB, −16.1 MB per install).
That cap is lossless *in practice*, not a compromise:

> Kodi caches GUI textures at `<imageres>` (default **720**) and our
> `advancedsettings.xml` does not raise it, so every pixel above 720 is
> discarded by Kodi before anything is drawn.

Measured difference after alpha-compositing, versus the originals:

| Source cap | Total size | Visible difference |
|---|---|---|
| original (1056 px) | 33.8 MB | — |
| **720 px (shipped)** | **17.7 MB** | **0.0000 / 255** |
| 512 px | 9.9 MB | 3.79 / 255 — rejected, a real loss |

`build_icons.sha256.txt` records name, dimensions, byte size and a sha256 prefix
for each original, so drift or accidental replacement is detectable.

### Regenerating the shipped versions

```
python tools/optimize_media.py <bundle.zip> --max-px 720
```

The tool rewrites `media/**.png` in place inside a bundle zip. Re-uploading the
optimised `FenLight_Estuary.zip` is enough for both builds to stay optimised:
repack bundles copy `media/` from `originals/`, and the POV bundle pulls
`media/build_icons` from that same original via `seed_include` in `build.json`.

### Note on Zephyr

Zephyr does **not** use these icons. Its menus referenced them only in the
vestigial `<icon>` field while rendering `<thumb>` (the skin's own
`director/tv/configure.png`); those dead references were removed in config 49.
`build_icons` is Estuary-only.
