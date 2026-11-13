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


# ----- Новые отчёты: 404, поиск по сайту, Директ, лояльность, товары -----

NOT_FOUND_SHARE = 2.0  # % визитов со страницей 404, с которого это уже проблема
AUTOTARGET = "автотаргетинг"


def short_url(url: str, keep: int = 70) -> str:
    path = url.split("://", 1)[-1].partition("/")[2] if "://" in url else url
    path = "/" + path.lstrip("/")
    return path if len(path) <= keep else "…" + path[-(keep - 1):]


def referer_hosts(referers: list[dict], domain: str) -> tuple[list[tuple[str, int]], bool]:
    """Источники переходов на 404: внешние — по сайту (google.com), свои — по странице. Второе — внешних большинство."""
    from urllib.parse import urlsplit

    grouped: dict[str, int] = {}
    outside = inside = 0
    for r in referers:
        if not r["key"]:
            continue
        host = urlsplit(r["key"]).netloc.removeprefix("www.")
        own = bool(domain) and host.endswith(domain)
        label = short_url(r["key"], 50) if own else host or r["key"]
        grouped[label] = grouped.get(label, 0) + r["pageviews"]
        if own:
            inside += r["pageviews"]
        else:
            outside += r["pageviews"]
    return sorted(grouped.items(), key=lambda kv: -kv[1]), outside > inside


def not_found_findings(nf: dict, total_visits: int, urls: list[dict], referers: list[dict],
                       domain: str = "") -> list[Finding]:
    visits = nf.get("visits") or 0
    if not visits or not total_visits:
        return []
    share = visits / total_visits * 100
    top = "; ".join(f"{short_url(r['key'], 50)} — {fmt_int(r['pageviews'])}" for r in urls[:3])
    text = (f"«Страница не найдена» была в {plural(visits, VISITS)} — {fmt_pct(share)} всех визитов. "
            f"Чаще всего: {top}.")
    if share < NOT_FOUND_SHARE:
        return [Finding("plus", text + " Это немного.")]
    out = [Finding("minus", text)]
    hosts, external = referer_hosts(referers, domain)
    if hosts:
        listed = ", ".join(f"{h} {fmt_int(v)}" for h, v in hosts[:4])
        if external:
            out.append(Finding("question", f"На несуществующие адреса переходят «с» {listed} — не со страниц сайта. "
                                           "Похоже на ботов с поддельным источником."))
        else:
            out.append(Finding("check", f"Откуда переходят на несуществующие адреса: {listed}. Исправить ссылки на этих "
                                        "страницах, со старых адресов поставить 301-переадресацию."))
    out.append(Finding("check", "Посмотреть в Вебвизоре визиты на эти адреса: если это люди — чинить срочно, "
                                "если боты — включить строгую фильтрацию роботов в настройках счётчика."))
    return out


def site_search_findings(rows: list[dict]) -> list[Finding]:
    rows = [r for r in rows if r["key"].strip()]
    if not rows:
        return []
    top = ", ".join(f"«{r['key']}» ({fmt_int(r['users'])})" for r in rows[:6])
    out = [Finding("question", f"Что ищут поиском по сайту чаще всего (посетителей): {top}. Это то, что не нашли "
                               "в меню: есть ли по этим словам понятные разделы и фильтры?")]
    grown = [r for r in rows[:30] if r["pageviews_prev"] and r["pageviews"] >= 30
             and r["pageviews"] >= 2 * r["pageviews_prev"]]
    if grown:
        out.append(Finding("check", "Поиск по сайту: интерес вырос вдвое и больше — "
                                    + ", ".join(f"«{r['key']}» {fmt_int(r['pageviews_prev'])} → {fmt_int(r['pageviews'])}"
                                                for r in grown[:4]) + ". Проверить наличие и цены."))
    return out


