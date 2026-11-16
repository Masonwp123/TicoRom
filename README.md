# TicoRom

Publishes a [RomM](https://romm.app) library as a [tico](https://github.com/ticohq/tico) Store repository, so games can be browsed and installed from tico on a Nintendo Switch.

**Status: skeleton.** The container starts, checks its configuration against RomM and the library, and answers a health check. It does not publish games yet. The design, and the hardware tests behind it, are in [DESIGN.md](DESIGN.md).

## Run it with Docker

You need Docker with Compose, a RomM instance, and the folder RomM uses as its library.

1. Fill in the environment variables in the compose file:

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

Other commands:

```bash
docker compose run --rm ticorom check   # run only the startup checks
docker compose down                     # stop
```

## Choosing what is published

On first start TicoRom writes `platforms.yaml` into the config folder. That file is the only thing that decides which RomM platforms are published:

- an entry under `platforms` publishes that platform;
- a name in `exclude` leaves it out;
- a platform in neither is left out, with a warning in the startup checks.

Edit the file and restart to change it. The comments at its top explain each field.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest                 # tests; they never touch the real RomM or library
.venv/bin/ticorom check          # startup checks, using .env in the current folder
```
