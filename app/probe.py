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


def _ext(content_type: str, body: bytes) -> str:
    ct = content_type.lower()
    if "json" in ct:
        return "json"
    if "xml" in ct or "rss" in ct or body.lstrip().startswith(b"<?xml"):
        return "xml"
    if "html" in ct:
        return "html"
    return "txt"


async def probe(sources: list[str] | None = None, out_root: Path | None = None) -> Path:
    settings = get_settings()
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    out = (out_root or PROJECT_ROOT / "data" / "probe") / stamp
    out.mkdir(parents=True, exist_ok=True)
    report: dict[str, list[dict]] = {}
    headers = {
        "User-Agent": settings.user_agent,
        "Accept": "text/html,application/xhtml+xml,application/json,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9",
    }
    async with httpx.AsyncClient(headers=headers, timeout=40, follow_redirects=True) as client:
        for code, urls in PROBES.items():
            if sources and code not in sources:
                continue
            (out / code).mkdir(exist_ok=True)
            report[code] = []
            for name, url in urls:
                entry: dict = {"name": name, "url": url}
                try:
                    r = await client.get(url)
                    ext = _ext(r.headers.get("content-type", ""), r.content)
                    (out / code / f"{name}.{ext}").write_bytes(r.content)
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
