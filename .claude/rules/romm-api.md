---
paths:
  - "src/ticorom/romm.py"
  - "src/ticorom/sync.py"
  - "src/ticorom/covers.py"
  - "src/ticorom/startup/preflight.py"
  - "tests/test_romm.py"
  - "tests/test_sync.py"
  - "tests/test_covers.py"
  - "tests/test_preflight.py"
---

# How TicoRom reads RomM

Read this before changing anything that calls RomM or interprets its answers. Checked against RomM 5.4.1.

How to apply:

- Every RomM call goes through `romm.py`. Responses are validated with Pydantic models that ignore unknown fields, so a changed RomM fails loudly at sync time instead of publishing nonsense.
- The bridge's RomM token needs only `roms.read` and `platforms.read`. Cover images under `/assets` need no token.
- Request only published platforms. Page with `limit`/`offset` and stop on a short page; `total` is trusted only when RomM sends it.
- Do not trust from RomM: sizes (they go stale), `regions` and sibling groups (not regional copies), `is_top_level` (also true for a file directly inside a game's folder; see `models/rom.py` in RomM), and paths (an absolute path or `..` is never read).
- RomM's WebP conversion writes `small.webp` beside `small.png` but its API keeps listing the `.png`; the bridge tries the `.webp` path too.
- `RomMError.unreachable` separates "RomM did not answer" (network error or 5xx, may fix itself) from a rejected token or an unexpected answer.

> Section numbers (§N) and "Appendix A" are those of the original design document. `.claude/CLAUDE.md` lists which file holds each section. *Observed* means tested on a real Switch; *unverified* means not tested.

### 6.7 What RomM provides (checked against 5.4.1)

The token needed only `roms.read` and `platforms.read`.

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

