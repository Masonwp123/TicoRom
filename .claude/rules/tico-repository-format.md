---
paths:
  - "src/ticorom/index.py"
  - "src/ticorom/packages.py"
  - "src/ticorom/covers.py"
  - "src/ticorom/downloads.py"
  - "src/ticorom/app.py"
  - "tests/test_index.py"
  - "tests/test_packages.py"
  - "tests/test_covers.py"
  - "tests/test_downloads.py"
  - "tests/test_app.py"
---

# How TicoRom talks to tico: the repository format and package mapping

Read this before changing what goes into `index.json`, how games are named, or how covers and downloads are served.

How to apply:

- Every package is `type: zip`. A single file goes out in file mode: `extract: false` plus `filename`. Never use `type: rom`; it failed on hardware.
- A package id is `romm-<INSTANCE_SLUG>-<rom id>`. Do not change this scheme: tico tracks installed games by id.
- `version` is the first 8 hex characters of the file's SHA-256, never a RomM timestamp.
- `filename` must be valid on FAT32 and exFAT and unique within its install folder, ignoring case (§9).
- Images must be `https://`. tico shows PNG, JPEG, WebP, GIF and BMP, but not AVIF. A changed image needs a new URL, because tico never re-checks an image it has.
- tico silently drops a package it considers invalid. A game missing from tico's list means tico rejected it, so check the index against §2.

> Section numbers (§N) and "Appendix A" are those of the original design document. `.claude/CLAUDE.md` lists which file holds each section. *Observed* means tested on a real Switch; *unverified* means not tested.

## 2. Background: what tico 0.8.0 expects

Source: the official "Creating a Store Repository" guide (see References). Anything not in that guide is marked *unverified* in this document. Behaviour seen in the spikes (§11) is marked *observed*.

A repository is one `index.json` at a plain URL. It may also be served compressed (`.zip`, `.7z`, `.rar`, `.tar.gz`, `.json.gz`).

Top level: `schema` (must be `1`), `name`, optional `description` / `icon` / `cover`, and `packages`.

Package fields relevant to ROM delivery:

| Field | Notes |
|---|---|
| `id` | Required. Unique across *all* repositories. `[a-z0-9._-]`, max 64 chars. |
| `name`, `description`, `tagline`, `author` | Display text. |
| `category` | `app` (default), `game`, `port`, `soundpack`. |
| `tags` | Max two. A console tag (name or tico slug) puts the game on that console's shelf. *Observed:* tico's ROM folder names work as tags (§8). |
| `version` | Compared as text; **any change** offers an update. |
| `icon`, `banner`, `screenshots` | Must be `https://`. Max 8 screenshots. *Observed:* PNG, JPEG (also progressive), WebP, GIF and BMP display; AVIF does not. `banner` and `screenshots` appear on the package page (§11, cover test). |
| `type` | `zip` (default), `nro`, `module`, `rom`. This design only uses `zip` (constraint 1). |
| `url` | `http://` or `https://` per the guide. *Observed:* only `https://` worked (§6.4). |
| `size` | Bytes. For `rom` it is required and must match exactly. *Observed:* not enforced for `type: zip`; a package whose `size` was about 10% too small still installed. |
| `sha256` | Optional but recommended; a mismatch is refused (*observed*, including with `extract: false`). *Observed:* uppercase hex is accepted. |
| `install` | Destination folder, e.g. `tico/roms/gba`. |
| `extract` | `false` saves the download unchanged. Default `true`. Requires `install`. *Observed:* the payload does not have to be a zip. |
| `extract_to_folder` | Unpacks into a subfolder named after the download. *Observed:* works, and the game still appeared on the console shelf. |
| `filename` | Saved name when `extract` is `false`. Required for `rom`. |
| `magnet`, `file` | Torrent delivery; `file` picks one file from a multi-file torrent. |

Constraints that shape this design:

