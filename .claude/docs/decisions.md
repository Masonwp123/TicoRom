# Why TicoRom looks the way it does

Rejected alternatives, goals and non-goals, the technology choice, milestones and references. Read this before proposing a large change, so an option that was already weighed is not proposed again without new information.

> Section numbers (§N) and "Appendix A" are those of the original design document. `.claude/CLAUDE.md` lists which file holds each section. *Observed* means tested on a real Switch; *unverified* means not tested.

## Document history

These notes began as a single design document (`DESIGN.md` in the repository root). Its header read:

> # RomM → tico Store Bridge: Design Document
>
> **Status:** Draft v3 (both spike rounds complete) · **Date:** 2026-11-14 · **Targets:** tico 0.8.0 (repository `schema: 1`), RomM 5.x (the instance in use runs 5.4.1)
>

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

## 12. Milestones

1. ~~Spikes A–H~~ Done (§11). Remaining before the MVP: a filled-in platform map for the consoles actually in the library. The download-speed experiments (§11) can run alongside.
2. **MVP** (built; download endpoint awaiting a test on hardware): *(Downloads, including a resumed one, were tested on hardware on 2026-11-20; the project is at version 1.0.0.)* RomM client, database (§6.8), syncer, hash cache, `ticorom token` command, file mode (which covers the whole library in use, §6.7), combined and per-platform indexes, tokenized and versioned download endpoint with `Range` and the `stat` check (§6.6), skip rules (files ≥ 4 GiB, CHD variants), HTTPS deployment behind a reverse proxy.
3. **Extract mode and bundles** for multi-file games, with `extract_to_folder` on name collisions. (Covers and `.json.gz` are done.)
4. **Admin surface:** sync status, skipped-ROM report, token management (CLI is enough).
5. **Later:** virtual stored zips for bundles, collection-based repositories, firmware/BIOS packages, optional `magnet` variants for large files, background re-hash of the library.
6. **Stretch:** `banner` and `screenshots` from RomM's large cover and screenshots (tico shows them on the package page; §11, cover test). Convert with `chdman` into the cache, e.g. DVD-type CHD → CD-type for PS2, or bin/cue → CHD. This doubles storage for converted games and needs the same cache as bundles.

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
