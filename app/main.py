import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api.routes import router
from app.config import Settings, get_settings
from app.database import Base, make_engine, make_session_factory
from app.paths import sqlite_file
from app.services.jobs import JobRunner

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
logger = logging.getLogger("studio")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not app.state.settings.jobs_inline:
        app.state.runner.start()
        logger.info("后台任务线程已启动")
    yield
    app.state.runner.stop()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings.data_path.mkdir(parents=True, exist_ok=True)
    database_file = sqlite_file(settings.database_url)
    if database_file is not None:
        database_file.parent.mkdir(parents=True, exist_ok=True)
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    session_factory = make_session_factory(engine)

    app = FastAPI(title="章映", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.SessionLocal = session_factory
    app.state.runner = JobRunner(session_factory, settings)
    app.include_router(router)

    @app.get("/")
    def index():
        return FileResponse(FRONTEND / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=FRONTEND), name="static")
    return app


app = create_app()