def direct_findings(campaigns: list[dict], phrases: list[dict], site_conv: float) -> list[Finding]:
    total = sum(r["visits"] for r in campaigns)
    if total < MIN_VISITS:
        return []
    out = []
    leads = sum(r["leads"] or 0 for r in campaigns)
    conv = leads / total * 100
    kind = "plus" if conv >= site_conv else "minus"
    out.append(Finding(kind, f"Директ: {plural(total, VISITS)}, {plural(leads, LEADS)}, конверсия {fmt_pct(conv, 2)} "
                             f"(по сайту {fmt_pct(site_conv, 2)})."))
    auto = [r for r in phrases if AUTOTARGET in r["dim_label"].lower()]
    auto_visits = sum(r["visits"] for r in auto)
    if auto_visits >= total * 0.6:
        auto_leads = sum(r["leads"] or 0 for r in auto)
        out.append(Finding("check", f"Директ: автотаргетинг — {fmt_pct(auto_visits / total * 100)} визитов из Директа "
                                    f"({plural(auto_leads, LEADS)}). Посмотреть в Директе поисковые запросы автотаргетинга, "
                                    "мусорные — в минус-слова, удачные — отдельными фразами."))
    idle = [r for r in campaigns if r["visits"] >= 100 and not r["leads"]]
    for r in idle[:2]:
        out.append(Finding("minus", f"Директ: кампания {r['dim_label']} — {plural(r['visits'], VISITS)} и ни одной заявки."))
    best = [r for r in phrases if r["leads"] and r["leads"] >= 2 and AUTOTARGET not in r["dim_label"].lower()]
    if best:
        out.append(Finding("plus", "Директ: фразы с заявками — " + ", ".join(
            f"«{r['dim_label']}» ({plural(r['leads'], LEADS)})" for r in sorted(best, key=lambda r: -r["leads"])[:3])
            + ". Их стоит усилить."))
    return out


def loyalty_findings(rows: list[dict]) -> list[Finding]:
    """rows — отчёт «какой по счёту визит»: доля заявок с первого визита и с повторных."""
    first = next((r for r in rows if r["dim_key"] == "1"), None)
    total_leads = sum(r["leads"] or 0 for r in rows)
    if not first or total_leads < 10:
        return []
    repeat = total_leads - (first["leads"] or 0)
    share = repeat / total_leads * 100
    visits = sum(r["visits"] for r in rows)
    visit_share = (visits - first["visits"]) / visits * 100 if visits else 0
    if share >= 30:
        return [Finding("question", f"{fmt_pct(share)} заявок приходят со 2-го визита и позже, хотя повторных визитов "
                                    f"{fmt_pct(visit_share)}. Решение принимают не сразу — есть ли ретаргетинг в Директе "
                                    "и способ вернуться (прайс на почту, менеджер)?")]
    return [Finding("plus", f"{fmt_pct(100 - share)} заявок оставляют в первый же визит.")]


def products_findings(products: list[dict], totals: dict | None, cart_visits: int | None,
                      purchase_visits: int | None) -> list[Finding]:
    out = []
    if cart_visits and cart_visits >= 20:
        conv = (purchase_visits or 0) / cart_visits * 100
        text = (f"Корзина → заказ: визитов с добавлением в корзину — {fmt_int(cart_visits)}, с оформленным заказом — "
                f"{fmt_int(purchase_visits or 0)} ({fmt_pct(conv)}).")
        out.append(Finding("minus" if conv < 10 else "plus", text))
    if totals and totals.get("basket_sum"):
        lost = [p for p in products if p["basket_sum"] >= 20000 and not p["purchased_sum"]]
        if lost:
            out.append(Finding("check", "Кладут в корзину, но не покупают: " + "; ".join(
                f"{p['product'][:60]} — в корзине на {fmt_int(p['basket_sum'])} ₽" for p in lost[:3])
                + ". Проверить цену, наличие, минимальную партию."))
    return out


# ----- Рекомендации: что сделать, чтобы росли трафик и заявки -----

PRIORITIES = {1: "Срочно", 2: "Важно", 3: "Стоит сделать"}


@dataclass(frozen=True)
class Recommendation:
    priority: int  # 1 — срочно, 2 — важно, 3 — стоит сделать
    area: str
    title: str
    action: str
    why: str


