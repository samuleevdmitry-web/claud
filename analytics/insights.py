"""Аналитические сводки: плюсы, минусы, вопросы и что проверить — по цифрам из базы.

Каждое правило — вопрос аналитика к отчёту: что выросло или упало, кто конвертирует лучше и хуже среднего,
где отказы подозрительно высокие, откуда взялся новый трафик. Пороги подобраны так, чтобы не шуметь
на мелочах: сегменты меньше нескольких процентов трафика или с десятками визитов не комментируются.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from statistics import median

from analytics.format import fmt_dec, fmt_int, fmt_pct, month_short, plural

KINDS = {"plus": "Плюсы", "minus": "Минусы", "question": "Вопросы", "check": "Что проверить"}

MIN_SHARE = 3.0  # % визитов: меньшие сегменты не комментируем
MIN_VISITS = 50
CHANGE = 10.0  # % изменения, которое уже стоит упомянуть
# Срезы, где «рост сегмента» не имеет смысла (часы, интервалы глубины) — для них только свои правила.
NO_TREND = {"hour", "weekday", "depth", "duration", "gender", "new_user"}
TARGET_COUNTRIES = {"Россия", "Казахстан", "Беларусь", "Узбекистан", "Кыргызстан", "Армения", "Таджикистан",
                    "Азербайджан", "Грузия", "Не определено"}
MOBILE_KEYS = {"mobile", "tablet"}
VISITS = ("визит", "визита", "визитов")
LEADS = ("заявка", "заявки", "заявок")
QUERIES = ("запрос", "запроса", "запросов")


@dataclass(frozen=True)
class Finding:
    kind: str  # plus | minus | question | check
    text: str


@dataclass(frozen=True)
class Summary:
    groups: list[tuple[str, str, list[str]]]  # [(вид, «Плюсы», [тексты]), …] — только непустые

    def __bool__(self) -> bool:
        return bool(self.groups)


def summarize(findings: Iterable[Finding], limit: int = 6) -> Summary:
    by_kind: dict[str, list[str]] = {k: [] for k in KINDS}
    for f in findings:
        if f.text not in by_kind[f.kind]:
            by_kind[f.kind].append(f.text)
    return Summary([(k, KINDS[k], texts[:limit]) for k, texts in by_kind.items() if texts])


def _pct_change(cur, prev) -> float | None:
    if cur is None or not prev:
        return None
    return (float(cur) - float(prev)) / float(prev) * 100


def _signed(value: float, digits: int = 1, unit: str = "%") -> str:
    sign = "+" if value > 0 else "−" if value < 0 else ""
    return f"{sign}{fmt_dec(abs(value), digits)}{unit}"


def is_undefined(row: dict) -> bool:
    """«Не определено» во всех группировках — в отчётах по видам источников это визиты не из этого источника."""
    return all(part == "(none)" for part in row["dim_key"].split("|")) or row["dim_label"].startswith("Не определено")


def _name(row: dict) -> str:
    label = row["dim_label"]
    if label.startswith("http"):  # страницы входа: домен сайта не несёт смысла
        label = "/" + label.split("://", 1)[1].partition("/")[2]
    return f"«{label}»"


# ----- Сводка трафика -----

def traffic_summary(m: dict, robots_share: float | None, robots_share_prev: float | None,
                    daily: list[dict], prev: date, yoy_partial: bool, audience: str) -> list[Finding]:
    out: list[Finding] = []
    prev_label = month_short(prev)
    visits = _pct_change(m.get("visits"), m.get("visits_prev"))
    if visits is not None and abs(visits) >= CHANGE:
        kind = "plus" if visits > 0 else "minus"
        out.append(Finding(kind, f"Визиты: {fmt_int(m['visits'])}, {_signed(visits)} к {prev_label} "
                                 f"({fmt_int(m['visits_prev'])})."))
    leads = _pct_change(m.get("leads"), m.get("leads_prev"))
    if leads is not None and abs(leads) >= CHANGE and (m.get("leads_prev") or 0) >= 10:
        kind = "plus" if leads > 0 else "minus"
        out.append(Finding(kind, f"Заявки с сайта: {fmt_int(m['leads'])}, {_signed(leads)} к {prev_label}."))
    if visits is not None and leads is not None and visits > CHANGE and leads < 0:
        out.append(Finding("question", "Трафик вырос, а заявок стало меньше. Новый трафик нецелевой "
                                       "или что-то мешает оставить заявку? Сравните каналы и страницы входа."))
    bounce_prev, bounce = m.get("bounce_rate_prev"), m.get("bounce_rate")
    if bounce is not None and bounce_prev is not None:
        diff = float(bounce) - float(bounce_prev)
        if diff >= 3:
            out.append(Finding("minus", f"Отказы выросли до {fmt_pct(bounce)} ({_signed(diff, unit=' п.п.')})."))
            out.append(Finding("check", "Где выросли отказы: вкладки «Источники» и «Содержание» — какой канал "
                                        "и какие страницы входа дают отказы выше среднего."))
        elif diff <= -3:
            out.append(Finding("plus", f"Отказы снизились до {fmt_pct(bounce)} ({_signed(diff, unit=' п.п.')})."))
    if robots_share is not None:
        if robots_share >= 10:
            out.append(Finding("minus", f"Роботы — {fmt_dec(robots_share, 0)}% визитов за месяц: весь трафик "
                                        "заметно завышен."))
            out.append(Finding("check", "Откуда роботы: включить в настройках счётчика фильтрацию роботов "
                                        "«по строгим правилам и поведению» и сравнить каналы в двух вариантах."))
        elif robots_share_prev is not None and robots_share_prev >= 10 and robots_share < 2:
            out.append(Finding("plus", f"Роботов в данных почти нет (было {fmt_dec(robots_share_prev, 0)}% "
                                       f"в {prev_label}) — весь трафик и «только люди» теперь совпадают."))
    if audience == "all":
        out.append(Finding("check", "Сейчас показан весь трафик, вместе с роботами. Для сравнения месяцев "
                                    "надёжнее вариант «Только люди»."))
    out += daily_spikes(daily)
    if yoy_partial:
        out.append(Finding("question", "Год назад счётчик собирал данные не каждый день: сравнение с прошлым "
                                       "годом за этот месяц некорректно."))
    return out


def daily_spikes(daily: list[dict], factor: float = 1.8) -> list[Finding]:
    """Дни, когда визитов было в factor раз больше медианы месяца, — подряд идущие склеиваются."""
    values = [r["visits"] for r in daily if r["visits"]]
    if len(values) < 10:
        return []
    base = median(values)
    spikes = [r for r in daily if r["visits"] >= base * factor]
    if not spikes:
        return []
    runs: list[list[dict]] = []
    for r in spikes:
        if runs and (r["day"] - runs[-1][-1]["day"]).days == 1:
            runs[-1].append(r)
        else:
            runs.append([r])
    out = []
    for run in runs[:2]:
        period = run[0]["day"].strftime("%d.%m") + (f"–{run[-1]['day'].strftime('%d.%m')}" if len(run) > 1 else "")
        peak = max(r["visits"] for r in run)
        leads = sum(r.get("leads") or 0 for r in run)
        out.append(Finding("question", f"Всплеск {period}: до {plural(peak, VISITS)} в день при обычных "
                                       f"{fmt_int(base)}, заявок за эти дни — {fmt_int(leads)}. Что это было — "
                                       "рассылка, реклама, упоминание или боты?"))
    return out


# ----- Любой отчёт Метрики -----

def slice_findings(slice_: str, title: str, rows: list[dict], total_visits: int, site_conv: float,
                   site_bounce: float, prev: date) -> list[Finding]:
    """Общие правила для отчёта + особые для отдельных отчётов (страны, устройства, время…)."""
    if not rows or not total_visits:
        return []
    prev_label = month_short(prev)
    for r in rows:
        r["share"] = r["visits"] / total_visits * 100
    big = [r for r in rows if r["share"] >= MIN_SHARE and r["visits"] >= MIN_VISITS and not is_undefined(r)]
    out: list[Finding] = []

    if slice_ not in NO_TREND:
        with_prev = [r for r in big if r.get("visits_prev")]
        if with_prev:
            up = max(with_prev, key=lambda r: r["visits"] - r["visits_prev"])
            delta = up["visits"] - up["visits_prev"]
            if delta >= max(MIN_VISITS, up["visits_prev"] * CHANGE / 100):
                out.append(Finding("plus", f"{title}: {_name(up)} — визиты {_signed(delta, 0, '')} "
                                           f"({_signed(_pct_change(up['visits'], up['visits_prev']))}) к {prev_label}."))
            down = min(with_prev, key=lambda r: r["visits"] - r["visits_prev"])
            delta = down["visits"] - down["visits_prev"]
            if delta <= -max(MIN_VISITS, down["visits_prev"] * CHANGE / 100):
                out.append(Finding("minus", f"{title}: {_name(down)} — визиты {_signed(delta, 0, '')} "
                                            f"({_signed(_pct_change(down['visits'], down['visits_prev']))}) к {prev_label}."))
        for r in big:
            before = r.get("visits_prev") or 0
            if r["visits"] >= 3 * max(before, 1) and r["visits"] - before >= 2 * MIN_VISITS:
                out.append(Finding("question", f"{title}: {_name(r)} — {plural(r['visits'], VISITS)} "
                                               f"({fmt_dec(r['share'])}%), а в {prev_label} было {fmt_int(before)}. "
                                               "Откуда этот трафик?"))

    if site_conv > 0:
        leaders = sorted((r for r in big if (r.get("leads") or 0) >= 3
                          and float(r.get("conversion_pct") or 0) >= 1.5 * site_conv),
                         key=lambda r: -float(r["conversion_pct"]))
        for r in leaders[:2]:
            ratio = float(r["conversion_pct"]) / site_conv
            out.append(Finding("plus", f"{title}: {_name(r)} конвертирует в заявку в {fmt_dec(ratio)} раза лучше "
                                       f"среднего ({fmt_pct(r['conversion_pct'], 2)} против {fmt_pct(site_conv, 2)})."))
        for r in big:
            if r["share"] >= 5 and float(r.get("conversion_pct") or 0) < 0.3 * site_conv:
                leads = r.get("leads") or 0
                tail = "ни одной заявки" if not leads else f"конверсия {fmt_pct(r['conversion_pct'], 2)}"
                out.append(Finding("minus", f"{title}: {_name(r)} — {fmt_dec(r['share'])}% визитов, но {tail} "
                                            f"(в среднем {fmt_pct(site_conv, 2)})."))

    if site_bounce and slice_ not in {"depth", "duration"}:
        for r in big:
            bounce = float(r.get("bounce_rate") or 0)
            if bounce >= max(2 * site_bounce, site_bounce + 15):
                out.append(Finding("check", f"{title}: {_name(r)} — отказы {fmt_pct(bounce)} при среднем "
                                            f"{fmt_pct(site_bounce)}. Проверить качество трафика и страницы входа."))

    special = SPECIAL.get(slice_)
    if special:
        out += special(rows, total_visits, site_conv)
    return out


def _countries(rows: list[dict], total: int, site_conv: float) -> list[Finding]:
    foreign = [r for r in rows if r["dim_label"] not in TARGET_COUNTRIES]
    visits = sum(r["visits"] for r in foreign)
    share = visits / total * 100
    if share < 3:
        return []
    top = ", ".join(f"{r['dim_label']} {fmt_int(r['visits'])}" for r in sorted(foreign, key=lambda r: -r["visits"])[:3])
    return [
        Finding("question", f"{plural(visits, VISITS)} ({fmt_dec(share)}%) из стран, где нет продаж: {top}."),
        Finding("check", "Трафик из стран без продаж похож на ботов: сравнить варианты «Весь трафик» и «Только люди», "
                         "посмотреть каналы и страницы входа этих визитов; если подтвердится — исключить их из "
                         "целей и рекламы."),
    ]


def _devices(rows: list[dict], total: int, site_conv: float) -> list[Finding]:
    mobile = [r for r in rows if r["dim_key"] in MOBILE_KEYS]
    desktop = next((r for r in rows if r["dim_key"] == "desktop"), None)
    m_visits = sum(r["visits"] for r in mobile)
    if not desktop or not m_visits:
        return []
    m_conv = sum(r.get("leads") or 0 for r in mobile) / m_visits * 100
    d_conv = float(desktop.get("conversion_pct") or 0)
    share = m_visits / total * 100
    out = [Finding("question", f"С телефонов и планшетов — {fmt_dec(share, 0)}% визитов; конверсия "
                               f"{fmt_pct(m_conv, 2)} против {fmt_pct(d_conv, 2)} с компьютеров.")]
    if share >= 30 and d_conv and m_conv < 0.5 * d_conv:
        out.append(Finding("check", "Мобильная версия конвертирует вдвое хуже: пройти путь до заявки с телефона "
                                    "— формы, кликабельный номер, скорость загрузки каталога."))
    return out


def _hours(rows: list[dict], total: int, site_conv: float) -> list[Finding]:
    out = []
    night = sum(r["visits"] for r in rows if r["dim_key"].isdigit() and int(r["dim_key"]) < 6)
    if night / total * 100 >= 15:
        out.append(Finding("check", f"Ночью (0–6 ч) — {fmt_dec(night / total * 100, 0)}% визитов: для B2B-сайта "
                                    "много, проверить на ботов."))
    with_leads = [r for r in rows if r.get("leads")]
    if with_leads:
        top = sorted(with_leads, key=lambda r: -r["leads"])[:3]
        hours = ", ".join(r["dim_label"] for r in top)
        out.append(Finding("question", f"Больше всего заявок приходит в {hours}. Совпадает ли это с часами, "
                                       "когда менеджеры на месте и быстро отвечают?"))
    return out


def _weekdays(rows: list[dict], total: int, site_conv: float) -> list[Finding]:
    weekend = [r for r in rows if r["dim_key"] in {"6", "7"}]
    leads = sum(r.get("leads") or 0 for r in rows)
    w_leads = sum(r.get("leads") or 0 for r in weekend)
    if leads and w_leads / leads >= 0.15:
        return [Finding("question", f"На выходные приходится {fmt_dec(w_leads / leads * 100, 0)}% заявок — "
                                    "кто их обрабатывает в субботу и воскресенье?")]
    return []


def _new_users(rows: list[dict], total: int, site_conv: float) -> list[Finding]:
    by_key = {r["dim_label"]: r for r in rows}
    new, back = by_key.get("Новые посетители"), by_key.get("Вернувшиеся посетители")
    if not new or not back or not new["visits"]:
        return []
    out = [Finding("question", f"Вернувшиеся — {fmt_dec(back['visits'] / total * 100, 0)}% визитов, их конверсия "
                               f"{fmt_pct(back.get('conversion_pct'), 2)} против {fmt_pct(new.get('conversion_pct'), 2)} "
                               "у новых.")]
    if float(back.get("conversion_pct") or 0) > 2 * float(new.get("conversion_pct") or 0) > 0:
        out.append(Finding("check", "Решение чаще принимают со второго-третьего визита: есть ли способы вернуть "
                                    "посетителя — ретаргетинг, подписка, прайс на почту?"))
    return out


def _landing(rows: list[dict], total: int, site_conv: float) -> list[Finding]:
    with_leads = sorted((r for r in rows if (r.get("leads") or 0) >= 3), key=lambda r: -r["leads"])[:3]
    if not with_leads:
        return []
    pages = "; ".join(f"{_name(r)} — {fmt_int(r['leads'])}" for r in with_leads)
    return [Finding("plus", f"Больше всего заявок со страниц входа: {pages}.")]


SPECIAL = {"country": _countries, "device": _devices, "hour": _hours, "weekday": _weekdays,
           "new_user": _new_users, "landing": _landing}


# ----- Цели -----

def goals_findings(goals: list[dict], prev: date) -> list[Finding]:
    out = []
    for g in goals:
        change = _pct_change(g["reaches"], g.get("reaches_prev"))
        if change is None or (g.get("reaches_prev") or 0) < 10 or abs(change) < 30:
            continue
        kind = "plus" if change > 0 else "minus"
        out.append(Finding(kind, f"Цель «{g['name']}»: {fmt_int(g['reaches'])} достижений, {_signed(change)} "
                                 f"к {month_short(prev)}."))
    noisy = [g for g in goals if g["type"] == "form" and not g["is_lead"] and g["reaches"] >= 10 * max(
        sum(x["reaches"] for x in goals if x["is_lead"]), 1)]
    for g in noisy[:1]:
        out.append(Finding("check", f"Цель «{g['name']}» срабатывает {fmt_int(g['reaches'])} раз — в десятки раз "
                                    "чаще реальных заявок. Скорее всего, она ловит любые формы (поиск, фильтры): "
                                    "уточнить настройку цели."))
    return out


# ----- Поиск Яндекса -----

def search_findings(sm: dict | None, partial: bool, movers: dict, stale_days: int | None,
                    problems: list[dict], prev: date) -> list[Finding]:
    out = []
    prev_label = month_short(prev)
    if stale_days and stale_days > 5:
        out.append(Finding("minus", f"Вебмастер не обновлял статистику {stale_days} дн.: цифры поиска за последние "
                                    "недели неполные."))
        out.append(Finding("check", "Открыть Вебмастер в интерфейсе: если там данные свежие — проблема в API, "
                                    "если нет — написать в поддержку Вебмастера."))
    if sm and not partial:
        for label, key, good_up in (("Показы", "shows", True), ("Клики", "clicks", True)):
            change = _pct_change(sm[key], sm[f"{key}_prev"])
            if change is not None and abs(change) >= CHANGE:
                kind = "plus" if (change > 0) == good_up else "minus"
                out.append(Finding(kind, f"{label}: {fmt_int(sm[key])}, {_signed(change)} к {prev_label}."))
    if sm and sm.get("ctr_pct") is not None and sm.get("ctr_pct_prev") is not None:
        diff = float(sm["ctr_pct"]) - float(sm["ctr_pct_prev"])
        if abs(diff) >= 0.5:
            kind = "plus" if diff > 0 else "minus"
            out.append(Finding(kind, f"CTR {fmt_pct(sm['ctr_pct'], 2)} ({_signed(diff, unit=' п.п.')})."))
            if diff < 0:
                out.append(Finding("question", "CTR падает: показы растут на информационных запросах с низкой "
                                               "кликабельностью или сниппеты стали хуже? Посмотреть запросы с "
                                               "большими показами и CTR меньше 1%."))
    if movers.get("down"):
        top = "; ".join(f"«{r['query']}» {fmt_dec(r['position_prev'])} → {fmt_dec(r['position'])}"
                        for r in movers["down"][:3])
        out.append(Finding("minus", f"Позиции упали: {plural(len(movers['down']), QUERIES)}, например {top}."))
    if movers.get("up"):
        top = "; ".join(f"«{r['query']}» {fmt_dec(r['position_prev'])} → {fmt_dec(r['position'])}"
                        for r in movers["up"][:3])
        out.append(Finding("plus", f"Позиции выросли: {plural(len(movers['up']), QUERIES)}, например {top}."))
    if movers.get("lost"):
        top = ", ".join(f"«{r['query']}»" for r in movers["lost"][:3])
        out.append(Finding("check", f"Пропали из популярных: {plural(len(movers['lost']), QUERIES)} с показами в "
                                    f"прошлом месяце, например {top}. Проверить их страницы — не закрыты ли от "
                                    "индексации, не сменились ли адреса."))
    if partial:
        out.append(Finding("question", "Месяц в Вебмастере неполный — сравнения показов и кликов с прошлым "
                                       "месяцем нет, позиции сравниваются по доступным дням."))
    for p in problems:
        kind = "minus" if p["severity"] in ("FATAL", "CRITICAL") else "check"
        out.append(Finding(kind, f"Диагностика Вебмастера: {p['problem']} ({p['severity']})."))
    return out


# ----- Спрос -----

def demand_findings(brand: dict, market: list[dict], categories: list[dict], year_ago: date) -> list[Finding]:
    out = []
    ya = month_short(year_ago)
    change = _pct_change(brand.get("cur"), brand.get("yoy"))
    if change is not None and abs(change) >= 5:
        kind = "plus" if change > 0 else "minus"
        out.append(Finding(kind, f"Брендовый спрос (B2B) {fmt_int(brand['cur'])}, {_signed(change)} к {ya}."))
    for r in market:
        change = _pct_change(r["cur"], r["yoy"])
        if change is not None and abs(change) >= 10:
            kind = "plus" if change > 0 else "minus"
            out.append(Finding(kind, f"Рынок: «{r['phrase']}» {_signed(change)} к {ya}."))
    rated = [c for c in categories if (c.get("yoy") or 0) >= 300 and c.get("demand")]
    for c in sorted(rated, key=lambda c: -_pct_change(c["demand"], c["yoy"]))[:3]:
        change = _pct_change(c["demand"], c["yoy"])
        if change >= 15:
            out.append(Finding("question", f"Спрос на «{c['category']}» {_signed(change)} к {ya} — хватает ли "
                                           "ассортимента и наличия, видна ли категория в поиске?"))
    for c in sorted(rated, key=lambda c: _pct_change(c["demand"], c["yoy"]))[:2]:
        change = _pct_change(c["demand"], c["yoy"])
        if change <= -15:
            out.append(Finding("minus", f"Спрос на «{c['category']}» {_signed(change)} к {ya}."))
    return out


# ----- Карты -----

def maps_findings(cards: list[dict], prev: date) -> list[Finding]:
    out = []
    for c in cards:
        m = c["data"]
        actions = (m["calls"] or 0) + (m["routes"] or 0) + (m["site"] or 0)
        actions_prev = (m["calls_prev"] or 0) + (m["routes_prev"] or 0) + (m["site_prev"] or 0)
        if not m["card_visits"] or not m["card_visits_prev"] or actions_prev < 20:
            continue
        rate, rate_prev = actions / m["card_visits"] * 100, actions_prev / m["card_visits_prev"] * 100
        if abs(rate - rate_prev) >= 1:
            kind = "plus" if rate > rate_prev else "minus"
            out.append(Finding(kind, f"«{c['name']}»: действий на 100 визитов карточки {fmt_dec(rate)} "
                                     f"(в {month_short(prev)} — {fmt_dec(rate_prev)})."))
        if rate < rate_prev - 1:
            out.append(Finding("check", f"«{c['name']}»: карточку смотрят, но реже звонят и строят маршрут — "
                                        "проверить часы работы, телефоны, фото и свежие отзывы."))
    return out


# ----- Позиции RabbitSeo -----

def rabbit_findings(compare: dict[str, list[dict]], now: dict, then: dict) -> list[Finding]:
    out = []
    period = f"{then['date']:%d.%m} → {now['date']:%d.%m}"
    if now["top10"] != then["top10"]:
        kind = "plus" if now["top10"] > then["top10"] else "minus"
        out.append(Finding(kind, f"RabbitSeo: в ТОП-10 {plural(now['top10'], QUERIES)} ({period}: было {then['top10']})."))
    if compare["up"]:
        top = "; ".join(f"«{r['query']}» {r['position_prev']} → {r['position']}" for r in compare["up"][:3])
        out.append(Finding("plus", f"RabbitSeo, позиции выросли: {plural(len(compare['up']), QUERIES)}, например {top}."))
    if compare["down"]:
        top = "; ".join(f"«{r['query']}» {r['position_prev']} → {r['position']}" for r in compare["down"][:3])
        out.append(Finding("minus", f"RabbitSeo, позиции просели: {plural(len(compare['down']), QUERIES)}, например {top}."))
    if compare["lost"]:
        top = ", ".join(f"«{r['query']}»" for r in compare["lost"][:3])
        out.append(Finding("check", f"RabbitSeo: выпали из выдачи {plural(len(compare['lost']), QUERIES)}, например {top} — "
                                    "проверить их страницы."))
    never = compare["never"]
    if never:
        demand = sum(r["frequency"] for r in never)
        top = ", ".join(f"«{r['query']}» ({fmt_int(r['frequency'])})" for r in never[:3])
        out.append(Finding("question", f"RabbitSeo: по {plural(len(never), QUERIES)} проекта сайт не найден ни разу "
                                       f"(суммарная частотность {fmt_int(demand)}), например {top}. Есть ли под них "
                                       "страницы и нужны ли они в проекте?"))
    return out


# ----- ПФ и риски -----
#
# Две группы сигналов. «Следы накрутки» — то, что Яндекс видит в поведении на выдаче и чем отличает ботов
# от людей: живые люди почти не кликают по второй странице и не дописывают домен к товарному запросу.
# «Признаки санкций» — то, как выглядит фильтр за накрутку: проседает именно Яндекс (Google — контроль)
# и позиции падают у многих запросов сразу.

FAR_CTR_ALERT = 20.0       # % CTR на позициях 11+ у запросов проекта; у живых людей там около 1 %
FAR_CTR_RATIO = 5.0        # во столько раз выше, чем у остальных запросов сайта
FAR_MIN_IMPRESSIONS = 10
FULL_CTR_WARN, FULL_CTR_ALERT = 3, 8      # запросов, где кликов почти столько же, сколько показов
DOMAIN_ALERT = 5           # запросов «запрос + домен»
SHARE_DROP_WARN, SHARE_DROP_ALERT = 5.0, 8.0   # п.п. доли Яндекса за неделю против 4 прошлых
ENGINE_FALL = -25.0        # % визитов из Яндекса за неделю — уже провал
MIN_WEEKLY = 100           # визитов из Яндекса и Google в неделю: меньше — доля скачет от случайностей
MASS_DROP_WARN, MASS_DROP_ALERT = 15.0, 30.0   # % запросов RabbitSeo из ТОП-30, выпавших или упавших на 10+
DROP_FROM = 30             # ниже 30-го места запросы и так то появляются, то пропадают — не считаем
FREQUENT = 100             # частотность, с которой запрос проекта считается частотным
MICRO = 20                 # и ниже которой — микрочастотным

SIGNAL_NAMES = {"alert": "Тревога", "warn": "Внимание", "ok": "В норме", "nodata": "Нет данных"}


@dataclass(frozen=True)
class Signal:
    status: str  # alert | warn | ok | nodata
    group: str   # traces — следы накрутки, sanctions — признаки санкций, benefit — отдача
    title: str
    text: str


def ratio_pct(clicks, impressions) -> float:
    return float(clicks or 0) / float(impressions) * 100 if impressions else 0.0


def pf_far_ctr(window: dict | None) -> Signal:
    title = "CTR запросов проекта на второй странице"
    if not window:
        return Signal("nodata", "traces", title, "Нет аналитики запросов Вебмастера.")
    b, o = window["boosted"] or {}, window["others"] or {}
    period = f"{window['first_day']:%d.%m}–{window['last_day']:%d.%m}"
    if (b.get("impressions_far") or 0) < FAR_MIN_IMPRESSIONS:
        return Signal("ok", "traces", title, f"За {period} у запросов проекта RabbitSeo почти нет показов на позициях 11+ — "
                                             "сравнивать нечего.")
    ctr_b, ctr_o = ratio_pct(b["clicks_far"], b["impressions_far"]), ratio_pct(o.get("clicks_far"), o.get("impressions_far"))
    text = (f"За {period} на позициях 11+ запросы проекта RabbitSeo получили CTR {fmt_pct(ctr_b)} "
            f"({fmt_int(b['clicks_far'])} кликов из {fmt_int(b['impressions_far'])} показов), остальные запросы сайта — "
            f"{fmt_pct(ctr_o, 2)}.")
    if ctr_b >= FAR_CTR_ALERT and ctr_b >= FAR_CTR_RATIO * max(ctr_o, 0.1):
        return Signal("alert", "traces", title, text + " Живые люди так по второй странице не кликают: для Яндекса это "
                                                       "прямой признак накрутки.")
    if ctr_b >= 2 * ctr_o and ctr_b >= 5:
        return Signal("warn", "traces", title, text + " Заметно выше обычного — стоит следить.")
    return Signal("ok", "traces", title, text)


def pf_full_ctr(window: dict | None) -> Signal:
    title = "Кликов столько же, сколько показов"
    if not window:
        return Signal("nodata", "traces", title, "Нет аналитики запросов Вебмастера.")
    rows = window["full_ctr"]
    n, boosted = len(rows), sum(1 for r in rows if r["boosted"])
    if not n:
        return Signal("ok", "traces", title, "Нет запросов, где почти каждый показ ниже 3-го места заканчивается кликом.")
    top = ", ".join(f"«{r['query']}» {r['clicks']}/{r['impressions']}" for r in rows[:3])
    text = (f"{plural(n, QUERIES).capitalize()} с CTR от 80% на позициях ниже 3-й (из них {boosted} — из проекта RabbitSeo), "
            f"например {top}. У живых людей CTR там 2–10%; почти каждый показ — это сам бот, который ищет и кликает.")
    status = "alert" if n >= FULL_CTR_ALERT else "warn" if n >= FULL_CTR_WARN else "ok"
    return Signal(status, "traces", title, text)


def brand_free(query: str, domain: str, brand_words: Iterable[str]) -> bool:
    """Запрос «… домен» без бренда: к брендовому запросу домен иногда дописывают и люди."""
    rest = query.lower().replace(domain.lower(), "")
    return not any(w in rest for w in brand_words)


def pf_domain(rows: list[dict], domain: str) -> Signal:
    title = f"Запросы «запрос + {domain}»"
    if not rows:
        return Signal("ok", "traces", title, f"Нет товарных запросов с дописанным доменом {domain}.")
    clicks, project = sum(r["clicks"] for r in rows), sum(1 for r in rows if r["from_project"])
    first = min(r["first_seen"] for r in rows)
    text = (f"{plural(len(rows), QUERIES).capitalize()} вида «{rows[0]['query']}» — {fmt_int(clicks)} кликов, "
            f"первые с {first:%d.%m}; {project} из них — запросы проекта RabbitSeo с дописанным доменом. Клиенты так "
            "не ищут: так добирается до сайта бот, который не нашёл его в выдаче.")
    return Signal("alert" if len(rows) >= DOMAIN_ALERT else "warn", "traces", title, text)


def pf_engine_share(weeks: list[dict]) -> Signal:
    """Последняя полная неделя против медианы 4 прошлых: медиана не даёт всплеску раздуть базу."""
    title = "Доля Яндекса против Google"
    if len(weeks) < 5:
        return Signal("nodata", "sanctions", title, "Нужно минимум 5 полных недель визитов по поисковым системам.")
    last, base = weeks[-1], weeks[-5:-1]
    y_base, g_base = median(w["yandex"] for w in base), median(w["google"] for w in base)
    if y_base + g_base < MIN_WEEKLY:
        return Signal("nodata", "sanctions", title, f"Из Яндекса и Google в среднем {fmt_int(y_base + g_base)} визитов "
                                                    "в неделю — слишком мало, чтобы судить по доле.")
    share = ratio_pct(last["yandex"], last["yandex"] + last["google"])
    base_share = ratio_pct(y_base, y_base + g_base)
    y_change = _pct_change(last["yandex"], y_base) or 0.0
    g_change = _pct_change(last["google"], g_base) or 0.0
    drop = base_share - share
    text = (f"Неделя с {last['week']:%d.%m}: Яндекс — {plural(last['yandex'], VISITS)} ({_signed(y_change)} к обычной "
            f"неделе, медиане 4 прошлых), Google — {fmt_int(last['google'])} ({_signed(g_change)}); доля Яндекса "
            f"{fmt_pct(share)} против {fmt_pct(base_share)}.")
    if drop >= SHARE_DROP_ALERT and y_change <= ENGINE_FALL and g_change > -10:
        return Signal("alert", "sanctions", title, text + " Проседает только Яндекс — так выглядит фильтр за накрутку ПФ. "
                                                          "Проверить позиции и сообщения в Вебмастере.")
    if y_change <= ENGINE_FALL and g_change <= ENGINE_FALL:
        return Signal("warn", "sanctions", title, text + " Упали оба поисковика — это не санкции Яндекса, а сезон, "
                                                         "праздники или проблема сайта.")
    if drop >= SHARE_DROP_WARN:
        return Signal("warn", "sanctions", title, text + " Яндекс теряет долю быстрее обычного — следить.")
    return Signal("ok", "sanctions", title, text + " Резких расхождений нет.")


def pf_rabbit_drop(rows: list[dict], now: dict | None, then: dict | None) -> Signal:
    """rows — запросы проекта с позицией на последний съём и неделей раньше."""
    title = "Массовая просадка позиций"
    base = [r for r in rows if r["position_prev"] is not None and r["position_prev"] <= DROP_FROM]
    if not now or not then or not base:
        return Signal("nodata", "sanctions", title, "Нет позиций RabbitSeo в ТОП-30 минимум за два съёма.")
    severe = [r for r in base if r["position"] is None or r["position"] - r["position_prev"] >= 10]
    top10 = sum(1 for r in rows if r["position"] is not None and r["position"] <= 10)
    top10_prev = sum(1 for r in base if r["position_prev"] <= 10)
    pct = len(severe) / len(base) * 100
    text = (f"{then['date']:%d.%m} → {now['date']:%d.%m}: в ТОП-{DROP_FROM} было {plural(len(base), QUERIES)}, "
            f"выпали или упали на 10+ позиций — {len(severe)} ({fmt_pct(pct)}); в ТОП-10 {top10} (было {top10_prev}).")
    if severe:
        text += " " + ", ".join(f"«{r['query']}» {r['position_prev']} → {r['position'] or '—'}" for r in severe[:3]) + "."
    if (pct >= MASS_DROP_ALERT and len(severe) >= 5) or (top10_prev >= 4 and top10 <= top10_prev / 2):
        return Signal("alert", "sanctions", title, text + " Так проседают позиции под фильтром — проверить Вебмастер "
                                                          "(«Безопасность и нарушения»).")
    if pct >= MASS_DROP_WARN and len(severe) >= 3:
        return Signal("warn", "sanctions", title, text)
    return Signal("ok", "sanctions", title, text + " Обычные колебания.")


def pf_benefit(rows: list[dict], then: dict | None) -> Signal:
    title = "Отдача по частотным запросам"
    frequent = [r for r in rows if r["frequency"] >= FREQUENT]
    if not frequent or not then:
        return Signal("nodata", "benefit", title, f"В проекте RabbitSeo нет запросов с частотностью от {FREQUENT}.")
    top10 = [r for r in frequent if r["position"] is not None and r["position"] <= 10]
    both = [r for r in frequent if r["position"] is not None and r["position_prev"] is not None]
    better = sum(1 for r in both if r["position"] < r["position_prev"])
    worse = sum(1 for r in both if r["position"] > r["position_prev"])
    absent = sum(1 for r in frequent if r["position"] is None)
    micro_top = [r for r in rows if r["frequency"] < MICRO and r["position"] is not None and r["position"] <= 3]
    leaders = ", ".join(f"«{r['query']}» — {r['position']}" for r in top10[:3]) or "нет ни одного"
    text = (f"Запросов проекта с частотностью от {FREQUENT} — {len(frequent)}, в ТОП-10 из них: {leaders}. "
            f"С {then['date']:%d.%m}: лучше — {better}, хуже — {worse}, не найдены — {absent}.")
    if micro_top:
        text += (f" В ТОП-3 зато {plural(len(micro_top), QUERIES)} с частотностью меньше {MICRO} в месяц — "
                 "трафика и заявок они не дают.")
    weak = len(top10) * 3 < len(frequent) and better <= worse
    return Signal("warn" if weak else "ok", "benefit", title, text)


def pf_verdict(signals: list[Signal]) -> tuple[str, str]:
    """Итог одной фразой: (статус, текст)."""
    sanctions = [s for s in signals if s.group == "sanctions" and s.status == "alert"]
    traces = [s for s in signals if s.group == "traces" and s.status in ("alert", "warn")]
    if sanctions:
        return "alert", ("Похоже на санкции Яндекса: " + "; ".join(s.title.lower() for s in sanctions)
                         + ". Остановить накрутку и проверить Вебмастер.")
    if traces:
        return "warn", (f"Санкций пока не видно, но следы накрутки заметны ({len(traces)} из 3 сигналов): "
                        "Яндекс отличает такое поведение от живого. Чем дольше оно продолжается, тем выше риск фильтра "
                        "на весь сайт.")
    return "ok", "Следов накрутки и признаков санкций не видно."



def pf_findings(signals: list[Signal]) -> list[Finding]:
    """Для сводок: тревоги — в минусы, «внимание» — в «что проверить»."""
    kind = {"alert": "minus", "warn": "check"}
    return [Finding(kind[s.status], f"ПФ: {s.title.lower()} — {s.text}") for s in signals if s.status in kind]
