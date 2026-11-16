"""The web application. For now it only reports that the bridge is up."""

from __future__ import annotations

from fastapi import FastAPI

from ticorom import __version__
from ticorom.startup.platforms import PlatformMap
from ticorom.startup.settings import Settings

# Create the FastAPI app
def create_app(settings: Settings, platform_map: PlatformMap) -> FastAPI:
    app = FastAPI(title="TicoRom", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.platform_map = platform_map

    @app.get("/v1/health")
    async def health() -> dict:
        return {"status": "ok", "version": __version__}

    return app