1. **`type: rom` is not usable here.** The guide caps it at 64 MiB and asks for a `.gba` filename. *Observed:* a `rom` package with a `.sfc` filename was dropped from the listing, and `.gba` `rom` packages downloaded completely but then failed to install, with dummy data and with a real ROM (§11).
2. **Only `zip` is documented as an extractable package archive.** `.7z` / `.rar` packages are *unverified*. With `extract: false` the payload is not inspected, so any file can be delivered as-is (*observed*).
3. **No authentication is documented.** *Observed:* tico sends a bare GET with `Host`, `Accept: */*`, and a desktop Chrome `User-Agent`; no `Accept-Encoding` and no conditional headers. It follows 302 redirects for both the index and downloads, and sends `user:pass@host` URL credentials as HTTP Basic.
4. **No pagination.** One file lists everything. *Observed:* a 5,000-package index is not a problem (§6.5).
5. **Community repositories are restricted.** Torrent packages are hidden unless the user enables them in Settings > Store, and every install to an `install` folder prompts for confirmation unless the user changes that setting.
6. **tico resumes with HTTP `Range`.** The download endpoint must honor it. *Observed:* tico sends `Range: bytes=N-` with no `If-Range`, and resumes after a dropped connection, after Wi-Fi loss, and after tico itself was closed (§11).
7. **Invalid packages are dropped silently**; the rest of the repository still loads (guide, and *observed*). A package missing from the listing means tico rejected it.
8. **The index is fetched often.** *Observed:* twice when a repository is added, and every added repository appears to be fetched again when the Store reloads.

## 7. Package mapping

### 7.1 Delivery modes

The mapper picks one mode per ROM:

| Mode | When | tico package |
|---|---|---|
| **file** | Single file of any size with an extension the tico core loads. Includes a library `.zip` that must stay whole (arcade sets, or `zip_policy: keep`) | `type: zip`, `extract: false`, `install`, `filename`, `size`, `sha256` |
| **extract** | Library file is a `.zip` containing a ROM the platform wants unpacked | `type: zip`, `install` (default `extract: true`) |
| **bundle** | Multi-file game (`.cue` + `.bin`, `.m3u` + discs) | Bridge-built zip, `type: zip`, `install` |
| **skip** | Extension not accepted for the platform | Not listed; reported in the sync log |

*(As built, only the **file** mode exists. A library archive on a platform with `zip_policy: extract` is skipped as `extract_mode`, and a game made of several files as `multi_file`. All skip reasons are in `packages.py`.)*

The **file** mode replaces the *raw* and *keep-archive* modes of draft v1. `type: rom` is not used at all: it is GBA-only and failed in testing (§2, constraint 1), and one package shape for every single file is simpler.

All three modes are confirmed on hardware (§11): file mode with a real `.gba`, `.7z`, and `.chd`; extract mode with a compressed zip; bundle mode with a stored zip holding a `.cue` and two `.bin` files.

### 7.2 Large files and bundles

Spike A settled the 64 MiB question. With `extract: false`, tico saves the download unchanged under `filename` and does not check that it is a zip. Disc images (`.chd`, `.rvz`, `.iso`) are therefore delivered as-is, with no repacking and no extra disk. A real disc image of about 2 GiB installed and launched this way. The ceiling is the card's file system: files of 4 GiB or more fail on FAT32 (§9).

Only **bundles** still need a zip built by the bridge. A stored zip unpacks flat into the console folder with its file names intact (*observed*). Two ways to provide it:

1. **A disk cache of stored (uncompressed) zips.** Simple, but doubles storage for those games.
2. **A "virtual zip" endpoint.** A stored zip is fully predictable — headers + raw bytes, CRC32 from RomM or the hash pass — so its exact size, SHA-256, and byte ranges can be computed without materializing it. More code, no extra disk.

Plan: ship v1 with no support for bundles; later add size-capped LRU disk cache for bundles or virtual zip only if the cache becomes a problem.

### 7.3 Field mapping

| tico field | Source |
|---|---|
| `id` | `romm-<instance>-<rom id>` where `<instance>` is a short configured slug. Stable, ≤ 64 chars, won't collide with other repositories. |
| `name` | RomM `name`, falling back to the file name without tags. |
| `category` | `game` |
| `tags` | `[<tico ROM folder name>]`; the second slot is free for region or a collection name. |
| `type`, `extract` | Always `type: zip`. `extract: false` for the file mode; omitted for extract and bundle (§7.1). |
| `version` | First 8 hex chars of the payload SHA-256. **Not** RomM's `updated_at`: a metadata rescan would then offer a pointless update for every game. |
| `description`, `tagline` | Description: RomM summary (truncated). Tagline: the tags in the file name (e.g. `USA · Rev 1 · Disc 2`), so copies of the same game can be told apart (§9); otherwise the first genre and release year. |
| `icon` | Cover image, served by the bridge at `/r/<token>/img/<rom id>-<fingerprint>.<ext>`, so it is HTTPS and RomM stays off the console's network. See "Covers" below. |
| `url` | Bridge download URL. |
| `size`, `sha256` | From the hash cache; for bundles, of the built zip. |
| `install` | From the platform map, e.g. `tico/roms/snes`. |
| `filename` | Sanitized library file name (§9, "File names"). |

