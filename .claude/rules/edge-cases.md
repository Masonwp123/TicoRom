---
paths:
  - "src/ticorom/packages.py"
  - "src/ticorom/sync.py"
  - "src/ticorom/library.py"
  - "src/ticorom/downloads.py"
  - "src/ticorom/covers.py"
  - "src/ticorom/index.py"
---

# How TicoRom handles awkward cases

Read this before changing how games are named, skipped or served. Each row states the handling that was chosen and, where it was tested, what was observed. Rows describing extract mode, bundles or an opt-in install mode describe plans: the code does not implement them yet.

> Section numbers (§N) and "Appendix A" are those of the original design document. `.claude/CLAUDE.md` lists which file holds each section. *Observed* means tested on a real Switch; *unverified* means not tested.

## 9. Other issues and how to handle them

| Issue | Handling |
|---|---|
| **`.7z` / `.rar` in the library** | Not a documented package archive. If the console's core loads that extension directly, deliver it in **file** mode. Otherwise skip and log. Don't re-compress on the fly. |
| **Zips with several ROMs or junk files inside** | `extract` dumps everything into the console folder. During the hash pass, inspect the zip listing; if it holds more than one loadable file, fall back to `keep` when the core reads zips, else skip and log. |
| **Multi-disc sets** | Bundle flat (no `extract_to_folder`) so the `.m3u` relative paths resolve and the user doesn't need Scan Subfolders. Check for name collisions across games on the same platform (`Track 01.bin`); on collision, deliver that game with `extract_to_folder` rather than overwrite. *Observed:* its files land in a subfolder and the game still appeared on the shelf; whether that depends on the Scan Subfolders setting was not checked. |
| **CHD variants** | *Observed (one game):* a PS2 `.chd` made with `chdman createdvd` installed but did not launch; the same game as a `createcd` CHD launched. The variant is readable from the CHD header's metadata tag without decompressing: `CHT2` for CD-type (verified), `DVD ` for DVD-type (verified), `CHGD` for GD-ROM. In the library every PS1 and PS2 image carries `CHT2` and every Dreamcast image `CHGD`. Read it during the hash pass; where the platform map limits `chd_types`, skip the others and list them in the skipped-ROM report. Converting them is a stretch goal (§12). |
| **File names** | The SD card is FAT32/exFAT. Strip `\ / : * ? " < > |`, trailing dots and spaces, and cap length. `filename` may not contain folders. URL-encode the path segment separately from the stored `filename`. A file inside a game's folder (`Game A/disc.chd`) is named after the folder (`Game A.chd`), because many games share inner file names. Every game installs into one console folder and FAT32/exFAT ignore case, so two games whose names match ignoring case would overwrite each other: the one with the lower RomM id keeps the name, the other gets its id added (`QUEST (4).7z`). |
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
| **Exposure** | Don't publish the bridge URL. Tokens, rate limits, and no directory listing. Paths are resolved from the DB by package id, never from user-supplied path strings, which rules out traversal. Paths from RomM are not trusted blindly either: an absolute path or one containing `..` is never read or served, and its game is skipped as `unsafe_path`. |
| **RomM down at startup** | `serve` starts anyway when RomM cannot be reached (a network error, or a 5xx from RomM or its proxy), for example while RomM is still starting after a host reboot. It serves the games already published, and the sync retries every 5 minutes. A rejected token or an unexpected answer still fails the startup checks, and `check` and `sync` treat an unreachable RomM as a failure. |

