from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import auth
from .cache import Cache
from .config import get_settings
from .routers import dq as dq_router
from .routers import radar as radar_router
from .scheduler import RadarScheduler

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.scheduler.start()
    yield
    app.state.scheduler.shutdown()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="GROW Dashboard", lifespan=lifespan)

    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret)

    cache = Cache(settings.cache_db_full_path)
    scheduler = RadarScheduler(cache, settings)

    app.state.settings = settings
    app.state.cache = cache
    app.state.scheduler = scheduler

    app.include_router(auth.router)
    app.include_router(radar_router.router)
    app.include_router(dq_router.router)

    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    @app.get("/")
    def index(request: Request):
        if not request.session.get("user"):
            return RedirectResponse(url="/auth/login")
        return FileResponse(str(FRONTEND_DIR / "index.html"))

    return app


app = create_app()