def recommend(d: dict) -> list[Recommendation]:
    """Рекомендации по данным месяца. d — заготовки разделов (см. pages.recommendations):
    total, conv, pf, nf, not_found, not_found_from, site_search, source, direct_campaign, direct_phrase,
    landing_section, device, visit_number, country, shop.
    """
    out: list[Recommendation] = []
    total, conv = d.get("total") or 0, d.get("conv") or 0.0

    nf_visits = (d.get("nf") or {}).get("visits") or 0
    if total and nf_visits / total * 100 >= NOT_FOUND_SHARE:
        urls = ", ".join(short_url(r["key"], 60) for r in d.get("not_found", [])[:3])
        refs, external = referer_hosts(d.get("not_found_from", []), d.get("domain") or "")
        why = (f"{plural(nf_visits, VISITS)} ({fmt_pct(nf_visits / total * 100)} всех) видели страницу 404. "
               f"Чаще всего: {urls}.")
        if refs:
            why += " Переходят на них с: " + ", ".join(f"{h} {fmt_int(v)}" for h, v in refs[:4]) + "."
        leads = d.get("leads") or 0
        if external:
            clean = leads / (total - nf_visits) * 100 if total > nf_visits else 0
            out.append(Recommendation(
                1, "Сайт", "Разобраться с визитами на несуществующие адреса",
                "Похоже на ботов с поддельным источником: на несуществующие адреса приходят «из» поисковиков и порталов, "
                "а не со страниц сайта. Открыть в Вебвизоре визиты на эти адреса; если это боты — включить в настройках "
                "счётчика фильтрацию роботов «по строгим правилам и по поведению» и закрыть эти адреса на сервере; "
                "если люди — поставить 301-переадресацию на нужные разделы.",
                why + f" Они искажают отчёты: без них конверсия сайта — около {fmt_pct(clean, 2)}, а не "
                      f"{fmt_pct(conv, 2)}, и доли каналов другие."))
        else:
            out.append(Recommendation(
                1, "Сайт", "Убрать «Страница не найдена»",
                "Исправить ссылки на страницах, с которых переходят на несуществующие адреса; со старых адресов "
                "поставить 301-переадресацию на нужные разделы.", why))

    pf = d.get("pf")
    if pf and pf["status"] != "ok":
        out.append(Recommendation(
            1 if pf["status"] == "alert" else 2, "Поиск", "Решить судьбу накрутки ПФ",
            "Остановить накрутку или хотя бы убрать из проекта запросы, где сайт уже в ТОП-3, и заходы «запрос + домен».",
            pf["verdict"]))

    zero = [r for r in d.get("source", []) if not is_undefined(r) and total and r["visits"] / total * 100 >= 5
            and not r["leads"]]
    for r in zero[:2]:
        out.append(Recommendation(
            2, "Каналы", f"Разобраться с каналом «{r['dim_label']}»",
            "Посмотреть, какие площадки и страницы дают этот трафик; если это реклама или посевы — перенастроить на "
            "целевую аудиторию или перераспределить бюджет.",
            f"{plural(r['visits'], VISITS)} ({fmt_pct(r['visits'] / total * 100)}) и ни одной заявки."))

    campaigns, phrases = d.get("direct_campaign", []), d.get("direct_phrase", [])
    direct = sum(r["visits"] for r in campaigns)
    auto = [r for r in phrases if AUTOTARGET in r["dim_label"].lower()]
    auto_visits, auto_leads = sum(r["visits"] for r in auto), sum(r["leads"] or 0 for r in auto)
    if direct >= MIN_VISITS and auto_visits >= direct * 0.6:
        auto_conv = auto_leads / auto_visits * 100 if auto_visits else 0
        if auto_conv < conv:
            out.append(Recommendation(
                2, "Директ", "Сузить автотаргетинг",
                "В Директе выгрузить поисковые запросы автотаргетинга: нецелевые — в минус-слова, удачные — "
                "отдельными фразами со своей ставкой.",
                f"Автотаргетинг — {fmt_pct(auto_visits / direct * 100)} визитов из Директа, конверсия "
                f"{fmt_pct(auto_conv, 2)} при {fmt_pct(conv, 2)} по сайту."))
    for r in [c for c in campaigns if c["visits"] >= 100 and not c["leads"]][:2]:
        out.append(Recommendation(
            2, "Директ", f"Кампания {r['dim_label']} без заявок",
            "Проверить посадочные страницы, гео и ключевые фразы кампании; без изменений — отключить.",
            f"{plural(r['visits'], VISITS)} за месяц и ни одной заявки."))

    weak = [r for r in d.get("landing_section", []) if r["visits"] >= 300 and conv
            and (r["conversion_pct"] or 0) < conv * 0.5]
    broken = [r["key"] for r in d.get("not_found", [])[:10]]
    with_404 = [r for r in weak if any(u.startswith(r["dim_label"]) for u in broken)]
    if with_404:
        out.append(Recommendation(
            2, "Каталог", "Пересчитать разделы без визитов на 404",
            "Сначала разобраться со «Страница не найдена»: в этих разделах лежат битые адреса, и их визиты занижают "
            "конверсию раздела. Потом смотреть, нужно ли дорабатывать сам раздел.",
            "; ".join(f"{short_url(r['dim_label'], 50)} — {plural(r['visits'], VISITS)}, конверсия "
                      f"{fmt_pct(r['conversion_pct'] or 0, 2)}" for r in with_404[:3]) + "."))
        weak = [r for r in weak if r not in with_404]
    if weak:
        out.append(Recommendation(
            2, "Каталог", "Доработать разделы с трафиком без заявок",
            "На этих страницах вывести цены или «от …», наличие, минимальную партию, кнопку «Запросить прайс» "
            "и телефон на виду; проверить в Вебвизоре и на карте скроллинга, куда доходят посетители.",
            "; ".join(f"{short_url(r['dim_label'], 50)} — {plural(r['visits'], VISITS)}, конверсия "
                      f"{fmt_pct(r['conversion_pct'] or 0, 2)}" for r in weak[:3]) + f" (по сайту {fmt_pct(conv, 2)})."))

    devices = {r["dim_key"]: r for r in d.get("device", [])}
    desktop = devices.get("desktop")
    mobile = [r for k, r in devices.items() if k in MOBILE_KEYS]
    m_visits = sum(r["visits"] for r in mobile)
    m_leads = sum(r["leads"] or 0 for r in mobile)
    if desktop and total and m_visits / total >= 0.3 and desktop["conversion_pct"]:
        m_conv = m_leads / m_visits * 100 if m_visits else 0
        if m_conv < float(desktop["conversion_pct"]) * 0.6:
            out.append(Recommendation(
                2, "Сайт", "Упростить путь до заявки с телефона",
                "Пройти с телефона путь «каталог → товар → заявка/корзина»: кликабельный номер и мессенджеры "
                "на виду, короткая форма, скорость загрузки каталога.",
                f"С телефонов {fmt_pct(m_visits / total * 100)} визитов, конверсия {fmt_pct(m_conv, 2)} против "
                f"{fmt_pct(desktop['conversion_pct'], 2)} с компьютеров."))

    shop = d.get("shop") or {}
    cart, purchase = shop.get("cart_visits"), shop.get("purchase_visits") or 0
    if cart and cart >= 20 and purchase / cart * 100 < 10:
        out.append(Recommendation(
            2, "Магазин", "Довести корзину до заказа",
            "Упростить оформление (меньше полей, без обязательной регистрации), показать условия доставки и "
            "минимальной партии до корзины, настроить письмо о брошенной корзине; проверить корзину с телефона.",
            f"Визитов с добавлением в корзину — {fmt_int(cart)}, с оформленным заказом — {fmt_int(purchase)} "
            f"({fmt_pct(purchase / cart * 100)})."))

    searches = [r for r in d.get("site_search", []) if r["key"].strip()]
    if searches:
        out.append(Recommendation(
            3, "Каталог", "Вынести в меню то, что ищут поиском",
            "Проверить, что поиск находит товары по частым запросам; вынести их в меню и фильтры, под самые частые — "
            "сделать посадочные страницы (они же пойдут в SEO и Директ).",
            "Чаще всего ищут: " + ", ".join(f"«{r['key']}»" for r in searches[:8]) + "."))

    visits_by_number = d.get("visit_number", [])
    first = next((r for r in visits_by_number if r["dim_key"] == "1"), None)
    all_leads = sum(r["leads"] or 0 for r in visits_by_number)
    if first and all_leads >= 10 and (all_leads - (first["leads"] or 0)) / all_leads >= 0.3:
        out.append(Recommendation(
            3, "Реклама", "Возвращать тех, кто не решился сразу",
            "Ретаргетинг в Директе на посетителей каталога без заявки (7–30 дней), сбор контактов за прайс или "
            "образцы, рассылка с новинками.",
            f"{fmt_pct((all_leads - (first['leads'] or 0)) / all_leads * 100)} заявок — со 2-го визита и позже."))

    foreign = [r for r in d.get("country", []) if r["dim_label"] not in TARGET_COUNTRIES and not is_undefined(r)]
    f_visits = sum(r["visits"] for r in foreign)
    if total and f_visits / total * 100 >= MIN_SHARE:
        out.append(Recommendation(
            3, "Трафик", "Отсечь нецелевые страны",
            "Проверить эти визиты в Вебвизоре; если это боты — включить строгую фильтрацию роботов в настройках "
            "счётчика и исключить страны в рекламе.",
            f"{plural(f_visits, VISITS)} ({fmt_pct(f_visits / total * 100)}) из стран без продаж: "
            + ", ".join(f"{r['dim_label']} {fmt_int(r['visits'])}" for r in sorted(foreign, key=lambda r: -r['visits'])[:3])
            + "."))
    return sorted(out, key=lambda r: r.priority)


