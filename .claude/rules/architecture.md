---
paths:
  - "src/ticorom/**/*.py"
  - "tests/**/*.py"
---

# How TicoRom works: architecture, sync, downloads, database

Read this before changing how TicoRom syncs, stores, publishes or serves anything.

How to apply:

- RomM says what exists and where; the disk says size, modification time and SHA-256. Never copy a size or hash from RomM into the index.
- Never hash a file while answering a request. A download compares the file's live `stat` with `file_facts`; on a mismatch it answers 404 and wakes the background sync (§6.6).
- Keep a sync all or nothing: the RomM mirror, `covers` and `packages` are replaced in one transaction. Only hashing writes as it goes, so finished hashes survive a crash.
- Keep `file_facts` keyed by path, never by RomM ids, so hashes survive a RomM rescan.
- Store only a token's SHA-256. A token is printed once, when created, and never logged. Answer every refusal with a plain 404.
- Build every absolute URL from `APP_URL`. Index files carry `@@TICOROM_TOKEN@@`, filled in from the request URL when served.
- Change the schema only by adding a numbered migration to `MIGRATIONS` in `db.py`. Never edit one that has been released.
- Keep disk and SQLite work off the event loop (`asyncio.to_thread`, `Database.run`).

> Section numbers (§N) and "Appendix A" are those of the original design document. `.claude/CLAUDE.md` lists which file holds each section. *Observed* means tested on a real Switch; *unverified* means not tested.

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

## 5. Architecture

```
            metadata (REST)                    index.json, covers, files
  RomM  <------------------  Bridge (FastAPI)  <--------------------------  tico (Switch)
   |                            |   |
   |  library volume (ro) ------+   +--- SQLite: hashes, package cache
   +--- /romm/library                +--- cache dir: built index, zips
```

*(As built, the library is mounted in the container at `/data/library`, the config at `/data/config` and the cache at `/data/cache`.)*

Components:

- **RomM client** — async `httpx` wrapper; lists platforms and ROMs.
- **Syncer** — background job; diffs RomM against the local DB, hashes new files, rebuilds indexes.
- **Mapper** — applies the platform map (§8) and decides the delivery mode per ROM.
- **Index builder** — writes `index.json` and `index.json.gz` per repository to the cache dir. *(As built: `_all.json` and `<tico slug>.json` in `CACHE_PATH/index/`; the `.json.gz` form is compressed per request, see §6.2.)*
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

A sync job (interval, plus a manual `POST /admin/sync`) does the work: *(As built there is no HTTP endpoint. A sync runs at startup, every `SYNC_INTERVAL_MINUTES`, 5 minutes after a failure, at once when a download finds a changed file, and by hand with `ticorom sync`.)*

1. `GET /api/platforms`, then page through `GET /api/roms` for each platform that has a map entry (`platform_ids`, `with_files=true`, `limit`, `offset`; the index extras `with_char_index`, `with_filter_values` and `with_rom_id_index` switched off). Excluded platforms are never requested.
2. Replace the RomM mirror tables with what RomM returned (§6.8), in one transaction.
3. For new or changed files (size or mtime differs from `file_facts`), compute SHA-256 from the mount. Hashing runs in a thread pool; the first sync of a large library will take a while and should report progress.
4. Run the mapper to rebuild `packages`, then write the index files atomically (write temp, rename) to `CACHE_PATH/index/`: `_all.json` for every console and `<tico slug>.json` per console. This repeats every 30 seconds while hashing, so games appear as they are hashed. The `.json.gz` variants are compressed on request, because the token is only filled in then (§6.8), and the result is kept in a small memory cache.

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
- Rate-limit by token and reject unknown tokens with 404, not 401. *(As built: the 404 is implemented; rate limiting and the collection filter are not.)*

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
- Keep packages lean: one `icon`, no `screenshots` by default, truncate `description` (configurable). *(As built: the limit is fixed at 600 characters, `DESCRIPTION_LIMIT` in `index.py`.)*
- Optional filter by RomM collection so a curated subset can be published. *(Not implemented.)*

