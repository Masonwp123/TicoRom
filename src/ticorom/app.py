"""The web application: the repository files tico reads, plus the background sync."""

from __future__ import annotations

import asyncio
import logging
import re
import threading
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse

from ticorom import __version__, covers, tokens
from ticorom.db import Database
from ticorom.index import IndexFiles
from ticorom.startup.platforms import PlatformMap
from ticorom.startup.settings import Settings
from ticorom.sync import Syncer, sync_forever

TOKEN_IN_PATH = re.compile(r"^/r/[^/]+")


# Replace the token in access-log lines: logs are often kept and shared, tokens act as passwords.
class HideTokens(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Uvicorn's access records carry (client, method, path, http version, status)
        if isinstance(record.args, tuple) and len(record.args) == 5:
            args = list(record.args)
            args[2] = TOKEN_IN_PATH.sub("/r/<token>", str(args[2]))
            record.args = tuple(args)
        return True


# Create the FastAPI app
def create_app(settings: Settings, platform_map: PlatformMap, database: Database,
               syncer: Syncer | None = None) -> FastAPI:
    index_files = IndexFiles(settings)

    # Run the sync in the background for as long as the app runs
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        access_log = logging.getLogger("uvicorn.access")
        if not any(isinstance(f, HideTokens) for f in access_log.filters):
            access_log.addFilter(HideTokens())

        stop = threading.Event()
        task = asyncio.create_task(sync_forever(syncer, settings.sync_interval_minutes, stop)) if syncer else None
        yield
        stop.set()  # also stops hashing that is still running in worker threads
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title="TicoRom", version=__version__, docs_url=None, redoc_url=None, openapi_url=None,
                  lifespan=lifespan)
    app.state.settings = settings
    app.state.platform_map = platform_map

    @app.get("/v1/health")
    async def health() -> dict:
        return {"status": "ok", "version": __version__}

    # The repository index for one token: every console it may see, or one console.
    # Unknown or revoked tokens get 404, not 401, so the address gives nothing away.
    async def index_response(token: str, console: str | None, gz: bool) -> Response:
        found = await database.run(lambda conn: tokens.authenticate(conn, token))
        if found is None:
            raise HTTPException(status_code=404)
        body = await asyncio.to_thread(index_files.render, token, found.platforms, console, gz)
        if body is None:
            raise HTTPException(status_code=404)
        return Response(body, media_type="application/gzip" if gz else "application/json",
                        headers={"Cache-Control": "no-store"})

    @app.get("/r/{token}/index.json")
    async def all_consoles(token: str) -> Response:
        return await index_response(token, None, gz=False)

    @app.get("/r/{token}/index.json.gz")
    async def all_consoles_gz(token: str) -> Response:
        return await index_response(token, None, gz=True)

    @app.get("/r/{token}/{console}/index.json")
    async def one_console(token: str, console: str) -> Response:
        return await index_response(token, console, gz=False)

    @app.get("/r/{token}/{console}/index.json.gz")
    async def one_console_gz(token: str, console: str) -> Response:
        return await index_response(token, console, gz=True)

    # A game's cover. The file name holds a fingerprint of the image, so a given
    # address never changes content and can be cached for good.
    @app.get("/r/{token}/img/{name}")
    async def cover(token: str, name: str) -> FileResponse:
        if not covers.FILE_NAME_RE.fullmatch(name):
            raise HTTPException(status_code=404)
        if await database.run(lambda conn: tokens.authenticate(conn, token)) is None:
            raise HTTPException(status_code=404)
        path = settings.cache_path / covers.COVERS_DIR / name
        if not path.is_file():
            raise HTTPException(status_code=404)
        return FileResponse(path, media_type=covers.CONTENT_TYPES[name.rsplit(".", 1)[1]],
                            headers={"Cache-Control": "public, max-age=31536000, immutable"})

    return app