# ----- Продажи: Битрикс24 и 1С -----

# Стадии лидов Битрикс24 у Олдос: нецелевые, ещё не разобранные и переданные в сделку.
NONTARGET_STAGES = {"Нецелевое обращение"}
UNPROCESSED_STAGES = {"Не обработан", "Заявки с сайта", "Новый", "Новая заявка"}
TO_DEAL_STAGES = {"Перевести в сделку"}
CLOSED_STATUSES = {"Закрыт", "Готов к закрытию"}
MISSED = "Пропущенный входящий"
MISSED_SHARE, UNPROCESSED_SHARE, NONTARGET_SHARE = 15.0, 20.0, 30.0


def crm_stats(forms: list[dict], calls: list[dict], orders: list[dict]) -> dict:
    """Сводка месяца по выгрузкам: формы, звонки и заказы 1С."""
    def count(rows, pred):
        return sum(1 for r in rows if pred(r))

    target_forms = [r for r in forms if r["stage"] not in NONTARGET_STAGES]
    new_orders = [o for o in orders if o["category"] == "new"]
    existing = [o for o in orders if o["category"] == "existing"]
    return {
        "forms": len(forms),
        "forms_target": len(target_forms),
        "forms_nontarget": len(forms) - len(target_forms),
        "forms_unprocessed": count(forms, lambda r: r["stage"] in UNPROCESSED_STAGES),
        "forms_to_deal": count(forms, lambda r: r["stage"] in TO_DEAL_STAGES),
        "forms_in_1c": count(forms, lambda r: r["in_1c"]),
        "forms_target_in_1c": count(target_forms, lambda r: r["in_1c"]),
        "forms_orders": sum(r["orders"] or 0 for r in forms),
        "forms_amount": float(sum(r["amount"] or 0 for r in forms)),
        "forms_closed": float(sum(r["closed"] or 0 for r in forms)),
        "calls": len(calls),
        "calls_missed": count(calls, lambda r: r["call_type"] == MISSED),
        "calls_missed_unprocessed": count(calls, lambda r: r["call_type"] == MISSED and r["stage"] in UNPROCESSED_STAGES),
        "calls_unprocessed": count(calls, lambda r: r["stage"] in UNPROCESSED_STAGES),
        "calls_nontarget": count(calls, lambda r: r["stage"] in NONTARGET_STAGES),
        "calls_to_deal": count(calls, lambda r: r["stage"] in TO_DEAL_STAGES),
        "calls_in_1c": count(calls, lambda r: r["in_1c"]),
        "new_sales": len({o["lead_id"] or o["order_no"] for o in new_orders}),
        "new_orders": len(new_orders),
        "new_amount": float(sum(o["amount"] for o in new_orders)),
        "existing_orders": len(existing),
        "existing_amount": float(sum(o["amount"] for o in existing)),
    }


