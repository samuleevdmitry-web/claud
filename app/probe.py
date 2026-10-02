"""Разведка площадок с компьютера пользователя (российский IP).

Отсюда (из облака) часть площадок недоступна, поэтому адаптеры для них пишутся по реальным
ответам, снятым этой командой на вашем компьютере:

    python -m app.cli probe            # снять ответы всех площадок
    python -m app.cli probe --source eis --source rts

Результат — папка data/probe/<дата>/ и архив data/probe/<дата>.zip. Архив нужно прислать
разработчику. Команда делает по 2–4 запроса на площадку с паузой 3–6 секунд и не обходит
капчи: если площадка показывает капчу или антибот, это просто фиксируется в отчёте.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import shutil
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import httpx

from app.config import PROJECT_ROOT, get_settings
from app.sources.tls import ensure_russian_ca, ssl_context

Q = "полотенца"
QE = quote(Q)

PROBES: dict[str, list[tuple[str, str]]] = {
    "eis": [
        ("robots", "https://zakupki.gov.ru/robots.txt"),
        (
            "rss",
            f"https://zakupki.gov.ru/epz/order/extendedsearch/rss.html?searchString={QE}&morphology=on"
            "&fz44=on&fz223=on&af=on&sortBy=UPDATE_DATE&sortDirection=false&recordsPerPage=_50",
        ),
        (
            "search",
            f"https://zakupki.gov.ru/epz/order/extendedsearch/results.html?searchString={QE}&morphology=on"
            "&fz44=on&fz223=on&af=on&sortBy=UPDATE_DATE&sortDirection=false&recordsPerPage=_50",
        ),
    ],
    "rts": [
        ("robots", "https://www.rts-tender.ru/robots.txt"),
        ("commercial", "https://www.rts-tender.ru/poisk/poisk-commercial-tenderi/"),
        ("search", f"https://www.rts-tender.ru/poisk/search?keywords={QE}"),
    ],
    "sberbank_ast": [
        ("robots", "https://www.sberbank-ast.ru/robots.txt"),
        ("utp_list", "https://utp.sberbank-ast.ru/VIP/List/PurchaseList"),
        ("purchase_list", "https://www.sberbank-ast.ru/purchaseList.aspx"),
    ],
    "roseltorg": [
        ("robots", "https://www.roseltorg.ru/robots.txt"),
        ("search", f"https://www.roseltorg.ru/procedures/search?query_field={QE}"),
    ],
    "etpgpb": [
        ("robots", "https://etpgpb.ru/robots.txt"),
        ("api_doc", "https://etpgpb.ru/api/"),
        ("search", f"https://etpgpb.ru/procedures/?search={QE}"),
        ("rss", f"https://etpgpb.ru/procedures.rss?search={QE}"),
    ],
    "tektorg": [
        ("robots", "https://www.tektorg.ru/robots.txt"),
        ("search", f"https://www.tektorg.ru/223-fz/procedures?q={QE}"),
    ],
    "fabrikant": [
        ("robots", "https://www.fabrikant.ru/robots.txt"),
        ("home", "https://www.fabrikant.ru/"),
        ("search", f"https://www.fabrikant.ru/procedure/search/?query={QE}"),
    ],
    "bidzaar": [
        ("robots", "https://bidzaar.com/robots.txt"),
        ("aggregator", "https://bidzaar.com/aggregator"),
    ],
    "ugmk": [
        ("zakupki", "https://zakupki.ugmk.com/"),
        ("etp", "https://etp.ugmk.com/"),
    ],
    "b2b_center": [
        ("robots", "https://www.b2b-center.ru/robots.txt"),
        ("search", f"https://www.b2b-center.ru/market/?searching=1&f_keyword={QE}"),
    ],
    "tender_pro": [
        ("robots", "https://www.tender.pro/robots.txt"),
        ("home", "https://www.tender.pro/"),
    ],
    "otc": [
        ("robots", "https://otc.ru/robots.txt"),
        ("search", f"https://otc.ru/tenders?keywords={QE}"),
    ],
    "etp_nit": [
        ("robots", "https://www.etp-nit.ru/robots.txt"),
        ("home", "https://www.etp-nit.ru/"),
    ],
}


# Второй заход: точечные варианты параметров и скрипты одностраничных приложений («+js» — сохранить
# собственные JS-файлы страницы, чтобы найти в них адрес API).
ROUND2: dict[str, list[tuple[str, str]]] = {
    "eis": [
        (
            "rss",
            f"https://zakupki.gov.ru/epz/order/extendedsearch/rss.html?searchString={QE}&morphology=on"
            "&fz44=on&fz223=on&af=on&sortBy=PUBLISH_DATE&sortDirection=false&recordsPerPage=_50",
        ),
        (
            "search",
            f"https://zakupki.gov.ru/epz/order/extendedsearch/results.html?searchString={QE}"
            "&morphology=on&fz44=on&fz223=on&af=on&sortBy=PUBLISH_DATE&sortDirection=false"
            "&recordsPerPage=_50",
        ),
        (
            "card_search",
            "https://zakupki.gov.ru/epz/order/extendedsearch/results.html?searchString=0372200119926000112",
        ),
        (
            "card44",
            "https://zakupki.gov.ru/epz/order/notice/ezk2020/view/common-info.html?regNumber=0372200119926000112",
        ),
        (
            "card223",
            "https://zakupki.gov.ru/epz/order/notice/notice223/common-info.html?regNumber=32616427976",
        ),
    ],
    "tektorg": [
        ("p_query", f"https://www.tektorg.ru/223-fz/procedures?query={QE}"),
        ("p_search", f"https://www.tektorg.ru/223-fz/procedures?search={QE}"),
        ("p_text", f"https://www.tektorg.ru/223-fz/procedures?text={QE}"),
        ("p_searchtext", f"https://www.tektorg.ru/223-fz/procedures?searchText={QE}"),
        ("page+js", f"https://www.tektorg.ru/223-fz/procedures?q={QE}"),
    ],
    "etpgpb": [
        ("api_late", "https://etp.gpb.ru/api/procedures.php?late=1"),
        ("api_gaz", "https://etpgaz.gazprombank.ru/api/procedures.php?late=1"),
        ("rss_plain", "https://etpgpb.ru/procedures.rss"),
        ("p_search_per", f"https://etpgpb.ru/procedures/?search={QE}&per=50"),
        ("p_procedure_search", f"https://etpgpb.ru/procedures/?procedure%5Bsearch%5D={QE}"),
        ("page+js", f"https://etpgpb.ru/procedures/?search={QE}"),
    ],
    "b2b_center": [
        ("classic", f"https://www.b2b-center.ru/market/?searching=1&f_keyword={QE}"),
        ("next_query", f"https://www.b2b-center.ru/app/next/market-search/?query={QE}"),
        ("page+js", f"https://www.b2b-center.ru/app/next/market-search/?searching=1&f_keyword={QE}"),
    ],
    "otc": [
        ("page+js", f"https://etp.otc.ru/tenders?keywords={QE}"),
        ("otc_tenders", f"https://otc.ru/tenders/?q={QE}"),
    ],
    "sberbank_ast": [
        ("utp_home+js", "https://utp.sberbank-ast.ru/"),
        ("utp_main_list", "https://utp.sberbank-ast.ru/Main/List/PurchaseList"),
        ("united", "https://www.sberbank-ast.ru/UnitedPurchaseList.aspx"),
    ],
    "roseltorg": [
        ("card", "https://www.roseltorg.ru/procedure/32616427976/1"),
    ],
    "fabrikant": [
        ("card44", "https://44.fabrikant.ru/44/procedure/ezk21/0372200119926000112"),
        ("card223", "https://fabrikant.ru/v2/trades/procedure/view/KTieTKEDeQ3-rBn3goWraA"),
    ],
    "ugmk": [
        ("zakupki_http", "http://zakupki.ugmk.com/"),
        ("ugmk_site", "https://www.ugmk.com/"),
    ],
}

_SCRIPT_RE = re.compile(rb"<script[^>]+src=[\"']([^\"']+)[\"']", re.I)
MAX_SCRIPTS = 8
MAX_SCRIPT_BYTES = 6_000_000


def _ext(content_type: str, body: bytes) -> str:
    ct = content_type.lower()
    if "json" in ct:
        return "json"
    if "xml" in ct or "rss" in ct or body.lstrip().startswith(b"<?xml"):
        return "xml"
    if "html" in ct:
        return "html"
    return "txt"


async def _save_scripts(client: httpx.AsyncClient, page: httpx.Response, folder: Path, name: str) -> int:
    """Сохраняет собственные JS-файлы страницы (не счётчики и не CDN), чтобы найти адрес API."""
    host = page.url.host.split(".")[-2] if page.url.host else ""
    saved = 0
    for src in _SCRIPT_RE.findall(page.content):
        url = page.url.join(src.decode("utf-8", "replace"))
        if host not in (url.host or "") or saved >= MAX_SCRIPTS:
            continue
        try:
            r = await client.get(url)
        except httpx.HTTPError:
            continue
        if r.status_code == 200 and len(r.content) <= MAX_SCRIPT_BYTES:
            saved += 1
            (folder / f"{name}.script{saved}.js").write_bytes(r.content)
        await asyncio.sleep(random.uniform(1, 2))
    return saved


async def probe(sources: list[str] | None = None, out_root: Path | None = None, round_: int = 1) -> Path:
    settings = get_settings()
    stamp = datetime.now().strftime("%Y%m%d_%H%M") + (f"_r{round_}" if round_ > 1 else "")
    out = (out_root or PROJECT_ROOT / "data" / "probe") / stamp
    out.mkdir(parents=True, exist_ok=True)
    report: dict[str, list[dict]] = {}
    headers = {
        "User-Agent": settings.user_agent,
        "Accept": "text/html,application/xhtml+xml,application/json,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9",
    }
    ca = await asyncio.to_thread(ensure_russian_ca)
    report["_env"] = [{"russian_ca": str(ca) if ca else "нет — госсайты будут с ошибкой сертификата"}]
    print("Сертификат Минцифры:", "есть" if ca else "НЕ скачался (см. README)")
    async with httpx.AsyncClient(
        headers=headers, timeout=40, follow_redirects=True, verify=ssl_context()
    ) as client:
        for code, urls in (ROUND2 if round_ == 2 else PROBES).items():
            if sources and code not in sources:
                continue
            (out / code).mkdir(exist_ok=True)
            report[code] = []
            for name, url in urls:
                entry: dict = {"name": name, "url": url}
                with_js = name.endswith("+js")
                name = name.removesuffix("+js")
                try:
                    r = await client.get(url)
                    ext = _ext(r.headers.get("content-type", ""), r.content)
                    (out / code / f"{name}.{ext}").write_bytes(r.content)
                    if with_js and r.status_code == 200:
                        entry["scripts"] = await _save_scripts(client, r, out / code, name)
                    title = re.search(rb"<title[^>]*>(.*?)</title>", r.content[:20000], re.S | re.I)
                    entry |= {
                        "status": r.status_code,
                        "final_url": str(r.url),
                        "bytes": len(r.content),
                        "content_type": r.headers.get("content-type", ""),
                        "server": r.headers.get("server", ""),
                        "title": title.group(1).decode("utf-8", "replace").strip()[:200] if title else "",
                        "redirects": [str(h.url) for h in r.history],
                    }
                except httpx.HTTPError as exc:
                    entry["error"] = repr(exc)
                report[code].append(entry)
                note = entry.get("title") or entry.get("error", "")
                print(f"{code:<14} {name:<14} {entry.get('status', 'ERR')} {note}")
                await asyncio.sleep(random.uniform(3, 6))
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    archive = shutil.make_archive(str(out), "zip", out)
    print(f"\nГотово: {archive}\nПришлите этот архив разработчику.")
    return Path(archive)
