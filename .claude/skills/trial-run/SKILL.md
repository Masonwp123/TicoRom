---
name: trial-run
description: End-to-end check of TicoRom against the real RomM and library without touching the real config or cache. Syncs a few platforms into a temporary folder, serves on port 8099, downloads a game and checks its SHA-256 against the index. Use after changing the sync, index, covers or downloads, or before a release.
---

# Trial run against the real RomM

Run from the repository root:

```bash
.claude/skills/trial-run/trial.sh            # GBA only
.claude/skills/trial-run/trial.sh gba snes   # any platforms from the default map, by RomM folder name
```

What it does:

1. Creates a temporary `CONFIG_PATH` and `CACHE_PATH`, with a platform map holding only the chosen platforms, copied from `src/ticorom/startup/default_platforms.yaml`.
2. Runs `ticorom sync`. It uses `.env` for `APP_URL`, `ROMM_URL` and `ROMM_API_TOKEN`, without the script reading the file itself.
3. Creates a token, starts `ticorom serve` on port 8099, and fetches the combined index.
4. Downloads the largest game in the index and compares its SHA-256 with the index.
5. Checks that the token never appears in the access log.
6. Stops the server and deletes the temporary folder, also when a step fails.

Report what it printed: games synced, covers, the downloaded game, and whether each check passed.

Keep the platforms small. Cartridge consoles (`gba`, `snes`, `nes`, `gb`, `gbc`, `genesis`) hash in seconds. Disc consoles (`ps2`, `ngc`, `wii`) mean reading many GiB from the library, so ask the maintainer before using them.

This never touches `docker/config`, `docker/cache` or a running container. Port 8099 must be free.
