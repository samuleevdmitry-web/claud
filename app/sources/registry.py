"""Реестр площадок.

mode:
* "api"     — адаптер в приложении, площадка обходится по расписанию;
* "manual"  — данные приносит Claude in Chrome или человек через импорт (капча, антибот);
* "planned" — адаптер ещё не написан (нужны реальные ответы площадки с российского IP).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.sources.base import SourceAdapter


@dataclass(frozen=True)
class SourceInfo:
    code: str
    title: str
    url: str
    mode: str
    note: str = ""
    factory: Callable[[], SourceAdapter] | None = None
    # Через сколько дней без проверки показывать «Площадка не проверена».
    max_age_days: int = 3


def _gosplan() -> SourceAdapter:
    from app.sources.gosplan import GosplanAdapter

    return GosplanAdapter()


def _onlinecontract() -> SourceAdapter:
    from app.sources.onlinecontract import OnlineContractAdapter

    return OnlineContractAdapter()


def _roseltorg() -> SourceAdapter:
    from app.sources.roseltorg import RoseltorgAdapter

    return RoseltorgAdapter()


def _fabrikant() -> SourceAdapter:
    from app.sources.fabrikant import FabrikantAdapter

    return FabrikantAdapter()


SOURCES: list[SourceInfo] = [
    SourceInfo(
        "eis_gosplan",
        "ЕИС (44-ФЗ, 223-ФЗ) через ГосПлан API",
        "https://gosplan.info",
        "api",
        "Без ключа — тестовый сервер, 10 запросов в минуту",
        _gosplan,
    ),
    SourceInfo(
        "eis",
        "ЕИС напрямую (zakupki.gov.ru)",
        "https://zakupki.gov.ru",
        "planned",
        "Нужен сертификат Минцифры (скачивается автоматически); адаптер — после повторной разведки",
    ),
    SourceInfo(
        "onlinecontract",
        "Online Contract",
        "https://onlinecontract.ru",
        "api",
        "Публичный API api.onlc.ru",
        _onlinecontract,
    ),
    SourceInfo(
        "roseltorg",
        "Росэлторг",
        "https://www.roseltorg.ru",
        "api",
        "Поиск по HTML-выдаче, первые 5 страниц на запрос",
        _roseltorg,
    ),
    SourceInfo(
        "fabrikant",
        "ЭТП Фабрикант",
        "https://www.fabrikant.ru",
        "api",
        "Поиск по HTML-выдаче, первые 3 страницы по 40 на запрос",
        _fabrikant,
    ),
    SourceInfo(
        "rts",
        "РТС-тендер",
        "https://www.rts-tender.ru",
        "manual",
        "Anti-DDoS защита (503) — проверка через Claude in Chrome",
    ),
    SourceInfo(
        "tender_pro",
        "Tender.Pro",
        "https://www.tender.pro",
        "manual",
        "Антибот «Testing…» (503) — проверка через Claude in Chrome",
    ),
    SourceInfo(
        "tektorg",
        "ТЭК-Торг",
        "https://www.tektorg.ru",
        "manual",
        "С вашего IP страница открылась; адаптер — после уточнения параметра поиска",
    ),
    SourceInfo(
        "bidzaar",
        "Bidzaar",
        "https://bidzaar.com",
        "manual",
        "Капча на поиске: Claude in Chrome или API поставщика по токену",
    ),
    SourceInfo(
        "sberbank_ast",
        "Сбербанк-АСТ (УТП)",
        "https://utp.sberbank-ast.ru",
        "planned",
        "Список закупок перенаправляет на главную — нужна повторная разведка",
    ),
    SourceInfo(
        "etpgpb",
        "ЭТП ГПБ",
        "https://etpgpb.ru",
        "planned",
        "Страница поиска открылась, но без учёта запроса — нужна повторная разведка",
    ),
    SourceInfo(
        "b2b_center",
        "B2B-Center",
        "https://www.b2b-center.ru",
        "planned",
        "Страница поиска открылась, но без учёта запроса — нужна повторная разведка",
    ),
    SourceInfo("otc", "OTC.ru", "https://etp.otc.ru", "planned", "Одностраничное приложение — ищем API"),
    SourceInfo(
        "ugmk",
        "ЭТП УГМК",
        "https://zakupki.ugmk.com",
        "manual",
        "Адрес площадки не отвечает — уточняется; проверка через Claude in Chrome",
        max_age_days=7,
    ),
    SourceInfo(
        "etp_nit",
        "ЭТП «Новые информационные технологии»",
        "",
        "planned",
        "Адрес площадки не найден — уточняется",
    ),
]

BY_CODE = {s.code: s for s in SOURCES}


def get_source(code: str) -> SourceInfo:
    try:
        return BY_CODE[code]
    except KeyError as exc:
        raise KeyError(f"неизвестная площадка «{code}»") from exc


def adapter_sources() -> list[SourceInfo]:
    return [s for s in SOURCES if s.mode == "api" and s.factory is not None]
