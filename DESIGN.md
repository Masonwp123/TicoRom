# RomM → tico Store Bridge: Design Document

**Status:** Draft v3 (both spike rounds complete) · **Date:** 2026-11-14 · **Targets:** tico 0.8.0 (repository `schema: 1`), RomM 5.x (the instance in use runs 5.4.1)

## 1. Summary

Build a small service ("the bridge") that reads a RomM library and publishes it as a tico Store repository, so games can be browsed and installed from tico on the Switch.

**Decisions**

| Question | Decision |
|---|---|
| Torrents (option 1) or direct links (option 2)? | **Option 2: direct HTTP downloads.** Torrents are deferred (§3). |
| Language / framework | **Python + FastAPI**, as proposed (§10). |
| How the bridge reads ROM bytes | **Read-only mount of the RomM library**; RomM's API is used for metadata only (§6.1). |
| Index generation | **Built in the background and cached**, never per request (§6.2). |
| How a single-file game is delivered | **`type: zip` + `extract: false` + `filename`**, for any size and extension. `type: rom` is not used (§7.1). |
| Transport | **HTTPS only**, behind a reverse proxy with a publicly trusted certificate (§6.4). |
| Files of 4 GiB or more | **Not listed by default.** They cannot be written to a FAT32 card, and tico only finds out after downloading 4 GiB (§9). |
| Hash check at download time | **No re-hash per request.** A `stat` comparison and versioned download URLs catch changed files instead (§6.6). |

Two rounds of spikes were run on a Switch, on 2026-11-11 and 2026-11-13/14. Every delivery mode in this design has now installed a game on real hardware. §11 records the results, what did not work, and the questions still open. The main open one is download speed: about 1.2 MB/s on the Switch.

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
| `icon`, `banner`, `screenshots` | Must be `https://`. Max 8 screenshots. |
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

## 3. Why direct links instead of torrents

Option 1 looks simpler ("let tico sort it out") but moves the work to the server side and adds moving parts:

- **A torrent has to exist and be seeded.** The bridge would need to hash every ROM into a `.torrent`, run a seeder (and realistically a private tracker, since DHT discovery of a home server is unreliable), and keep both in sync with the library.
- **tico only takes a `magnet` link**, so the Switch must first fetch metadata from a peer before anything downloads. With a single seeder behind NAT this is the least reliable step.
- **Debrid services won't help.** TorBox / Real-Debrid / AllDebrid can't reach a private swarm, so only tico's built-in engine or tico-fetch would work.
- **Torrents are off by default for community repositories.** Every user must flip a setting before any package appears at all.
- **It doesn't remove the mapping problem.** tico still needs `install`, `file`, and extraction flags per package, so the platform map is needed either way.
- **It exposes the library to a swarm**, which is a worse security posture than a tokenized HTTP link.

Option 2 needs none of that: RomM already has the files, and the bridge only has to describe them and serve bytes with `Range` support.

Torrents remain a reasonable *later* addition for one case: very large disc images on flaky Wi-Fi. The package model in §7 leaves room for a `magnet` variant.

## 4. Goals and non-goals

**Goals**

- Publish a RomM library as one or more tico repositories.
- Correct per-platform placement and extraction on the Switch.
- Integrity checking (`sha256`) and resumable downloads.
- Keep RomM credentials off the Switch and off the wire.

**Non-goals (v1)**