### 6.6 Stale files and hashes at download time

tico checks `sha256` after every download (*observed*), so a corrupt or changed file can never be installed. What that check cannot prevent is a wasted download: if a library file changes after the bridge hashed it, tico fetches the whole file and only then refuses it. At the speed measured in §11, that can cost an hour or more.

The bridge guards against this without hashing on every request:

- **Compare `stat` at request time.** Before sending any bytes, compare the file's size and mtime with the values recorded when it was hashed. On a mismatch, answer 404 and wake the background sync so it starts at once instead of at the next interval; the sync sees the changed size or mtime, re-hashes the file and rebuilds the index. This costs one `stat` call. The same 404 answers an address whose version or file name is no longer current, and a token not allowed to see that console.
- **Put the version in the download URL** (`/dl/<package-id>/<version>/<filename>`). tico resumes with `Range` but sends no `If-Range` (*observed*), so it cannot tell that a file changed between attempts. With a versioned URL, a partial download of the old file can only resume against an address that now answers 404.
- **Do not hash the file on each request.** Hashing reads the whole file before the first byte goes out: seconds on a fast local disk, minutes for a multi-GB file on slower storage. tico also makes a new request for every resume and retry, so the cost would repeat.

A periodic background re-hash of the whole library would additionally catch silent disk corruption. It is optional and can come later.

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
  cover_path      TEXT,                   -- RomM /assets path of the small cover
  cover_large_path TEXT,                  -- ... and of the large one (migration 2)
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

-- Migration 2. No foreign key to roms: roms is replaced on every sync, and a
-- cascade would throw every cover away each time.
CREATE TABLE covers (
  rom_id          INTEGER PRIMARY KEY,
  source          TEXT NOT NULL,          -- RomM's cover paths when chosen; a change means fetch again
  file            TEXT,                   -- file in CACHE_PATH/covers; NULL if RomM has no usable cover
  fetched_at      INTEGER NOT NULL
);
```

Rules:

- **A sync is all or nothing.** The mirror tables and `packages` are replaced in one transaction at the end of a successful sync, so games removed from RomM disappear then, and a failed sync leaves the last good state in place. Hashing writes `file_facts` as it goes, so a crash mid-hash does not lose finished work.
- **`file_facts` rows are dropped only when RomM no longer lists the file and it is gone from disk.** Excluding a platform for a while does not throw its hashes away. A file RomM lists but that is missing right now keeps its hash and its game is skipped as `missing`; if the file comes back unchanged, it is not hashed again.
- **An unavailable library changes nothing.** If none of the files RomM lists can be found, the library is most likely not mounted. The sync is refused and recorded as failed, and the last good state, index included, stays as it was.
- **One sync at a time.** A sync holds an exclusive lock on `CONFIG_PATH/sync.lock`, also across processes: `ticorom sync` next to a running container is refused with a message rather than duplicating the work.
- **Failed syncs are retried after 5 minutes**, not after a full `SYNC_INTERVAL_MINUTES`.
- **A package is `pending` until its file is hashed**, because its version comes from the hash. On the first sync, games appear in tico gradually as hashing works through the library. `skipped` rows carry a reason and make up the skipped-ROM report.
- **Downloads** look up a package by id and version, then compare the live `stat` of its path with `file_facts` before sending a byte (§6.6).
- **Tokens** are shown once, when created, and only their SHA-256 is stored. A plain hash is enough because tokens are 128-bit random values, not passwords. Because the bridge never knows a token after creating it, the index cannot be built per token: it is built once with a placeholder where the token goes, and the placeholder is filled from the request URL when the index is served. Managed from the command line:

  ```
  ticorom token add <name> [--platforms gba,snes]   # prints the token once
  ticorom token list
  ticorom token revoke <name>
  ```