def _share(part, whole) -> float:
    return part / whole * 100 if whole else 0.0


def crm_findings(s: dict, prev: dict | None, lead_visits: int | None) -> list[Finding]:
    out = []
    if s["forms"]:
        conv = _share(s["forms_target_in_1c"], s["forms_target"])
        out.append(Finding("plus" if s["forms_amount"] else "minus",
                           f"Лиды с сайта: {plural(s['forms'], LEADS_CRM)}, целевых {s['forms_target']}; заказ в 1С сделали "
                           f"{s['forms_in_1c']} ({fmt_pct(conv)} целевых) — {plural(s['forms_orders'], ORDERS)} на "
                           f"{fmt_int(s['forms_amount'])} ₽, из них закрыто на {fmt_int(s['forms_closed'])} ₽."))
        if prev and prev.get("forms_target"):
            was = _share(prev["forms_target_in_1c"], prev["forms_target"])
            if abs(conv - was) >= 3:
                out.append(Finding("plus" if conv > was else "minus",
                                   f"Конверсия целевых лидов с сайта в заказ: {fmt_pct(conv)} против {fmt_pct(was)} "
                                   "месяцем раньше."))
        share = _share(s["forms_nontarget"], s["forms"])
        if share >= NONTARGET_SHARE:
            out.append(Finding("question", f"{fmt_pct(share)} лидов с форм и почты — нецелевые (поставщики, спам, "
                                           "предложения услуг). Они раздувают число заявок; можно ли отсекать их до CRM?"))
    if s["calls"]:
        missed = _share(s["calls_missed"], s["calls"])
        if missed >= MISSED_SHARE:
            out.append(Finding("minus", f"Пропущено {plural(s['calls_missed'], CALLS)} из {s['calls']} входящих "
                                        f"({fmt_pct(missed)}); по {s['calls_missed_unprocessed']} так и нет решения в Б24."))
        if s["new_sales"]:
            out.append(Finding("plus", f"Звонки дали {plural(s['new_sales'], NEW_CLIENTS)} с заказами на "
                                       f"{fmt_int(s['new_amount'])} ₽; действующие клиенты после звонков заказали ещё на "
                                       f"{fmt_int(s['existing_amount'])} ₽."))
    unprocessed = s["forms_unprocessed"] + s["calls_unprocessed"]
    total = s["forms"] + s["calls"]
    if total and _share(unprocessed, total) >= UNPROCESSED_SHARE:
        out.append(Finding("minus", f"Не разобрано {plural(unprocessed, LEADS_CRM)} из {total} ({fmt_pct(_share(unprocessed, total))}): "
                                    f"звонков {s['calls_unprocessed']}, заявок с сайта {s['forms_unprocessed']}. "
                                    "Без стадии не посчитать, какие каналы приносят продажи."))
    if lead_visits and s["forms"]:
        out.append(Finding("question", f"Метрика насчитала {plural(lead_visits, VISITS)} с заявкой (формы, клики по "
                                       f"телефону и почте, заказы), в Б24 — {plural(s['forms'], LEADS_CRM)} с форм и почты и "
                                       f"{plural(s['calls'], CALLS)}. Все ли формы сайта создают лид в Б24?"))
    return out


