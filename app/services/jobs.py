"""Запуск прогона в фоне из веб-интерфейса (кнопка «Запустить сейчас»)."""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from app.db import get_session_factory
from app.models import Run
from app.services.runner import RunAlreadyActive, blocking_run, run_all

log = logging.getLogger(__name__)
_tasks: set[asyncio.Task] = set()


def active_run_id() -> int | None:
    with get_session_factory()() as session:
        return session.scalar(select(Run.id).where(Run.status == "running").order_by(Run.id.desc()))


async def _run(trigger: str, only: list[str] | None) -> None:
    try:
        await run_all(get_session_factory(), trigger=trigger, only=only)
    except RunAlreadyActive as exc:
        log.info("Прогон не запущен: %s", exc)
        return
    except Exception:  # noqa: BLE001
        log.exception("Сбой прогона")
    await sync_portal()


async def sync_portal() -> None:
    """Обмен с сайтом менеджеров (если настроен) — в отдельном потоке, чтобы не держать веб-сервер."""
    from app.services import portal

    if not portal.configured():
        return
    try:
        await asyncio.to_thread(portal.sync, get_session_factory())
    except Exception:  # noqa: BLE001
        log.exception("Сбой обмена с сайтом менеджеров")


def launch_run(trigger: str = "manual", only: list[str] | None = None) -> bool:
    """Запускает прогон в текущем цикле событий. False — если прогон уже идёт."""
    if any(not t.done() for t in _tasks):
        return False
    with get_session_factory()() as session:
        if blocking_run(session) is not None:  # например, прогон из командной строки
            return False
    task = asyncio.get_running_loop().create_task(_run(trigger, only))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return True
