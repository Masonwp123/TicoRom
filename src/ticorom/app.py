"""The web application. For now it reports that the bridge is up and runs the sync in the background."""

from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from ticorom import __version__
from ticorom.startup.platforms import PlatformMap
from ticorom.startup.settings import Settings
from ticorom.sync import Syncer, sync_forever


# Create the FastAPI app
def create_app(settings: Settings, platform_map: PlatformMap, syncer: Syncer | None = None) -> FastAPI:

    # Run the sync in the background for as long as the app runs
    @asynccontextmanager
    async def lifespan(app: FastAPI):
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

    return app
