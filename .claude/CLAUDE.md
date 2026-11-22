# TicoRom

TicoRom ("the bridge") publishes a [RomM](https://romm.app) library as a [tico](https://github.com/ticohq/tico) Store repository, so games can be browsed and installed from tico on a Nintendo Switch. It syncs game data from RomM's API, reads and hashes the files from a read-only mount of RomM's library, and serves `index.json` files, cover images and resumable downloads behind tokenized URLs.

Version 1.0.0. Targets tico 0.8.0 (repository `schema: 1`) and RomM 5.x (tested with 5.4.1).

## Layout

```
src/ticorom/
  startup/start.py      command line: check, sync, serve, token add|list|revoke
  startup/settings.py   settings from environment variables or .env (pydantic-settings)
  startup/platforms.py  the platform map (platforms.yaml) and its default, default_platforms.yaml
  startup/preflight.py  startup checks
  romm.py               the only module that calls RomM
  sync.py               the sync: fetch, look at disk, save in one transaction, hash, publish
  library.py            disk facts: stat, SHA-256, CHD type, path safety
  packages.py           what gets published, skipped or waits for a hash; file names
  covers.py             cover images: choose, download, store
  index.py              builds and serves the index files
  tokens.py             access tokens
  downloads.py          checks behind the download route
  db.py                 SQLite schema and numbered migrations
  app.py                FastAPI app: routes and the background sync loop
tests/                  pytest; RomM is faked with httpx.MockTransport
entrypoint.sh, Dockerfile, docker-compose.yml, .github/workflows/docker-publish.yml
```

## Commands

```bash
uv sync                                   # .venv with the app and dev tools
uv run pytest                             # all tests; they never touch the real RomM or library
uv run ticorom check | sync | serve       # uses .env in the current folder
uv run ticorom token add <name> [--platforms gba,snes] | token list | token revoke <name>
docker compose up -d --build              # build and run the container
docker compose run --rm ticorom <command> # any command inside the container (goes through the entrypoint)
```

## How it works, in short

- A sync runs at startup, every `SYNC_INTERVAL_MINUTES`, 5 minutes after a failure, and at once when a download finds a changed file. Only one sync runs at a time (`CONFIG_PATH/sync.lock`).
- Steps: fetch published platforms, games and covers from RomM; stat the files on disk; save everything in one transaction; hash pending files smallest first, republishing every 30 seconds.
- `platforms.yaml` alone decides what is published. Each game becomes one package: published, pending (waiting for its hash), or skipped with a reason.
- tico reads `/r/<token>/index.json(.gz)` or `/r/<token>/<console>/index.json(.gz)`, covers at `/r/<token>/img/<file>`, and downloads at `/r/<token>/dl/<id>/<version>/<filename>`. Every refusal is a plain 404.

## Where the design lives

The former `DESIGN.md` is split by topic. The files in `rules/` load automatically when matching files are touched; read the ones in `docs/` when a question is about history or past decisions. Section numbers are the original ones:

| Sections | File |
|---|---|
| §1 Summary, §5 Architecture, §6.1–6.6, §6.8 Database | `.claude/rules/architecture.md` |
| §2 What tico expects, §7 Package mapping | `.claude/rules/tico-repository-format.md` |
| §6.7 What RomM provides | `.claude/rules/romm-api.md` |
| §8 Platform map | `.claude/rules/platform-map.md` |
| §9 Other issues and how to handle them | `.claude/rules/edge-cases.md` |
| §13 Configuration, Appendix A Reverse proxy | `.claude/rules/deployment.md` |
| §3 Torrents, §4 Goals, §10 Technology, §12 Milestones, References | `.claude/docs/decisions.md` |
| §11 Hardware test results | `.claude/docs/hardware-tests.md` |

When behaviour changes, update the matching file in the same change, so these notes keep describing the code.

## Working rules

- **Assume nothing about storage.** The library, config and cache are plain directories. No NFS-specific (or any filesystem-specific) behaviour or wording.
- **No console lists in code.** What is published is decided only by `platforms.yaml`.
- **Docker first.** Permissions come from `PUID`/`PGID` via `entrypoint.sh`. Container paths are fixed: `/data/library` (read-only), `/data/config`, `/data/cache`.
- **Code style.** Explain a function or class with a `#` comment line above it, not a docstring. Comments say why, in plain words. `sync.py` is laid out as its numbered steps; keep it that way and keep it simple.
- **Secrets.** Never print, log or commit the RomM token or an access token. `.env` holds the RomM token and is not to be read.
- **Tests.** Every change comes with tests. Tests never use the network, the real RomM or the real library: fake RomM with `httpx.MockTransport` and build libraries in `tmp_path`.
- **Trying things against the real RomM.** Use a temporary `CONFIG_PATH` and `CACHE_PATH` (`mktemp -d`) and port 8099, never `docker/config` or `docker/cache`, and delete them afterwards (see the `trial-run` skill). Don't start a full first sync of the whole library without asking: it reads hundreds of GiB.
- **Bugs.** Reproduce a suspected bug before reporting or fixing it, and say how it was reproduced.
- **Git.** The maintainer commits. Don't commit or push unless asked.
- **Hardware.** Anything about tico's behaviour that `docs/hardware-tests.md` does not cover is unverified until tested on a Switch by the maintainer.
