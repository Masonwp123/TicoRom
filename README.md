# TicoRom

Publishes a [RomM](https://romm.app) library as a [tico](https://github.com/ticohq/tico) Store repository, so games can be browsed and installed from tico on a Nintendo Switch.

**Status: working.** TicoRom syncs with RomM, publishes every game whose console tico supports, with covers, and serves resumable downloads; installing from tico has been tested on a Switch. The design, and the hardware tests behind it, are in [DESIGN.md](DESIGN.md).

## Run it with Docker

You need Docker with Compose, a RomM instance, and the folder RomM uses as its library.

1. Put `APP_URL`, `ROMM_URL` and `ROMM_API_TOKEN` in a `.env` file next to `docker-compose.yml` (see `.env.example`), set the rest in the compose file, and change the library volume (`/data/library:/data/library:ro`) so its left side is the folder RomM uses as its library:

   | Setting | Meaning |
   |---|---|
   | `APP_URL` | The public `https://` address the Switch will use. Every link TicoRom generates starts with it. |
   | `ROMM_URL` | Address of RomM. |
   | `ROMM_API_TOKEN` | A RomM client API token with the scopes `roms.read` and `platforms.read`. |
   | `INSTANCE_SLUG` | A short label for this RomM, part of every package id. Do not change it once games are installed. |
   | `PUID`, `PGID` | User and group TicoRom runs as. They must be able to read the library and write the other two folders. |

2. Start it:

   ```bash
   docker compose up -d --build
   docker compose logs -f
   ```

   The log begins with the startup checks. If one says `FAIL`, the container stops; fix the setting and start it again.

3. Point your HTTPS reverse proxy at `http://<this machine>:8080`. tico only connects over HTTPS. DESIGN.md Appendix A has proxy settings.

4. Create an access token and add the repository in tico:

   ```bash
   docker compose run --rm ticorom token add switch
   ```

   This prints the token once, with the address to enter in tico (Store > Sources > Add repository): `https://<your address>/r/<token>/index.json.gz` for every console, or `.../r/<token>/<console>/index.json.gz` for one. Keep the token private; anyone with it can see and download the games. Games appear as the first sync hashes them.

Tokens:

```bash
docker compose run --rm ticorom token add <name> [--platforms gba,snes]   # create; --platforms limits what it can see
docker compose run --rm ticorom token list                               # every token and when it was last used
docker compose run --rm ticorom token revoke <name>                      # stop a token from working
```

Other commands:

```bash
docker compose run --rm ticorom check   # run only the startup checks
docker compose run --rm ticorom sync    # sync with RomM once, in the foreground (serve also syncs on its own)
docker compose down                     # stop
```

## Choosing what is published

On first start TicoRom writes `platforms.yaml` into the config folder. That file is the only thing that decides which RomM platforms are published:

- an entry under `platforms` publishes that platform;
- a name in `exclude` leaves it out;
- a platform in neither is left out, with a warning in the startup checks.

Edit the file and restart to change it. The comments at its top explain each field.

## Development

Uses [uv](https://docs.astral.sh/uv/). Dependency versions are pinned in `uv.lock`.

```bash
uv sync                          # create .venv with the app and the dev tools
uv run pytest                    # tests; they never touch the real RomM or library
uv run ticorom check             # startup checks, using .env in the current folder
uv run ticorom sync              # one sync with RomM; the database is CONFIG_PATH/ticorom.db
uv add <package>                 # add a dependency (uv add --dev for a dev tool)
```
