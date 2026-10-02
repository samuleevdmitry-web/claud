from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi import Depends, FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import PROJECT_ROOT, get_settings
from app.db import get_engine, session_scope
from app.services.keywords import bootstrap_keywords
from app.web.auth import require_auth
from app.web.routes_import import router as import_router
from app.web.routes_keywords import router as keywords_router

log = logging.getLogger(__name__)


def run_migrations() -> None:
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    cfg.attributes["configure_logger"] = False
    with get_engine().begin() as connection:
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, "head")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not get_settings().app_password:
        log.warning("APP_PASSWORD не задан — приложение доступно без пароля")
    run_migrations()
    with session_scope() as session:
        bootstrap_keywords(session)
    yield


app = FastAPI(title="Velmorium — мониторинг тендеров", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "web" / "static"), name="static")
app.include_router(keywords_router)
app.include_router(import_router)


@app.get("/", dependencies=[Depends(require_auth)])
def index():
    return RedirectResponse("/keywords")


@app.get("/health")
def health():
    return {"status": "ok"}