- Torrent delivery.
- Save / state sync back to RomM.
- Format conversion (e.g. bin/cue → CHD). The bridge delivers what the library holds. Converting with `chdman` is a stretch goal (§12), prompted by the CHD finding in §9.
- BIOS / firmware delivery (possible later via RomM's firmware endpoints).
- Public hosting. This is for a private instance on a LAN, VPN, or tunnel.

## 5. Architecture

```
            metadata (REST)                    index.json, covers, files
  RomM  <------------------  Bridge (FastAPI)  <--------------------------  tico (Switch)
   |                            |   |
   |  library volume (ro) ------+   +--- SQLite: hashes, package cache
   +--- /romm/library                +--- cache dir: built index, zips
```

Components:

- **RomM client** — async `httpx` wrapper; lists platforms and ROMs.
- **Syncer** — background job; diffs RomM against the local DB, hashes new files, rebuilds indexes.
- **Mapper** — applies the platform map (§8) and decides the delivery mode per ROM.
- **Index builder** — writes `index.json` and `index.json.gz` per repository to the cache dir.
- **Download endpoint** — streams files from the read-only mount with `Range` support.
- **Store** — SQLite (one file, no extra container); schema in §6.8.

## 6. Key design points

### 6.1 Read files from disk, not through RomM

The bridge mounts the RomM library read-only and serves bytes itself. RomM's API supplies metadata only.

Reasons:

- **Auth.** tico can't send credentials. Pointing tico at RomM directly would mean setting `DISABLE_DOWNLOAD_ENDPOINT_AUTH=true` on RomM, which opens every download to anyone who can reach it.
- **Hashing.** tico wants SHA-256. RomM 5.4.1 has CRC32 / MD5 / SHA-1 for only some files and no SHA-256 (§6.7), so the bridge has to read the bytes anyway.
- **Range and speed.** One fewer hop, and no dependency on how RomM streams multi-file ROMs.

Fallback when a mount isn't possible (RomM on another host): proxy `GET /api/roms/{id}/content/{file_name}` with a service account and forward `Range`. Slower, and SHA-256 has to be computed by downloading each file once.

### 6.2 Build the index in the background

A sync job (interval, plus a manual `POST /admin/sync`) does the work:

1. `GET /api/platforms`, then page through `GET /api/roms` for each platform that has a map entry (`platform_ids`, `with_files=true`, `limit`, `offset`; the index extras `with_char_index`, `with_filter_values` and `with_rom_id_index` switched off). Excluded platforms are never requested.
2. Replace the RomM mirror tables with what RomM returned (§6.8), in one transaction.
3. For new or changed files (size or mtime differs from `file_facts`), compute SHA-256 from the mount. Hashing runs in a thread pool; the first sync of a large library will take a while and should report progress.
4. Run the mapper to rebuild `packages`, then write `index.json` + `index.json.gz` atomically (write temp, rename).

Requests for the index are then a static file read plus one string substitution for the token (§6.8). tico never waits on RomM.

### 6.3 Authentication without headers

Put a capability token in the URL path:

```
https://bridge.example/r/<token>/index.json
https://bridge.example/r/<token>/snes/index.json
https://bridge.example/r/<token>/dl/<package-id>/<version>/<filename>
```

- Tokens are random (≥128 bits), stored hashed, revocable, and optionally scoped to platforms. They are managed with the `ticorom token` command (§6.8).
- The bridge holds the only RomM credential: a client API token with the scopes `roms.read` and `platforms.read` (plus `collections.read` if the collection filter is used), sent as `Authorization: Bearer rmm_…`. Issue it from a dedicated Viewer user, since a token can never hold more than its owner does.
- Tokens end up in tico's config and in proxy access logs. Treat them like passwords; strip the path from logs if the proxy allows.
- Rate-limit by token and reject unknown tokens with 404, not 401.

*Observed (spike B):* a 32-character path token works for the index and for downloads. URL credentials sent as HTTP Basic would also work, but path tokens stay because they need no header support anywhere in the chain.

### 6.4 HTTPS

**Serve everything over HTTPS**, behind a TLS reverse proxy or tunnel. The guide allows `http://` downloads, but in testing tico only connected over HTTPS (the cause was not isolated, §11), and images must be `https://` in any case.

- Use a publicly trusted certificate. The spike used a Let's Encrypt wildcard. The Switch's homebrew TLS stack won't trust a private CA without extra work.
- The hostname must resolve *on the Switch*. Modded consoles often use 90DNS or other manual DNS servers, which never see a name that exists only on the router. Use a public DNS record, or add the name to Atmosphère's hosts file.
- Build every absolute URL, including redirect targets, from `APP_URL`. Behind a proxy the `Host` header may be the upstream address.
- Appendix A has proxy configurations for Nginx Proxy Manager, plain nginx, and Apache, tuned for ROM downloads.

### 6.5 Large libraries

There's no pagination, but spike C showed that a 5,000-package index (3 MB, 86 KiB gzipped) adds, browses, and searches quickly. So:

- **Publish a combined repository and one per platform** (`/r/<token>/<tico-slug>/index.json`). Users add whichever they prefer. the per-platform repositories are the fallback.
- Advertise the `.json.gz` address. tico accepts it directly (*observed*) and sends no `Accept-Encoding`, so a plain `.json` always travels uncompressed.
- Keep packages lean: one `icon`, no `screenshots` by default, truncate `description` (configurable).
- Optional filter by RomM collection so a curated subset can be published.

### 6.6 Stale files and hashes at download time

tico checks `sha256` after every download (*observed*), so a corrupt or changed file can never be installed. What that check cannot prevent is a wasted download: if a library file changes after the bridge hashed it, tico fetches the whole file and only then refuses it. At the speed measured in §11, that can cost an hour or more.

The bridge guards against this without hashing on every request:

- **Compare `stat` at request time.** Before sending any bytes, compare the file's size and mtime with the values recorded when it was hashed. On a mismatch, answer 404 and queue that ROM for re-hashing and an index rebuild. This costs one `stat` call.
- **Put the version in the download URL** (`/dl/<package-id>/<version>/<filename>`). tico resumes with `Range` but sends no `If-Range` (*observed*), so it cannot tell that a file changed between attempts. With a versioned URL, a partial download of the old file can only resume against an address that now answers 404.
- **Do not hash the file on each request.** Hashing reads the whole file before the first byte goes out: seconds on a fast local disk, minutes for a multi-GB file on slower storage. tico also makes a new request for every resume and retry, so the cost would repeat.

A periodic background re-hash of the whole library would additionally catch silent disk corruption. It is optional and can come later.

### 6.7 What RomM provides (checked against 5.4.1)

`tools/romm_probe.py` pulled every record from the instance in use on 2026-11-15 (20 platforms, 789 ROMs) and compared it with the mounted library. The token needed only `roms.read` and `platforms.read`.

| Need | RomM field | Finding |
|---|---|---|
| Where the file is | `files[].full_path`, relative to the library root, e.g. `roms/gba/Game.7z` | All 116,720 file entries resolved to a file under `LIBRARY_PATH`. |
| Size and modification time | `files[].file_size_bytes`, `files[].last_modified` | Out of date for 53 files that were replaced on disk after RomM's last scan. |
| Single file or set | `has_simple_single_file`, `has_nested_single_file`, `has_multiple_files`; per file `is_top_level`, `category` | Decides file mode or bundle. |
| What an archive holds | `files[].archive_members` | Present for 350 archives, so the mapper can inspect a `.zip` / `.7z` without opening it. |
| Hashes | `crc_hash`, `md5_hash`, `sha1_hash`, `chd_sha1_hash` | Only on some files (636 of them); no SHA-256. |
| Text | `name`, `summary` | Present on all but one ROM. |
| Cover | `path_cover_small`, `path_cover_large`, e.g. `/assets/romm/resources/roms/1/2/cover/small.png` | Present on all but one ROM. RomM serves these **without authentication**. |
| Variants | `sibling_roms`, `regions`, `revision`, `tags` | Not usable to find regional copies. On the published platforms, all 24 sibling groups are either the discs of one game or different games sharing a metadata entry (*HeartGold* / *SoulSilver*). `regions` mixes file-name tags with release regions from metadata providers: 236 files with no region in their name still list one. |
| Paging | `GET /api/roms` returns `items` and `total` | 100 per page worked. |

Consequences:

- **RomM says what exists and where; the disk says size, mtime, and hash.** The bridge never copies a size from RomM into the index.
- **Covers need no credential.** The bridge can fetch them from RomM with no extra scope. They still go through the bridge (§7.3), so the Switch needs one hostname and RomM itself stays off the console's network.
- **For this library, file mode delivers every game on a platform tico supports.** The cartridge platforms hold `.7z` / `.zip` archives that the cores open directly, and the disc platforms hold single `.chd`, `.rvz`, `.zcci`, or `.wad` files. No game needs extract or bundle mode, which is why the MVP (§12) ships file mode alone.
- **Only request platforms that are published.** One excluded platform accounts for 115,000 of the file entries.
- **Every file is published as its own package.** There is no reliable way to tell from RomM which files are the same game in different regions, so the bridge does not try (§9).

### 6.8 Database

SQLite, one file at `CONFIG_PATH/ticorom.db`. It lives with the configuration, not in the cache folder: hashes take an hour or more to recompute, and tokens cannot be recreated.

- **Access:** Python's built-in `sqlite3` with plain SQL, run off the event loop with `asyncio.to_thread`. No ORM; the schema is small.
- **Concurrency:** WAL journal mode, so index requests and downloads keep reading while a sync writes.
- **Migrations:** the schema version is kept in `PRAGMA user_version`. Numbered migrations run at startup, each in a transaction.

The tables fall into four groups, kept apart because they change at different rates and cost different amounts to rebuild:

| Group | Tables | Comes from | Rebuilt |
|---|---|---|---|
| RomM mirror | `platforms`, `roms`, `rom_files` | RomM's API | Every sync. Cheap. |
| Disk facts | `file_facts` | The library | Only for files whose size or mtime changed. Expensive. |
| Packages | `packages` | The tables above, `platforms.yaml`, and settings | After every sync. |
| Bookkeeping | `sync_runs`, `tokens` | The bridge | As things happen. |

```sql
-- RomM mirror. Only published platforms are fetched (§6.2).
CREATE TABLE platforms (
  id              INTEGER PRIMARY KEY,    -- RomM platform id
  slug            TEXT NOT NULL,
  fs_slug         TEXT NOT NULL,
  name            TEXT NOT NULL
);

CREATE TABLE roms (
  id              INTEGER PRIMARY KEY,    -- RomM ROM id; part of the package id (§7.3)
  platform_id     INTEGER NOT NULL REFERENCES platforms(id) ON DELETE CASCADE,
  name            TEXT NOT NULL,
  fs_name         TEXT NOT NULL,          -- file name with tags, e.g. "Game (USA) (Rev 1).7z"
  summary         TEXT,
  genres          TEXT NOT NULL DEFAULT '[]',   -- JSON list
  release_date    INTEGER,                -- unix seconds
  cover_path      TEXT,                   -- RomM /assets path, for the cover proxy
  romm_updated_at TEXT NOT NULL
);

CREATE TABLE rom_files (
  id              INTEGER PRIMARY KEY,    -- RomM file id
  rom_id          INTEGER NOT NULL REFERENCES roms(id) ON DELETE CASCADE,
  path            TEXT NOT NULL UNIQUE,   -- relative to LIBRARY_PATH
  file_name       TEXT NOT NULL,
  is_top_level    INTEGER NOT NULL,
  category        TEXT,
  archive_members TEXT                    -- JSON; for extract mode later
);

-- Disk facts. Keyed by path, not by RomM ids, so hashes survive a RomM rescan.
CREATE TABLE file_facts (
  path            TEXT PRIMARY KEY,       -- relative to LIBRARY_PATH
  size            INTEGER NOT NULL,
  mtime_ns        INTEGER NOT NULL,
  sha256          TEXT,                   -- NULL until hashed
  chd_type        TEXT,                   -- cd | gd | dvd; NULL if not a CHD
  hashed_at       INTEGER
);

-- What gets published. Rebuilt after every sync.
CREATE TABLE packages (
  id              TEXT PRIMARY KEY,       -- romm-<instance slug>-<rom id>
  rom_id          INTEGER NOT NULL REFERENCES roms(id) ON DELETE CASCADE,
  path            TEXT NOT NULL,          -- file served; size and hash come from file_facts
  tico_slug       TEXT NOT NULL,
  install         TEXT NOT NULL,
  filename        TEXT NOT NULL,          -- sanitized for FAT32 / exFAT (§9)
  mode            TEXT NOT NULL,          -- file; later extract, bundle (§7.1)
  version         TEXT,                   -- first 8 hex of sha256; part of the download URL
  status          TEXT NOT NULL,          -- published | pending | skipped
  skip_reason     TEXT                    -- over_4gib, extension, chd_type, missing, ...
);
CREATE INDEX packages_by_console ON packages(tico_slug, status);

-- Bookkeeping
CREATE TABLE sync_runs (
  id              INTEGER PRIMARY KEY,
  started_at      INTEGER NOT NULL,
  finished_at     INTEGER,
  status          TEXT NOT NULL,          -- running | ok | failed
  stats           TEXT,                   -- JSON counts
  error           TEXT
);

CREATE TABLE tokens (
  id              INTEGER PRIMARY KEY,
  name            TEXT NOT NULL UNIQUE,   -- e.g. "switch"
  token_sha256    TEXT NOT NULL UNIQUE,   -- the token itself is never stored
  platforms       TEXT,                   -- JSON list of tico slugs; NULL = all
  created_at      INTEGER NOT NULL,
  revoked_at      INTEGER,
  last_used_at    INTEGER
);
```

Rules:

- **A sync is all or nothing.** The mirror tables and `packages` are replaced in one transaction at the end of a successful sync, so games removed from RomM disappear then, and a failed sync leaves the last good state in place. Hashing writes `file_facts` as it goes, so a crash mid-hash does not lose finished work.
- **`file_facts` rows are dropped only when the file is gone from disk.** Excluding a platform for a while does not throw its hashes away.
- **A package is `pending` until its file is hashed**, because its version comes from the hash. On the first sync, games appear in tico gradually as hashing works through the library. `skipped` rows carry a reason and make up the skipped-ROM report.
- **Downloads** look up a package by id and version, then compare the live `stat` of its path with `file_facts` before sending a byte (§6.6).
- **Tokens** are shown once, when created, and only their SHA-256 is stored. A plain hash is enough because tokens are 128-bit random values, not passwords. Because the bridge never knows a token after creating it, the index cannot be built per token: it is built once with a placeholder where the token goes, and the placeholder is filled from the request URL when the index is served. Managed from the command line:

  ```
  ticorom token add <name> [--platforms gba,snes]   # prints the token once
  ticorom token list
  ticorom token revoke <name>
  ```

## 7. Package mapping

### 7.1 Delivery modes

The mapper picks one mode per ROM:

| Mode | When | tico package |
|---|---|---|
| **file** | Single file of any size with an extension the tico core loads. Includes a library `.zip` that must stay whole (arcade sets, or `zip_policy: keep`) | `type: zip`, `extract: false`, `install`, `filename`, `size`, `sha256` |
| **extract** | Library file is a `.zip` containing a ROM the platform wants unpacked | `type: zip`, `install` (default `extract: true`) |
| **bundle** | Multi-file game (`.cue` + `.bin`, `.m3u` + discs) | Bridge-built zip, `type: zip`, `install` |
| **skip** | Extension not accepted for the platform | Not listed; reported in the sync log |

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
| `icon` | Cover image, proxied through the bridge at `/r/<token>/img/<rom id>.jpg` so it is HTTPS and needs no RomM login. |
| `url` | Bridge download URL. |
| `size`, `sha256` | From the hash cache; for bundles, of the built zip. |
| `install` | From the platform map, e.g. `tico/roms/snes`. |
| `filename` | Sanitized library file name (§9, "File names"). |

**The instance slug (`INSTANCE_SLUG`).** tico requires a package id to be unique across every repository a user has added, and drops duplicates (§2). RomM's ROM ids are small numbers that every RomM instance reuses, so `romm-4821` from one bridge would collide with `romm-4821` from another. The instance slug is a short label for *this* RomM that goes into every id, as `romm-<slug>-<rom id>`, e.g. `romm-home-4821`.

- `home` is a fine value for someone running one RomM. Two bridges that a user might add to the same console need different slugs.
- Allowed characters are those of a package id: lowercase letters, digits, `.`, `_`, `-`. Keep it short; the whole id is capped at 64 characters. The bridge validates this at startup.
- **Treat it as permanent.** tico tracks installed games and updates by id. Changing the slug changes every id, so tico would show every game as a new, uninstalled package.

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

## 9. Other issues and how to handle them

| Issue | Handling |
|---|---|
| **`.7z` / `.rar` in the library** | Not a documented package archive. If the console's core loads that extension directly, deliver it in **file** mode. Otherwise skip and log. Don't re-compress on the fly. |
| **Zips with several ROMs or junk files inside** | `extract` dumps everything into the console folder. During the hash pass, inspect the zip listing; if it holds more than one loadable file, fall back to `keep` when the core reads zips, else skip and log. |
| **Multi-disc sets** | Bundle flat (no `extract_to_folder`) so the `.m3u` relative paths resolve and the user doesn't need Scan Subfolders. Check for name collisions across games on the same platform (`Track 01.bin`); on collision, deliver that game with `extract_to_folder` rather than overwrite. *Observed:* its files land in a subfolder and the game still appeared on the shelf; whether that depends on the Scan Subfolders setting was not checked. |
| **CHD variants** | *Observed (one game):* a PS2 `.chd` made with `chdman createdvd` installed but did not launch; the same game as a `createcd` CHD launched. The variant is readable from the CHD header's metadata tag without decompressing: `CHT2` for CD-type (verified), `DVD ` for DVD-type (verified), `CHGD` for GD-ROM. In the library every PS1 and PS2 image carries `CHT2` and every Dreamcast image `CHGD`. Read it during the hash pass; where the platform map limits `chd_types`, skip the others and list them in the skipped-ROM report. Converting them is a stretch goal (§12). |
| **File names** | The SD card is FAT32/exFAT. Strip `\ / : * ? " < > |`, trailing dots and spaces, and cap length. `filename` may not contain folders. URL-encode the path segment separately from the stored `filename`. |
| **4 GiB limit on FAT32** | Files ≥ 4 GiB can't be written on FAT32 cards, and tico does not check first. *Observed:* a 6.8 GiB download ran for 55 minutes, failed when the file on the card reached 4 GiB, and retries failed the same way. Skip these files by default and list them in the skipped-ROM report. A config flag re-enables them for exFAT cards (untested). |
| **Download speed** | *Observed:* about 1.2 MB/s (10 Mbit/s) on the Switch for multi-GB files, so a 2 GiB game takes about half an hour. The server and proxy are not the limit (§11). Not yet investigated; §11 lists the experiments, and Appendix A adds a proxy log that shows where the limit is. |
| **Install prompts** | A community repository prompts on every install. Document the Settings > Store toggle in the README. A zip *without* `install`, laid out as `tico/roms/<console>/…`, is allowed to write under `tico/roms/` and may avoid the prompt, but it breaks for users with a custom ROM path. Leave as an opt-in mode after testing. |
| **Duplicate ids across repositories** | Per-platform and combined repositories list the same ids on purpose; that is harmless. The `<instance>` segment prevents clashes with other people's repositories. |
| **Regions, revisions, hacks** | Every file is its own package, with the tags from its file name in `tagline`. A "preferred region" option was considered and dropped: RomM's sibling groups are not regional copies, and its `regions` field includes release regions that the file itself may not be (§6.7). Choosing between copies would mean parsing file names and guessing, and a wrong guess hides a game. |
| **Separate discs** | When RomM lists each disc as its own game ("Disc 1", "Disc 2"; 22 files in the library in use), each disc is its own package. A tico package delivers one download, so the user installs each disc. |
| **Deleted ROMs** | Dropped from the index on the next sync. tico won't remove installed copies; that's acceptable. |
| **RomM API drift** | Query parameter names and response shapes have changed between RomM releases. Isolate all calls in one client module, pin a tested RomM version range, and validate responses with Pydantic so a mismatch fails loudly at sync time. |
| **Hash cost** | Cache by (path, size, mtime). Hash with a bounded thread pool so the bridge doesn't starve RomM of disk I/O. Never hash at request time (§6.6). |
| **Range correctness** | Recent Starlette `FileResponse` handles `Range`; add an integration test for `206`, suffix ranges, `416`, and offsets beyond 4 GiB. Alternatively hand off to the reverse proxy with `X-Accel-Redirect` / `X-Sendfile`. *Observed:* after an interruption tico reports a connection error; starting the install again continues with `Range: bytes=N-` from the exact byte it stopped at. |
| **Proxy limits** | Some tunnels and CDNs cap response size or frown on bulk binary transfer. For multi-GB files prefer LAN or VPN access. *Observed:* the reverse proxy buffered about 1 GB ahead of the Switch and then passed data through at the Switch's pace; multi-GB downloads completed through it. Appendix A turns the disk spooling off. |
| **Exposure** | Don't publish the bridge URL. Tokens, rate limits, and no directory listing. Paths are resolved from the DB by package id, never from user-supplied path strings, which rules out traversal. |

## 10. Technology choice

**FastAPI is a good fit; keep it.**

- Async `httpx` for RomM, Pydantic models for both schemas, built-in OpenAPI for the admin endpoints.
- RomM's own backend is FastAPI, so the code and models would port almost directly if this is ever proposed upstream as a native feed.

Stack: Python 3.12+, FastAPI + Uvicorn, `httpx`, Pydantic v2 / `pydantic-settings`, SQLite through the standard library's `sqlite3` (§6.8), PyYAML, `pytest`, built with uv. Ship as a Docker image meant to sit in the same compose file as RomM.

Alternatives considered:

| Alternative | Verdict |
|---|---|
| **Static generator** (cron script writes `index.json`; Caddy/nginx serves it and the library) | Simplest and gives `Range` for free. Loses tokens, cover proxying, and bundles. Good fallback if the service feels like too much; the syncer and mapper are the same code either way. |
| **Go or Rust single binary** | Better raw file throughput and a smaller image. Not worth it here: the bottleneck is on the Switch's side, not the server (measured, §11). |
| **Native RomM feed** (like its Tinfoil / pkgi feeds) | The best long-term home, and worth proposing once the format is proven. It inherits RomM's download-auth limitation, though, and ties releases to RomM's schedule. |

## 11. Spike results

### Round 1 (spikes A–C)

Run on 2026-11-11. All payloads were dummy data (random bytes).

| Spike | Question | Result | Consequence |
|---|---|---|---|
| **A** | Does `type: zip` + `extract: false` + `filename` save a non-zip file intact? | **Yes.** A 4 MiB and a 128 MiB non-zip file installed under their `.chd` filenames. | Single files need no repacking, whatever their size. Bundles are only for multi-file games (§7.2). |
| **B** | Does tico accept credentials or long tokens in the URL, and follow redirects? | **Yes.** A 32-character path token worked for the index and for downloads. 302 redirects were followed for both. `user:pass@` credentials arrived as HTTP Basic. | Path tokens work as designed (§6.3). |
| **C** | How does tico behave with ~5,000 packages in one index? | **Fine.** The repository added quickly, tico stayed responsive, and search was fast. | A combined repository is viable (§6.5). |

Also confirmed:

- **`sha256` is enforced with `extract: false`.** A package with a deliberately wrong hash was refused ("could not be verified"). The successful installs above were therefore hash-checked.
- **Console tags.** All 23 of tico's ROM folder names produced a shelf (§8).
- **`.json.gz` index** is accepted.
- **A genuine zip with `extract: false`** was saved whole, from a percent-encoded URL containing spaces and parentheses.

What did not work:

- **`type: rom`.** A `rom` package with a `.sfc` filename never appeared in the listing. Three `rom` packages with `.gba` filenames were listed and downloaded completely (the server sent every byte), but tico then reported that the file could not be written to the SD card. A zip package installed into the same folder, so the card and folder were fine. Round 2 repeated this with a real ROM and got the same error, so the dummy payload was not the cause. The cause remains unknown and is not pursued, because the file mode covers the same case.
- **Plain `http://`.** tico only connected over HTTPS. No plain-HTTP request from the Switch reached the test server, so whether tico refused the address or the network dropped it was not determined.

### Round 2 (spikes D–H)

Run on 2026-11-13 and 14 with real games: a GBA ROM, an SNES game as a `.7z`, and two PS2 `.chd` images (about 2 GiB and 6.8 GiB). The bundle and resume-cut payloads were dummy data.

| Spike | Question | Result | Consequence |
|---|---|---|---|
| **D** | Does tico continue an interrupted download with `Range`? | **Yes.** The server dropped a download at 16 of 64 MiB; tico reported a connection problem, and starting the install again sent `Range: bytes=16777216-` and completed. A 2 GiB download interrupted by Wi-Fi loss and by closing tico also continued from the byte it had reached. | `Range` support is required and is enough. tico sends no `If-Range`, hence the versioned URLs in §6.6. |
| **E** | Do real games delivered in file mode launch? | **Yes, with one catch.** The `.gba` and the `.7z` installed and played. The PS2 `.chd` installed, but launched only after it was rebuilt as a CD-type CHD. | File mode is confirmed for cartridge ROMs, archives the core opens, and disc images. Check the CHD variant per platform (§9). |
| **F** | Does a zip with no `extract` flag unpack its ROM into `install`? | **Yes.** The unpacked ROM installed and played. | Extract mode works as documented. |
| **G** | Does a bridge-built stored zip unpack flat? What does `extract_to_folder` do? | **Yes.** The `.cue` and both `.bin` files appeared in the console folder. With `extract_to_folder` they landed in a subfolder, and tico still listed the game. | Bundle flat by default; `extract_to_folder` is the fallback for name collisions (§9). |
| **H** | Size limits. | **A real limit at 4 GiB.** The 2 GiB images installed. The 6.8 GiB image downloaded for 55 minutes and failed when the file on the card reached 4 GiB, the FAT32 limit. The 25,000-package index was not run. | Skip files ≥ 4 GiB by default (§9). |

Also observed in round 2:

- **`type: rom` fails with a real ROM too** (Spike 13), with the same SD-card error as round 1.
- **`size` is not enforced for `type: zip`.** After a test file was replaced, the index still listed the old size (about 10% smaller) and the package installed. The hash is what tico checks.
- **`sha256` in uppercase hex** is accepted.
- **Retries.** After the 4 GiB failure tico retried five times within five seconds, each time with `Range` from the same offset.

**Download speed.** The Switch received multi-GB files at 1.1–1.3 MB/s (about 10 Mbit/s), measured from the server log over three long transfers. The same proxy delivered 20 MB/s to a PC on the same Wi-Fi, and the server fed the proxy at 25–60 MiB/s. The limit is therefore on the Switch's side: its Wi-Fi link, tico's HTTP/TLS client, or writing to the card. Which one is not known.

Experiments, cheapest first:

1. **Check the Switch's Wi-Fi band and signal** in the router's client list. A 2.4 GHz link would explain most of it.
2. **Apply the proxy settings and log from Appendix A**, run one large download, and read the round-trip time and window from the log (A.7). This separates the Wi-Fi link, packet loss, and the console itself.
3. **Send a large file to the Switch by another route**, such as an FTP homebrew. That measures Wi-Fi plus card writes without tico's HTTP/TLS client.
4. **Try wired networking** with the console docked and a USB LAN adapter.
5. If the other route is fast and tico is slow, the limit is in tico's client and can't be fixed from the bridge. Report it upstream. A small default socket buffer is a candidate (Appendix A.1, *unverified*).

Still open after both rounds:

- **Download speed**, above.
- **Files ≥ 4 GiB on an exFAT card.**
- **A real multi-file game.** The bundle test used dummy `.cue` / `.bin` files, so it checked the layout on the card, not a launch.
- **Bundles over 4 GiB**, which need Zip64.
- **Whether `extract_to_folder` games depend on the Scan Subfolders setting**, and whether extract mode leaves the zip behind.
- **Libraries beyond 5,000 games.**
- **Plain `http://`** (round 1).

## 12. Milestones

1. ~~Spikes A–H~~ Done (§11). Remaining before the MVP: a filled-in platform map for the consoles actually in the library. The download-speed experiments (§11) can run alongside.
2. **MVP:** RomM client, database (§6.8), syncer, hash cache, `ticorom token` command, file mode (which covers the whole library in use, §6.7), combined and per-platform indexes, tokenized and versioned download endpoint with `Range` and the `stat` check (§6.6), skip rules (files ≥ 4 GiB, CHD variants), HTTPS deployment behind a reverse proxy.
3. **Extract mode and bundles** for multi-file games, with `extract_to_folder` on name collisions; cover proxy; `.json.gz`.
4. **Admin surface:** sync status, skipped-ROM report, token management (CLI is enough).
5. **Later:** virtual stored zips for bundles, collection-based repositories, firmware/BIOS packages, optional `magnet` variants for large files, background re-hash of the library.
6. **Stretch:** convert with `chdman` into the cache, e.g. DVD-type CHD → CD-type for PS2, or bin/cue → CHD. This doubles storage for converted games and needs the same cache as bundles.

## 13. Configuration sketch

```env
APP_URL=https://bridge.example      # public address; must be https; every generated URL starts with it
ROMM_URL=https://romm.example
ROMM_API_TOKEN=rmm_...              # client API token; scopes roms.read, platforms.read
INSTANCE_SLUG=home                  # part of every package id (§7.3); do not change it later
SYNC_INTERVAL_MINUTES=60
LIBRARY_PATH=/data/library          # read-only mount; the folder that contains roms/
CONFIG_PATH=/data/config            # platforms.yaml and the SQLite database
CACHE_PATH=/data/cache              # built indexes and bundle zips; safe to delete
CACHE_MAX_GB=50
SKIP_FILES_OVER_4GIB=true           # FAT32 cards; set false only for exFAT
PUID=1000                           # user and group the bridge runs as in the container;
PGID=1000                           # must read the library and write config and cache
APP_PORT=8080                       # port on the host that the reverse proxy forwards to
```

**File permissions (`PUID` / `PGID`).** The container starts as root, creates the config and cache folders if they are missing, gives them to `PUID:PGID`, then drops to that user before the bridge starts. The library is only ever read.

**Paths.** `LIBRARY_PATH`, `CONFIG_PATH`, and `CACHE_PATH` are folders on the host. `docker-compose.yml` mounts them into the container at fixed places (`/data/library` read-only, `/data/config`, `/data/cache`), and the bridge inside always uses those. Run outside Docker, the bridge reads the three variables directly. The bridge treats all three as plain directories and assumes nothing about the storage behind them; the only requirement is that `PUID:PGID` can read the library and write the other two.

## Appendix A. Reverse proxy configuration for Switch downloads

Settings for the TLS proxy in front of the bridge (§6.4), chosen for ROM download speed. Variants: Nginx Proxy Manager (NPM), plain nginx, and Apache 2.4.

**What to expect.** The proxy was not the bottleneck in the spikes: with NPM's stock settings it delivered 20 MB/s to a PC while the Switch received 1.2 MB/s (§11). These settings remove the proxy-side costs that were observed, and add a log that shows where the real limit is. Only the congestion-control change in A.6 has a realistic chance of raising the Switch's speed, and only if the log points at packet loss.

**Test status.** The nginx directives were checked with a config parser for names, arguments, and context. Nothing here has been run against the Switch yet, and the Apache configuration has not been syntax-checked.

### A.1 What is known about the client

Measured in the spikes:

- One connection per download; no parallel range requests.
- `Range` on resume, no `If-Range`, no `Accept-Encoding`.
- 1.1–1.3 MB/s for multi-GB files.

Inferred, because tico is closed source. These hold if tico uses devkitPro's stock curl, which its request headers are consistent with:

- **TLS is done by the console's own SSL service**, not by a library inside tico. It supports TLS 1.3 on system version 14.0.0 and later, and falls back to TLS 1.2.
- **HTTP/1.1 only.** That curl build has no HTTP/2 support.
- **A 64 KiB TCP receive buffer** by default, growing to at most 256 KiB.

The last point suggests a hypothesis: a download limited by a 64 KiB window runs at about window ÷ round-trip time, and 64 KiB at 50 ms is 1.3 MB/s, the speed that was measured. A 50 ms round trip on a LAN would mean a poor Wi-Fi link or Wi-Fi power saving. The log in A.3 records the round-trip time, so it can confirm or rule this out.

### A.2 The settings and why

| Setting | Reason | Expected effect on speed |
|---|---|---|
| No spooling to disk (`proxy_max_temp_file_size 0`) | By default nginx reads ahead up to 1 GB per download into a temp file. On a router that is wasted flash writes and I/O. | None directly; removes disk load. |
| 2 MiB of in-memory read-ahead (`proxy_buffers 32 64k`) | Keeps the proxy fed without the disk, and reads the bridge in large chunks. | Small. |
| Full-size TLS records (`ssl_buffer_size 16k`, the default) | Fewer records for the console to decrypt per megabyte. Guides that lower it are tuning page-load time, not throughput. | Small; do not lower it. |
| HTTP/1.1 | The client most likely speaks nothing else. | None. |
| No compression, no rate limit | ROMs don't compress, and tico doesn't ask for it. | None; avoids wasted CPU. |
| Client's cipher order (`ssl_prefer_server_ciphers off`) | Whether AES-GCM or ChaCha20 is faster on the console is unknown; its own SSL service is the better judge. | Unknown; the log records what was chosen. |
| Long send timeout (`send_timeout 300s`) | A brief Wi-Fi stall should not end a transfer. tico can resume, but the user has to restart the install. | Fewer interrupted downloads. |
| Log without the token, with TLS and TCP details | The token is a password (§6.3). The TCP fields show whether the link, packet loss, or the console is the limit. | Diagnostic. |
| BBR congestion control on the proxy host (A.6) | Keeps its sending rate up on a lossy Wi-Fi link, where the default backs off. | Possibly large, if loss is the cause. |

### A.3 Nginx Proxy Manager

In the proxy host's **Advanced** tab, paste:

```nginx
# TicoRom: ROM downloads to a Switch
proxy_buffering          on;
proxy_buffer_size        16k;
proxy_buffers            32 64k;     # 2 MiB read-ahead per download, in RAM
proxy_busy_buffers_size  128k;
proxy_max_temp_file_size 0;          # never spool a download to disk
proxy_read_timeout       300s;
send_timeout             300s;
access_log               off;        # the URL contains the access token
```

On the other tabs:

- **Cache Assets: off.** It would cache cover images at the proxy for 30 minutes under their tokenized URLs, so they would outlive a revoked token.
- **HTTP/2 Support:** either setting; the Switch is not expected to use it.
- **Do not add** `ssl_protocols`, `ssl_ciphers`, `ssl_prefer_server_ciphers`, or `ssl_session_cache` to the Advanced tab. NPM already sets them in the same server block for Let's Encrypt certificates, nginx rejects the duplicate, and the host goes offline. NPM's values already match this appendix.

**Optional: the diagnostic log.** This needs file access to NPM's data folder. Create `/data/nginx/custom/http_top.conf` inside the container (the `data/nginx/custom/` folder of the NPM volume):

```nginx
map $request_uri $ticorom_uri {
    "~^/r/[^/]+(?<ticorom_rest>/.*)$"  "/r/-$ticorom_rest";
    default                            $request_uri;
}
log_format ticorom '$time_iso8601 $remote_addr $status "$request_method $ticorom_uri" '
                   'range="$http_range" sent=$body_bytes_sent time=$request_time '
                   'tls=$ssl_protocol/$ssl_cipher rtt=$tcpinfo_rtt rttvar=$tcpinfo_rttvar '
                   'cwnd=$tcpinfo_snd_cwnd rcv_space=$tcpinfo_rcv_space';
```

Restart NPM, then replace the `access_log off;` line in the Advanced tab with this block. Defining `location /` here makes NPM leave out its own, which is intended:

```nginx
location / {
    access_log /data/logs/ticorom_access.log ticorom;
    include conf.d/include/proxy.conf;
}
```

Do it in that order. If the Advanced tab names a log format that does not exist yet, the host goes offline. If the host uses an NPM Access List, note that it is not applied to a hand-written `location /`.

### A.4 Plain nginx

```nginx
# http context
map $request_uri $ticorom_uri {
    "~^/r/[^/]+(?<ticorom_rest>/.*)$"  "/r/-$ticorom_rest";
    default                            $request_uri;
}
log_format ticorom '$time_iso8601 $remote_addr $status "$request_method $ticorom_uri" '
                   'range="$http_range" sent=$body_bytes_sent time=$request_time '
                   'tls=$ssl_protocol/$ssl_cipher rtt=$tcpinfo_rtt rttvar=$tcpinfo_rttvar '
                   'cwnd=$tcpinfo_snd_cwnd rcv_space=$tcpinfo_rcv_space';

upstream ticorom_bridge {
    server 127.0.0.1:8000;
    keepalive 8;
}

server {
    listen 443 ssl;
    server_name bridge.example;

    ssl_certificate     /etc/letsencrypt/live/bridge.example/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/bridge.example/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers off;
    ssl_session_cache   shared:ticorom:10m;
    ssl_session_timeout 1d;
    ssl_buffer_size     16k;             # the default; do not lower it

    access_log /var/log/nginx/ticorom_access.log ticorom;

    location / {
        proxy_pass         http://ticorom_bridge;   # no URI part: the path is passed through untouched
        proxy_http_version 1.1;
        proxy_set_header   Connection        "";
        proxy_set_header   Host              $host;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Real-IP         $remote_addr;

        proxy_buffering          on;
        proxy_buffer_size        16k;
        proxy_buffers            32 64k;
        proxy_busy_buffers_size  128k;
        proxy_max_temp_file_size 0;
        proxy_read_timeout       300s;
        send_timeout             300s;
    }
}
```

### A.5 Apache 2.4

Modules: `ssl`, `proxy`, `proxy_http`, `headers`, `setenvif`. Apache streams proxied responses and never spools them to disk, so there is no equivalent of the temp-file setting. It cannot log the TCP fields.

```apache
<VirtualHost *:443>
    ServerName bridge.example
    Protocols http/1.1

    SSLEngine on
    SSLCertificateFile    /etc/letsencrypt/live/bridge.example/fullchain.pem
    SSLCertificateKeyFile /etc/letsencrypt/live/bridge.example/privkey.pem
    SSLProtocol           -all +TLSv1.2 +TLSv1.3
    SSLHonorCipherOrder   off
    SSLCompression        off

    ProxyRequests     Off
    ProxyPreserveHost On
    ProxyPass         / http://127.0.0.1:8000/ timeout=300
    ProxyPassReverse  / http://127.0.0.1:8000/
    RequestHeader     set X-Forwarded-Proto "https"
    ProxyIOBufferSize 65536
    TimeOut           300

    # ROMs do not compress
    SetEnv no-gzip 1

    # Log without the access token
    SetEnvIf Request_URI "^(.*)$"            ticorom_path=$1
    SetEnvIf Request_URI "^/r/[^/]+(/.*)$"   ticorom_path=/r/-$1
    LogFormat "%{%Y-%m-%dT%H:%M:%S%z}t %a %>s \"%m %{ticorom_path}e\" range=\"%{Range}i\" sent=%B time_us=%D tls=%{SSL_PROTOCOL}x/%{SSL_CIPHER}x" ticorom
    CustomLog ${APACHE_LOG_DIR}/ticorom_access.log ticorom
</VirtualHost>
```

### A.6 Proxy host kernel settings (Linux)

Use BBR for the connections the proxy sends on. Needs Linux 4.13 or later and the `tcp_bbr` module (`modprobe tcp_bbr`).

On the host, in `/etc/sysctl.d/90-ticorom.conf`:

```
net.ipv4.tcp_congestion_control = bbr
net.ipv4.tcp_slow_start_after_idle = 0
```

If the proxy runs in Docker (NPM does), set them on the container instead, since each container has its own copy of these settings. With host networking, use the host file above.

```yaml
services:
  npm:
    sysctls:
      net.ipv4.tcp_congestion_control: bbr
      net.ipv4.tcp_slow_start_after_idle: 0
```

Treat this as an experiment: measure one large download before and after. BBR helps when the limit is packet loss on the Wi-Fi link, and does nothing when the console itself is the limit.

### A.7 Reading the log

After one large download, look at its line in `ticorom_access.log`. `sent ÷ time` is the speed in bytes per second, `rtt` is the round-trip time in microseconds, and `cwnd` is how many packets the proxy was allowed to have in flight. The thresholds below are rules of thumb.

| What the line shows | Likely limit | What to try |
|---|---|---|
| `rtt` above about 20000 (20 ms) on a LAN | The Wi-Fi link: band, signal, or power saving. | A.8. |
| Low `rtt`, small `cwnd` (under about 20) | Packet loss. | BBR (A.6), then the Wi-Fi link. |
| Low `rtt`, large `cwnd`, still slow | The console is reading slowly: tico's client or the card. | Nothing on the proxy. Report it to tico. |

`tls=` shows the protocol and cipher the console chose, which settles the TLS inferences in A.1.

### A.8 On the Switch

Not proxy settings, but they bear on the same number:

- **Use the 5 GHz band**, close to the access point.
- **Set the MTU to 1500** (System Settings > Internet > Internet Settings > the network > Change Settings > MTU). The Switch defaults to 1400. Users report faster downloads after the change; it was not measured here.
- **Dock with a USB LAN adapter** to take Wi-Fi out of the picture entirely.

## References

- Creating a Store Repository (tico forum): https://forum.ticoverse.com/topic/150/creating-a-store-repository
- tico 0.8.0 release notes: https://github.com/ticohq/tico/releases/tag/0.8.0
- tico Supported ROM Formats: https://ticoverse.com/wiki/library-and-roms/supported-rom-formats
- RomM API reference: https://docs.romm.app/4.5.0/API-and-Development/API-Reference/
- RomM feed clients (download-endpoint auth): https://docs.romm.app/3.2.0/ecosystem/feed-clients/
- Switch SSL service (TLS versions by system version): https://switchbrew.org/wiki/SSL_services
- libnx default socket buffer sizes: https://github.com/switchbrew/libnx/blob/master/nx/source/runtime/devices/socket.c
- devkitPro's curl build for the Switch (TLS backend, no HTTP/2): https://github.com/devkitPro/pacman-packages/blob/master/switch/curl/PKGBUILD
- Nginx Proxy Manager server-block template: https://github.com/NginxProxyManager/nginx-proxy-manager/blob/develop/backend/templates/proxy_host.conf
- nginx proxy module (buffering, temp files): https://nginx.org/en/docs/http/ngx_http_proxy_module.html
- Apache mod_proxy: https://httpd.apache.org/docs/2.4/mod/mod_proxy.html
