"""Запуск прогона в фоне из веб-интерфейса (кнопка «Запустить сейчас»)."""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from app.db import get_session_factory
from app.models import Run
from app.services.runner import RunAlreadyActive, run_all

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
    except Exception:  # noqa: BLE001
        log.exception("Сбой прогона")


def launch_run(trigger: str = "manual", only: list[str] | None = None) -> bool:
    """Запускает прогон в текущем цикле событий. False — если прогон уже идёт."""
    if active_run_id() is not None:
        return False
    task = asyncio.get_running_loop().create_task(_run(trigger, only))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return True
