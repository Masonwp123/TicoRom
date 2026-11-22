---
paths:
  - "src/ticorom/startup/platforms.py"
  - "src/ticorom/startup/default_platforms.yaml"
  - "src/ticorom/packages.py"
  - "tests/test_platforms.py"
---

# How TicoRom decides what is published: the platform map

Read this before changing the platform map, its format, or the default map.

How to apply:

- `platforms.yaml` is the only place that decides which RomM platforms are published. Never put a list of supported or unsupported consoles in the code.
- The map shipped in the image is `src/ticorom/startup/default_platforms.yaml`. It is copied to `CONFIG_PATH` on first start, and after that only the user's copy is read; never overwrite it.
- `tico_slug` must be one of tico's ROM folder names (listed at the end of §8).
- The YAML example in §8 is illustrative. The shipped default uses `zip_policy: keep`, because tico's cartridge emulators open archives themselves.

> Section numbers (§N) and "Appendix A" are those of the original design document. `.claude/CLAUDE.md` lists which file holds each section. *Observed* means tested on a real Switch; *unverified* means not tested.

## 8. Platform map

A YAML file, `platforms.yaml`, in `CONFIG_PATH`. Keyed by RomM's `fs_slug` (the folder name), since that's what users control; `slug` is accepted as a fallback.

**The map is the only place that decides which platforms are published.** The code holds no list of supported or unsupported consoles. Every RomM platform falls into one of three cases:

| In the map | Result |
|---|---|
| An entry under `platforms` | Published as described by the entry. |
| Named in `exclude` | Not published, and not mentioned again. This is how platforms with no tico console are left out. |
| Neither | Not published, with a warning at startup and in the sync report, so a new platform is never dropped unnoticed. |

The image carries a default map. On first start the bridge copies it into `CONFIG_PATH` if no `platforms.yaml` is there; from then on only that copy is read, and it is the user's to edit.

```yaml
defaults:
  zip_policy: keep               # keep | extract; tico's cartridge emulators open archives themselves
  multi_file: bundle             # bundle | skip

# RomM platforms that are deliberately not published, e.g. no tico console for them
exclude: [ps3, switch, xbox, xbox360, nintendo-dsi]

platforms:
  snes:
    tico_slug: snes
    install: tico/roms/snes
    extensions: [sfc, smc, fig, swc]   # in preference order
    zip_policy: extract

  gba:
    tico_slug: gba
    install: tico/roms/gba
    extensions: [gba]

  arcade:                              # tico has no "arcade" folder; pick fbneo, naomi, or atomiswave
    tico_slug: fbneo
    install: tico/roms/fbneo
    extensions: [zip, 7z]
    zip_policy: keep                   # romsets must stay zipped

  psx:
    tico_slug: psx
    install: tico/roms/psx
    extensions: [chd, cue, m3u, pbp, iso]
    companion_extensions: [bin]        # delivered only alongside a cue
    multi_file: bundle

  ngc:                                 # RomM calls it ngc, tico calls it gc
    tico_slug: gc
    install: tico/roms/gc
    extensions: [rvz, iso, gcm, gcz, ciso, wbfs, m3u]

  ps2:
    tico_slug: ps2
    install: tico/roms/ps2
    extensions: [chd, iso, cso, zso]
    chd_types: [cd]                    # a DVD-type CHD installed but did not launch (§9)
```

Per-platform fields:

| Field | Purpose |
|---|---|
| `tico_slug` | Console tag and shelf. |
| `install` | Destination folder. |
| `extensions` | What tico's core for that console loads. Anything else is skipped. |
| `companion_extensions` | Files valid only as part of a set. |
| `zip_policy` | Whether a library `.zip` is unpacked (`extract`) or delivered whole (`keep`). This is the "does tico need to extract" flag. |
| `multi_file` | Bundle or skip multi-file games. |
| `chd_types` | CHD variants the console's core launches (`cd`, `gd`, `dvd`). Others are skipped and reported. Default: all. |
| `enabled` | Set to `false` to stop publishing a platform while keeping its entry. For a platform that will never be published, use `exclude` instead. |

`tico_slug` is tico's ROM folder name. Spike C confirmed that all 23 produce a console shelf when used as a tag: `nes`, `snes`, `n64`, `gb`, `gbc`, `gba`, `nds`, `3ds`, `gc`, `wii`, `triforce`, `genesis`, `master-system`, `game-gear`, `sega-cd`, `saturn`, `dc`, `psx`, `psp`, `ps2`, `fbneo`, `naomi`, `atomiswave`.

Fill the extensions from tico's "Supported ROM Formats" page.

