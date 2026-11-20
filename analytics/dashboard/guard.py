"""Блок, который не удалось построить, не роняет страницу: вместо него — пояснение, остальное показывается.

Два уровня:
- данные: safe("Название", lambda: ...) — ошибка запроса или расчёта превращается в значение по умолчанию,
  а название блока и ошибка попадают в список проблем страницы (его показывает base.html вверху);
- шаблон: {% guard "Название" %} … {% endguard %} — ошибка при выводе блока (деление на ноль, пустое значение)
  заменяет только этот блок карточкой «не построен».

Подробности ошибки — в журнале дашборда (docker compose logs dashboard).
"""

from __future__ import annotations

import contextvars
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar, TypeVar

import psycopg
from jinja2 import nodes
from jinja2.ext import Extension
from markupsafe import Markup, escape

log = logging.getLogger("analytics.dashboard")
T = TypeVar("T")


@dataclass(frozen=True)
class Problem:
    title: str
    error: str


_problems: contextvars.ContextVar[list[Problem] | None] = contextvars.ContextVar("problems", default=None)


def start() -> list[Problem]:
    """Новый список проблем для одной страницы или одного отчёта."""
    problems: list[Problem] = []
    _problems.set(problems)
    return problems


def describe(exc: BaseException) -> str:
    """Ошибка одной строкой: тип и первая строка текста."""
    if isinstance(exc, psycopg.errors.QueryCanceled):
        return "запрос к базе выполнялся дольше минуты (обновите страницу через пару минут; если повторяется — сообщите)"
    text = (str(exc).strip().splitlines() or [""])[0][:300]
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def _note(title: str, exc: Exception) -> Problem:
    log.exception("блок «%s» не построен", title)
    problem = Problem(title, describe(exc))
    problems = _problems.get()
    if problems is not None and problem not in problems:
        problems.append(problem)
    return problem


def safe(title: str, build: Callable[[], T], default: T = None) -> T:
    """Построить часть страницы; при ошибке — default и запись в список проблем. Нет связи с базой — это не ошибка
    блока, а всей страницы: такое исключение пропускается дальше."""
    try:
        return build()
    except psycopg.errors.QueryCanceled as exc:  # долгий запрос — беда этого блока, а не связи с базой
        _note(title, exc)
        return default
    except psycopg.OperationalError:
        raise
    except Exception as exc:  # noqa: BLE001 — любой сбой блока остаётся в блоке
        _note(title, exc)
        return default


def error_card(title: str, error: str) -> Markup:
    return Markup(
        '<div class="card block-error" role="status"><strong>{}</strong> — не построен: {}. '
        "Остальное на странице показано. Если повторяется — пришлите этот текст.</div>"
    ).format(escape(title), escape(error))


class GuardExtension(Extension):
    """{% guard "Название" %} … {% endguard %}: ошибка внутри — карточка «не построен» вместо блока."""

    tags: ClassVar[set[str]] = {"guard"}

    def parse(self, parser):
        lineno = next(parser.stream).lineno
        title = parser.parse_expression()
        body = parser.parse_statements(("name:endguard",), drop_needle=True)
        return nodes.CallBlock(self.call_method("_render", [title]), [], [], body).set_lineno(lineno)

    def _render(self, title: str, caller) -> Markup:
        try:
            return caller()
        except Exception as exc:  # noqa: BLE001
            problem = _note(title, exc)
            return error_card(problem.title, problem.error)
