"""FastAPI application factory."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.configs.api import ApiConfig
from app.core.orm.session_factory import dispose_engine
from app.middlewares.manager import install_middlewares
from app.transport.rest.health import router as health_router
from app.transport.rest.v1_routers import rest_v1_routers


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    yield
    await dispose_engine()


def create_app(config: ApiConfig | None = None) -> FastAPI:
    api_config = config or ApiConfig()
    app = FastAPI(
        title=api_config.title,
        version=api_config.version,
        docs_url=api_config.docs_url,
        openapi_url=api_config.openapi_url,
        lifespan=lifespan,
        openapi_tags=[
            {
                "name": "payments",
                "description": "Создание платежей и получение информации о них",
            },
        ],
    )
    install_middlewares(app)
    for router in rest_v1_routers:
        app.include_router(router, prefix="/api/v1")
    app.include_router(health_router)
    return app