**The instance slug (`INSTANCE_SLUG`).** tico requires a package id to be unique across every repository a user has added, and drops duplicates (§2). RomM's ROM ids are small numbers that every RomM instance reuses, so `romm-4821` from one bridge would collide with `romm-4821` from another. The instance slug is a short label for *this* RomM that goes into every id, as `romm-<slug>-<rom id>`, e.g. `romm-home-4821`.

- `home` is a fine value for someone running one RomM. Two bridges that a user might add to the same console need different slugs.
- Allowed characters are those of a package id: lowercase letters, digits, `.`, `_`, `-`. Keep it short; the whole id is capped at 64 characters. The bridge validates this at startup.
- **Treat it as permanent.** tico tracks installed games and updates by id. Changing the slug changes every id, so tico would show every game as a new, uninstalled package.

**Covers.** Fetched from RomM during the sync (step 1) and stored as files in `CACHE_PATH/covers/`; the `covers` table records which file belongs to which game (§6.8).

- **Smallest usable cover wins.** RomM offers a small and a large cover. The bridge asks RomM for their sizes (`HEAD`) and downloads the smallest one tico can display.
- **WebP copies count too.** RomM's optional WebP conversion task saves `small.webp` beside `small.png` and keeps the original. Its API keeps listing the `.png` path, and RomM's web page swaps the extension itself (`frontend/src/utils/covers.ts` in 5.4.1). The bridge does the same: for each cover path ending in `.png`, `.jpg` or `.jpeg`, the `.webp` path is a candidate too. In the library in use the WebP copies are about 5 times smaller: 86 GBA and SNES covers came to 2.1 MiB as WebP against 10.2 MiB as PNG.
- **Format from the bytes, not the name.** PNG, JPEG, WebP, GIF and BMP are accepted. AVIF, which tico cannot show, and anything unrecognised are rejected, and the next cover is tried.
- **Downloaded only when they change.** RomM puts a timestamp in its cover paths (`small.png?ts=...`). If both paths are unchanged and the file still exists, the cover is reused. One exception: a game whose cover is not yet WebP gets a quick `HEAD` for WebP copies each sync, because the conversion task adds them without changing the paths. A WebP cover is left alone with no request at all.
- **A new cover gets a new address.** tico fetches each image once and sends no caching headers (§11), so the file name carries the first 8 hex characters of the image's SHA-256. The bridge serves covers with `Cache-Control: immutable`.
- **Failures are not fatal.** If RomM cannot be reached for a cover, the previous cover stays (or the game has none) and the next sync tries again. Files no game uses are deleted after each sync.

### 7.4 Examples

Cartridge ROM, delivered as-is (an arcade set kept zipped has the same shape):

```json
{
  "id": "romm-home-4821",
  "name": "Example Quest",
  "category": "game",
  "tags": ["gba"],
  "version": "9f2c41aa",
  "type": "zip",
  "extract": false,
  "url": "https://bridge.example/r/TOKEN/dl/romm-home-4821/9f2c41aa/Example%20Quest.gba",
  "filename": "Example Quest.gba",
  "install": "tico/roms/gba",
  "size": 8388608,
  "sha256": "<64 hex chars>",
  "icon": "https://bridge.example/r/TOKEN/img/4821.jpg"
}
```

*(As built, cover file names carry a fingerprint: `/r/<token>/img/4821-9f2c41aa.webp`, see "Covers" in §7.3.)*

Large disc image, also delivered as-is:

```json
{
  "id": "romm-home-7710",
  "name": "Example Racer",
  "category": "game",
  "tags": ["psx"],
  "version": "03be77d1",
  "type": "zip",
  "extract": false,
  "url": "https://bridge.example/r/TOKEN/dl/romm-home-7710/03be77d1/Example%20Racer.chd",
  "filename": "Example Racer.chd",
  "install": "tico/roms/psx",
  "size": 412745221,
  "sha256": "<64 hex chars>"
}
```

Multi-disc game as a bridge-built bundle (extracts flat into the console folder):

```json
{
  "id": "romm-home-9034",
  "name": "Example Saga",
  "category": "game",
  "tags": ["psx"],
  "version": "c81d0e5f",
  "type": "zip",
  "url": "https://bridge.example/r/TOKEN/dl/romm-home-9034/c81d0e5f/Example%20Saga.zip",
  "install": "tico/roms/psx",
  "size": 1432748032,
  "sha256": "<64 hex chars>"
}
```