LEADS_CRM = ("лид", "лида", "лидов")
ORDERS = ("заказ", "заказа", "заказов")
CALLS = ("звонок", "звонка", "звонков")
NEW_CLIENTS = ("нового клиента", "новых клиентов", "новых клиентов")


def crm_recommend(s: dict | None) -> list[Recommendation]:
    if not s:
        return []
    out = []
    if s["calls"] and _share(s["calls_missed"], s["calls"]) >= MISSED_SHARE:
        out.append(Recommendation(
            1, "Продажи", "Перезванивать пропущенные звонки",
            "Включить в Битрикс24 задачу на каждый пропущенный звонок с перезвоном в течение 15 минут; проверить, на какие "
            "линии и в какие часы звонки теряются, и закрыть их переадресацией или графиком.",
            f"Пропущено {plural(s['calls_missed'], CALLS)} из {s['calls']} входящих ({fmt_pct(_share(s['calls_missed'], s['calls']))}), "
            f"по {s['calls_missed_unprocessed']} нет решения. Звонки — главный путь к заказу: каждая новая продажа "
            f"со звонка в среднем на {fmt_int(s['new_amount'] / s['new_sales']) if s['new_sales'] else '—'} ₽."))
    unprocessed, total = s["forms_unprocessed"] + s["calls_unprocessed"], s["forms"] + s["calls"]
    if total and _share(unprocessed, total) >= UNPROCESSED_SHARE:
        out.append(Recommendation(
            1, "Продажи", "Разбирать лиды до конца",
            "Договориться о правиле: каждый лид за день получает стадию — в сделку или нецелевой; в конце недели — "
            "список неразобранных по менеджерам (таблица в разделе «Продажи»).",
            f"Не разобрано {plural(unprocessed, LEADS_CRM)} из {total}: звонков {s['calls_unprocessed']}, заявок с сайта "
            f"{s['forms_unprocessed']}. Часть из них — потерянные клиенты, и без стадий не посчитать отдачу каналов."))
    nontarget = s["forms_nontarget"] + s["calls_nontarget"]
    if total and _share(nontarget, total) >= NONTARGET_SHARE:
        out.append(Recommendation(
            3, "Продажи", "Отсекать нецелевые обращения до CRM",
            "Поставщикам и рекламе услуг дать отдельный адрес почты и форму; на формы сайта — проверку от спама; "
            "в Битрикс24 — правила, которые сразу помечают типовой спам.",
            f"Нецелевых {fmt_pct(_share(nontarget, total))}: с форм и почты {s['forms_nontarget']}, звонков "
            f"{s['calls_nontarget']} — менеджеры тратят на них время."))
    return out
