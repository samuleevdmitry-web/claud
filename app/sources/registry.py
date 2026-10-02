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
        "Нужен российский IP: адаптер пишется по ответам, снятым на вашем компьютере",
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
        "rts", "РТС-тендер (коммерческие)", "https://www.rts-tender.ru", "planned", "Нужен российский IP"
    ),
    SourceInfo(
        "sberbank_ast", "Сбербанк-АСТ (УТП)", "https://utp.sberbank-ast.ru", "planned", "Нужен российский IP"
    ),
    SourceInfo("roseltorg", "Росэлторг", "https://www.roseltorg.ru", "planned", "Нужен российский IP"),
    SourceInfo(
        "etpgpb", "ЭТП ГПБ", "https://etpgpb.ru", "planned", "Официальный API/RSS; нужен российский IP"
    ),
    SourceInfo(
        "tektorg",
        "ТЭК-Торг",
        "https://www.tektorg.ru",
        "manual",
        "Антибот-защита: проверка через Claude in Chrome",
    ),
    SourceInfo("fabrikant", "ЭТП Фабрикант", "https://www.fabrikant.ru", "planned", "Нужен российский IP"),
    SourceInfo(
        "bidzaar",
        "Bidzaar",
        "https://bidzaar.com",
        "manual",
        "Капча на сайте: Claude in Chrome или API поставщика по токену",
    ),
    SourceInfo(
        "ugmk",
        "ЭТП УГМК",
        "https://zakupki.ugmk.com",
        "manual",
        "Адрес площадки уточняется; проверка через Claude in Chrome",
        max_age_days=7,
    ),
    SourceInfo(
        "b2b_center", "B2B-Center", "https://www.b2b-center.ru", "planned", "Разведка на вашем компьютере"
    ),
    SourceInfo(
        "tender_pro", "Tender.Pro", "https://www.tender.pro", "planned", "Разведка на вашем компьютере"
    ),
    SourceInfo("otc", "OTC.ru", "https://otc.ru", "planned", "Разведка на вашем компьютере"),
    SourceInfo(
        "etp_nit",
        "ЭТП «Новые информационные технологии»",
        "https://www.etp-nit.ru",
        "planned",
        "Разведка на вашем компьютере",
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
