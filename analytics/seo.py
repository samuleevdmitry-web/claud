"""Ассистент SEO: ситуация по ключам, рекомендации и план продвижения выбранных ключей, оценка внедрённого.

Методика — seo-research (seo-aeo-geo-ultimate), переведённая в правила по данным, которые собирает сборщик:
- две дорожки: ближняя (просевшие, почти в ТОПе, сниппет и CTR, каннибализация, нет страницы в выдаче, защита ТОПа)
  и лидерство — самые востребованные запросы рынка с целью ТОП-3 (1-е место — сверхцель); трудный ведущий запрос
  не выпадает из плана, а получает долгий план с вехами;
- очередь: сейчас / следом / позже; «отложить» — только решение владельца (кнопка на странице), а не «трудно»;
- наблюдение (что видно в данных — с источником и датами) отдельно от вывода (гипотеза, которую проверит внедрение);
- у запроса одна страница-владелец; каннибализация — только если Яндекс по запросу действительно показывал разные
  страницы сайта, а не потому что в адресах общее слово;
- никакого единого «балла ключа»: спрос, показы, позиция, CTR и страница — отдельными полями; порядок в списке —
  только сортировка для удобства;
- позиции не обещаются: цель и вехи — то, что измеряем, а не прогноз.

Выдачу Яндекса ассистент сам не смотрит: шаг «разбор выдачи» в плане делает человек и записывает дату и регион.
"""

from __future__ import annotations

import json
import statistics
import tomllib
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from urllib.parse import urlsplit

import psycopg

from analytics.format import fmt_dec, fmt_int, fmt_pct
from analytics.keywords import load_keywords
from analytics.sources.semantics import normalize

WINDOW = 28           # дней в окне сравнения: последние 28 дней данных против 28 до них
MIN_IMP = 30          # показов за окно, чтобы судить о позиции запроса по Вебмастеру
CTR_MIN_IMP = 100     # показов, чтобы судить о CTR
CTR_SHARE = 0.5       # CTR ниже половины обычного для сайта на тех же позициях — слабый сниппет
LOST_POS = 3.0        # просел на 3+ позиции
LOST_SHARE = 0.5      # или показов вдвое меньше при том же спросе
SWITCH_DAYS = 3       # страница была в выдаче по запросу хотя бы 3 дня — это смена страницы, а не случайность
HEAD_TERMS = 5        # ведущих запросов в дорожке лидерства
LAG_DAYS = 7          # после внедрения неделя — на переобход и переиндексацию
JUDGE_DAYS = 14       # судить о результате — по 14+ дням данных после этой недели

LANES = {
    "lost": "Просел",
    "consolidate": "Каннибализация",
    "ctr": "Сниппет и CTR",
    "near_win": "Почти в ТОПе",
    "gap": "Нет страницы в выдаче",
    "defend": "Защита ТОПа",
    "head_term": "Ведущий запрос",
    "strategic": "Долгая работа",
}
TRACK_OF = {"head_term": "lead", "strategic": "lead"}
TRACKS = {"near": "Ближняя дорожка", "lead": "Лидерство"}
PRIORITIES = {"now": "Сейчас", "next": "Следом", "later": "Позже"}
LIMITS = {"lost": 15, "consolidate": 10, "ctr": 10, "near_win": 25, "gap": 15, "defend": 10, "head_term": HEAD_TERMS,
          "strategic": 10}
STATUSES = {"open": "в работе", "done": "внедрена", "dismissed": "отложена владельцем", "stale": "неактуальна"}
MILESTONES = "Вехи: ТОП-20 → ТОП-10 → ТОП-3 (цель); 1-е место — сверхцель. Это ориентиры для измерения, не прогноз."


@dataclass
class Key:
    norm: str
    phrase: str
    imp: int = 0
    clicks: int = 0
    pos: float | None = None
    demand_day: float | None = None
    demand_day_prev: float | None = None
    days: int = 0
    imp_prev: int = 0
    clicks_prev: int = 0
    pos_prev: float | None = None
    page: str | None = None
    pages: list[dict] = field(default_factory=list)  # {url, days, imp, pos} — какие страницы Яндекс показывал
    in_semantics: bool = False
    freq_year: int | None = None
    sheet_pos: int | None = None
    sheet_date: date | None = None
    in_rabbit: bool = False
    rabbit_pos: int | None = None
    rabbit_date: date | None = None
    rabbit_prev: int | None = None
    rabbit_freq: int | None = None
    focus: bool = False
    target: int = 3
    brand: bool = False

    @property
    def ctr(self) -> float | None:
        return 100.0 * self.clicks / self.imp if self.imp else None

    @property
    def demand(self) -> tuple[int | None, str]:
        """Спрос в месяц и откуда он: Вебмастер (спрос в Яндексе в день × 30), таблица семантики (за год / 12),
        RabbitSeo. Разные источники считают по-разному — источник всегда рядом с числом."""
        if self.demand_day:
            return round(self.demand_day * 30), "Вебмастер"
        if self.freq_year:
            return round(self.freq_year / 12), "семантика"
        if self.rabbit_freq:
            return self.rabbit_freq, "RabbitSeo"
        return None, ""

    @property
    def position(self) -> float | None:
        """Позиция для решений: Вебмастер, если показов достаточно; иначе RabbitSeo; иначе Вебмастер по малым данным."""
        if self.pos is not None and self.imp >= MIN_IMP:
            return self.pos
        if self.rabbit_pos is not None:
            return float(self.rabbit_pos)
        return self.pos

    @property
    def tracked(self) -> bool:
        return self.in_semantics or self.in_rabbit or self.focus


@dataclass
class Snapshot:
    host_id: str
    project: str
    first: date | None
    last: date | None
    keys: dict[str, Key]
    rabbit_last: date | None = None
    sheet_last: date | None = None
    days_cur: int = 0      # дней с данными Вебмастера в окне (сборщик мог не работать — тогда меньше 28)
    days_prev: int = 0

    @property
    def steady(self) -> float | None:
        """Во сколько раз показов было бы больше в текущем окне при том же спросе — поправка на дни без данных.
        None — в прошлом окне меньше двух недель данных, сравнивать показы нельзя."""
        return self.days_cur / self.days_prev if self.days_prev >= 14 and self.days_cur else None

    @property
    def d1(self) -> date | None:
        return self.last - timedelta(days=WINDOW - 1) if self.last else None

    @property
    def p1(self) -> date | None:
        return self.last - timedelta(days=2 * WINDOW - 1) if self.last else None

    @property
    def p2(self) -> date | None:
        return self.last - timedelta(days=WINDOW) if self.last else None


@dataclass
class Rec:
    key: str
    lane: str
    priority: str
    norm: str | None
    phrase: str | None
    page: str | None
    title: str
    observed: str
    inferred: str
    actions: list[str]
    acceptance: str
    baseline: dict
    focus: bool = False

    @property
    def track(self) -> str:
        return TRACK_OF.get(self.lane, "near")


# ----- данные -----

_NORM = r"regexp_replace(replace(lower(query), 'ё', 'е'), '\s+', ' ', 'g')"


def project_of(host_id: str) -> str:
    """'https:www.oldos.ru:443' → 'oldos.ru' (так называется проект RabbitSeo)."""
    parts = host_id.split(":")
    return (parts[1] if len(parts) > 1 else host_id).removeprefix("www.")


def brand_words(wordstat_path: str, domain: str) -> set[str]:
    """Слова бренда: имя домена и первые слова брендовых фраз Вордстата («олдос», «oldos»)."""
    words = {domain.split(".")[0].lower()} if domain else set()
    try:
        config = load_keywords(wordstat_path)
    except (FileNotFoundError, tomllib.TOMLDecodeError):
        return words
    return words | {k.phrase.split()[0].lower() for k in config.keywords if k.kind == "brand"}


def collect(conn: psycopg.Connection, host_id: str, with_semantics: bool = True) -> Snapshot:
    """Всё, что известно о ключах сайта: Вебмастер за 2 окна по 28 дней, таблица семантики (у основного сайта),
    RabbitSeo, продвигаемые ключи."""
    project = project_of(host_id)
    first, last = conn.execute("SELECT min(date), max(date) FROM webmaster_query_daily WHERE host_id = %s",
                               (host_id,)).fetchone()
    snap = Snapshot(host_id, project, first, last, {})
    keys = snap.keys

    def key(norm: str, phrase: str) -> Key:
        norm = norm.strip()
        if norm not in keys:
            keys[norm] = Key(norm, phrase.strip())
        return keys[norm]

    if last:
        params = {"h": host_id, "p1": snap.p1, "d1": snap.d1, "d2": last}
        snap.days_cur, snap.days_prev = conn.execute("""
            SELECT count(DISTINCT date) FILTER (WHERE date >= %(d1)s), count(DISTINCT date) FILTER (WHERE date < %(d1)s)
            FROM webmaster_query_daily WHERE host_id = %(h)s AND date BETWEEN %(p1)s AND %(d2)s
        """, params).fetchone()
        rows = conn.execute(f"""
            WITH w AS (
                SELECT {_NORM} AS norm, query, date, top_url, impressions, clicks, position, demand
                FROM webmaster_query_daily WHERE host_id = %(h)s AND date BETWEEN %(p1)s AND %(d2)s
            )
            SELECT norm, min(query),
                   coalesce(sum(impressions) FILTER (WHERE date >= %(d1)s), 0),
                   coalesce(sum(clicks) FILTER (WHERE date >= %(d1)s), 0),
                   sum(position * impressions) FILTER (WHERE date >= %(d1)s AND position IS NOT NULL)
                       / nullif(sum(impressions) FILTER (WHERE date >= %(d1)s AND position IS NOT NULL), 0),
                   avg(demand) FILTER (WHERE date >= %(d1)s), avg(demand) FILTER (WHERE date < %(d1)s),
                   count(*) FILTER (WHERE date >= %(d1)s AND impressions > 0),
                   coalesce(sum(impressions) FILTER (WHERE date < %(d1)s), 0),
                   coalesce(sum(clicks) FILTER (WHERE date < %(d1)s), 0),
                   sum(position * impressions) FILTER (WHERE date < %(d1)s AND position IS NOT NULL)
                       / nullif(sum(impressions) FILTER (WHERE date < %(d1)s AND position IS NOT NULL), 0),
                   (array_agg(top_url ORDER BY date DESC) FILTER (WHERE top_url IS NOT NULL))[1]
            FROM w GROUP BY norm
        """, params).fetchall()
        for (norm, phrase, imp, clicks, pos, dem, dem_prev, days, imp_prev, clicks_prev, pos_prev, page) in rows:
            k = key(norm, phrase)
            k.imp, k.clicks, k.days, k.imp_prev, k.clicks_prev, k.page = imp, clicks, days, imp_prev, clicks_prev, page
            k.pos = float(pos) if pos is not None else None
            k.pos_prev = float(pos_prev) if pos_prev is not None else None
            k.demand_day = float(dem) if dem is not None else None
            k.demand_day_prev = float(dem_prev) if dem_prev is not None else None
        for norm, url, days, imp, pos in conn.execute(f"""
            SELECT {_NORM}, top_url, count(DISTINCT date), sum(impressions),
                   sum(position * impressions) / nullif(sum(impressions) FILTER (WHERE position IS NOT NULL), 0)
            FROM webmaster_query_daily
            WHERE host_id = %(h)s AND date BETWEEN %(p1)s AND %(d2)s AND top_url IS NOT NULL
            GROUP BY 1, 2
        """, params).fetchall():
            if norm.strip() in keys:
                keys[norm.strip()].pages.append({"url": url, "days": days, "imp": imp,
                                                 "pos": float(pos) if pos is not None else None})
    if with_semantics:
        for norm, phrase, freq in conn.execute("SELECT norm, phrase, frequency FROM semantic_keywords").fetchall():
            k = key(norm, phrase)
            k.in_semantics, k.freq_year = True, freq
        for norm, pos, day in conn.execute("""
            SELECT DISTINCT ON (norm) norm, position, date FROM semantic_positions WHERE engine = 'yandex'
            ORDER BY norm, date DESC
        """).fetchall():
            if norm in keys:
                keys[norm].sheet_pos, keys[norm].sheet_date = pos, day
        snap.sheet_last = conn.execute("SELECT max(date) FROM semantic_positions").fetchone()[0]
    for phrase, day, pos, freq, pos_before in conn.execute("""
        WITH r AS (SELECT query, lower(query) AS q, date, position, frequency FROM rabbit_positions WHERE project = %s),
        last AS (SELECT DISTINCT ON (q) q, query, date, position, frequency FROM r ORDER BY q, date DESC),
        before AS (SELECT DISTINCT ON (r.q) r.q, r.position FROM r JOIN last l USING (q)
                   WHERE r.date <= l.date - %s ORDER BY r.q, r.date DESC)
        SELECT l.query, l.date, l.position, l.frequency, b.position FROM last l LEFT JOIN before b USING (q)
    """, (project, WINDOW)).fetchall():
        k = key(normalize(phrase), phrase)
        k.in_rabbit, k.rabbit_date, k.rabbit_pos, k.rabbit_freq, k.rabbit_prev = True, day, pos, freq, pos_before
        snap.rabbit_last = max(snap.rabbit_last or day, day)
    for norm, phrase, target in conn.execute(
            "SELECT norm, phrase, target FROM seo_focus WHERE host_id = %s AND removed_at IS NULL", (host_id,)).fetchall():
        k = key(norm, phrase)
        k.focus, k.target = True, target
    return snap


# ----- анализ -----

def _path(url: str | None) -> str:
    if not url:
        return "—"
    parts = urlsplit(url)
    return (parts.path or "/") + (f"?{parts.query}" if parts.query else "")


def _pos(v: float | None) -> str:
    return fmt_dec(v, 1) if v is not None else "нет в выдаче"


def ctr_norms(keys: list[Key]) -> dict[str, float]:
    """Обычный CTR сайта на позициях 1–3 и 4–10: медиана по запросам с 50+ показами. Сравнение со своим же сайтом
    на тех же позициях, а не с «нормой из интернета»."""
    bands: dict[str, list[float]] = defaultdict(list)
    for k in keys:
        if k.imp >= 50 and k.pos is not None and k.pos <= 10:
            bands[band(k.pos)].append(k.ctr or 0.0)
    return {b: statistics.median(v) for b, v in bands.items() if len(v) >= 5}


def band(pos: float) -> str:
    return "1–3" if pos <= 3.5 else "4–10"


def is_brand(k: Key, words: set[str], domain: str) -> bool:
    text = k.norm
    return bool(domain) and domain.lower() in text or any(w and w in text for w in words)


def _observed(snap: Snapshot, k: Key) -> str:
    parts = []
    if k.imp or k.imp_prev:
        parts.append(f"Вебмастер {snap.d1:%d.%m}–{snap.last:%d.%m.%Y}: позиция {_pos(k.pos)}"
                     + (f" (за 28 дней до этого — {_pos(k.pos_prev)})" if k.imp_prev else "")
                     + f", показы {fmt_int(k.imp)}" + (f" (было {fmt_int(k.imp_prev)})" if k.imp_prev else "")
                     + f", клики {fmt_int(k.clicks)}" + (f", CTR {fmt_pct(k.ctr)}" if k.imp else ""))
    else:
        parts.append(f"Вебмастер {snap.d1:%d.%m}–{snap.last:%d.%m.%Y}: показов по запросу нет" if snap.last
                     else "данных Вебмастера по запросам ещё нет")
    demand, source = k.demand
    if demand:
        parts.append(f"спрос ≈ {fmt_int(demand)} в месяц ({source})")
    if k.page:
        parts.append(f"страница в выдаче: {_path(k.page)}")
    if k.rabbit_pos is not None or k.in_rabbit:
        parts.append(f"RabbitSeo {k.rabbit_date:%d.%m}: {k.rabbit_pos if k.rabbit_pos is not None else 'не найден в выдаче'}"
                     + (f" (28 дней назад — {k.rabbit_prev})" if k.rabbit_prev is not None else ""))
    if k.sheet_pos is not None:
        parts.append(f"таблица семантики {k.sheet_date:%d.%m}: {k.sheet_pos}")
    return "; ".join(parts) + "."


def _baseline(snap: Snapshot, k: Key) -> dict:
    demand, source = k.demand
    return {"window": [snap.d1.isoformat(), snap.last.isoformat()] if snap.last else None,
            "imp": k.imp, "clicks": k.clicks, "ctr": round(k.ctr, 2) if k.ctr is not None else None,
            "pos": round(k.pos, 1) if k.pos is not None else None, "demand": demand, "demand_source": source,
            "rabbit": k.rabbit_pos, "rabbit_date": k.rabbit_date.isoformat() if k.rabbit_date else None, "page": k.page}


SERP_STEP = ("Открыть выдачу Яндекса по запросу «{p}» (записать дату и регион): какие страницы в ТОП-3, их формат "
             "(каталог, карточка, статья), что у них есть, чего нет у нас — ассортимент, цены, доставка, отзывы, фото.")


def _cluster(k: Key, by_page: dict[str, list[Key]]) -> list[str]:
    if not k.page:
        return []
    return [x.phrase for x in sorted(by_page.get(k.page, []), key=lambda x: -x.imp) if x.norm != k.norm][:5]


def _rec(snap: Snapshot, k: Key, lane: str, priority: str, by_page: dict[str, list[Key]], norms: dict[str, float]) -> Rec:
    p, page = k.phrase, _path(k.page)
    cluster = _cluster(k, by_page)
    cluster_text = f" Близкие запросы той же страницы: {', '.join(cluster)}." if cluster else ""
    owner = (f"Страница-владелец — {page}: Яндекс сам показывает её по запросу." if k.page
             else "Выбрать страницу-владельца: существующую, которая лучше всего отвечает на запрос, или новую (планируется).")
    pos = k.position
    if lane == "lost":
        title = f"«{p}»: позиции просели"
        inferred = ("Вероятные причины — правка страницы, смена страницы в выдаче, выпадение из индекса или усиление "
                    "конкурентов. Если упал и спрос, часть падения — сезон.")
        actions = [(f"Сверить даты: что менялось на странице {page} в последние 4–6 недель (тексты, цены, наличие, адрес, "
                    "редиректы); если падение совпало с правкой — вернуть убранное."),
                   "Вебмастер → «Диагностика» и «Страницы в поиске»: не исключена ли страница, нет ли ошибок.",
                   SERP_STEP.format(p=p) + " Кто занял места выше.",
                   "Проверить, не начал ли Яндекс показывать по запросу другую страницу сайта (см. «Каннибализация»)."]
        acceptance = f"позиция вернулась к {_pos(k.pos_prev)} или лучше за 28 дней после правок"
    elif lane == "defend":
        title = f"«{p}»: держать ТОП"
        inferred = "Страница уже отвечает на запрос; риск — потерять место из-за правок или конкурентов."
        actions = [f"Не менять без нужды title, H1 и адрес страницы {page}; после правок проверять позицию через 2 недели.",
                   "Следить за наличием товара и актуальностью цен на странице.",
                   "Раз в месяц смотреть выдачу: не появились ли новые сильные конкуренты."]
        acceptance = "позиция остаётся в ТОП-3"
    elif lane == "near_win":
        title = f"«{p}»: позиция {fmt_dec(pos, 1)} — до ТОП-3 недалеко" if pos and pos <= 10 else \
            f"«{p}»: позиция {fmt_dec(pos, 1)} — вывести в ТОП-10"
        inferred = ("Яндекс уже считает страницу подходящей; рост даст доработка страницы под запрос, ссылки на неё и "
                    "коммерческие факторы (ассортимент, цены, доставка, контакты).")
        actions = [owner,
                   f"Проверить, что «{p}» естественно отражён в title, H1 и первом экране страницы — без повторов и спама.",
                   "Дополнить страницу тем, что нужно покупателю по этому запросу: ассортимент, характеристики, цены, "
                   "наличие, фото, условия опта и доставки." + cluster_text,
                   "Поставить 2–3 внутренние ссылки на страницу из близких разделов каталога и статей с понятным текстом ссылки.",
                   SERP_STEP.format(p=p),
                   "Проверить скорость и мобильную версию страницы (Вебмастер → «Диагностика»)."]
        acceptance = "позиция улучшилась минимум на 2 и вошла в ТОП-10 (затем ТОП-3) за 4–6 недель"
    elif lane == "ctr":
        norm = norms.get(band(k.pos or 99))
        title = f"«{p}»: на позиции {fmt_dec(k.pos, 1)} кликают редко (CTR {fmt_pct(k.ctr)})"
        inferred = (f"Обычный CTR сайта на позициях {band(k.pos or 99)} — {fmt_pct(norm)}; сниппет, видимо, не обещает "
                    "того, что ищут, или проигрывает соседям.")
        actions = [(f"Переписать title и description страницы {page} под запрос: что за товар, опт, цена от…, наличие, "
                    "доставка; без капса и перечисления ключей."),
                   "Проверить микроразметку товаров (Product/Offer с ценой и наличием) и фавикон — они меняют вид сниппета.",
                   SERP_STEP.format(p=p) + " Что обещают сниппеты соседей."]
        acceptance = f"CTR не ниже {fmt_pct(norm)} при тех же позициях за 28 дней после правки"
    elif lane == "consolidate":
        pages = sorted((x for x in k.pages if x["days"] >= SWITCH_DAYS), key=lambda x: (x["pos"] or 99, -x["imp"]))
        best = pages[0]["url"] if pages else k.page
        others = [_path(x["url"]) for x in pages[1:]]
        title = f"«{p}»: Яндекс показывает разные страницы сайта"
        inferred = ("Страницы конкурируют за один запрос, и ни одна не набирает вес. Если страницу просто перенесли на новый "
                    "адрес, это не каннибализация — рекомендацию можно отложить.")
        actions = [f"Сделать владельцем одну страницу — предлагается {_path(best)} (лучшая позиция среди показанных).",
                   (f"На других страницах ({', '.join(others) or '—'}) убрать запрос из title и H1 или развести их по "
                    "смыслу; поставить с них ссылку на владельца."),
                   "Если страницы дублируют друг друга — склеить: 301-редирект или canonical на владельца."]
        acceptance = "28 дней Яндекс показывает по запросу одну страницу, позиция не хуже прежней"
    elif lane == "gap":
        title = f"«{p}»: сайта нет в выдаче"
        inferred = ("Отсутствие в Вебмастере — не доказательство, что страницы нет: возможно, она есть, но не отвечает на "
                    "запрос или не в индексе.")
        actions = [owner + " Если подходящей нет — создать страницу под запрос (планируемая) без тонких дублей.",
                   "Проверить, что страница в индексе (Вебмастер → «Страницы в поиске») и не закрыта robots или noindex.",
                   f"Отразить «{p}» в title, H1 и тексте; поставить ссылки на страницу из меню или каталога.",
                   SERP_STEP.format(p=p)]
        acceptance = "по запросу появились показы в Вебмастере, затем позиция в ТОП-50 → ТОП-20"
    elif lane == "head_term":
        title = f"«{p}»: ведущий запрос — цель ТОП-{min(k.target, 3)}"
        inferred = ("Самый востребованный запрос рынка определяет долю спроса; трудность меняет срок и объём работ, но не "
                    "снимает цель.")
        actions = [f"Цель — ТОП-{min(k.target, 3)} по «{p}», сверхцель — 1-е место.",
                   owner + " Одна страница на запрос: остальные ссылаются на неё.",
                   "Собрать кластер: близкие запросы раскрыть на той же странице, а не плодить отдельные." + cluster_text,
                   SERP_STEP.format(p=p) + " Разрывы записать по отдельности: формат, глубина ассортимента, цены, "
                   "доверие (отзывы, сроки работы, реквизиты), ссылки на сайт.",
                   "Перелинковка: ссылки на страницу-владельца с главной, из меню и родственных категорий.",
                   "Внешние упоминания: отраслевые каталоги, площадки, Яндекс Бизнес — без покупки ссылок."]
        acceptance = MILESTONES + " Проверка — Вебмастер (средняя позиция за 28 дней) и RabbitSeo (ежедневно)."
    else:  # strategic
        title = f"«{p}»: позиция {_pos(pos)} — долгая работа"
        inferred = "Запрос нужен, но страница пока далеко: нужен поэтапный план — содержание, структура, ссылки."
        actions = [owner,
                   "Доработать страницу под запрос: содержание, ассортимент, характеристики, коммерческие блоки." + cluster_text,
                   "Перелинковка из каталога и статей; внешние упоминания.",
                   SERP_STEP.format(p=p)]
        acceptance = MILESTONES
    if k.focus and lane != "head_term":
        acceptance += f". Цель продвижения — ТОП-{k.target}; " + MILESTONES.lower()
    return Rec(f"{lane}:{k.norm}", lane, priority, k.norm, k.phrase, k.page, title, _observed(snap, k), inferred,
               actions, acceptance, _baseline(snap, k), focus=k.focus)


def lane_of(k: Key, steady: float | None = 1.0) -> str | None:
    """Основная дорожка ключа: просел / держать ТОП / почти в ТОПе / долгая работа / нет в выдаче.
    steady — поправка на дни без данных (Snapshot.steady); None — показы окон не сравниваются."""
    dropped = k.imp_prev >= MIN_IMP and (
        (k.pos is not None and k.pos_prev is not None and k.pos - k.pos_prev >= LOST_POS)
        or (steady is not None and k.imp < k.imp_prev * steady * LOST_SHARE
            and (k.demand_day_prev is None or (k.demand_day or 0) >= 0.7 * k.demand_day_prev)))
    if dropped:
        return "lost"
    pos = k.position
    if pos is None:
        return "gap" if k.tracked else None
    if k.imp < MIN_IMP and k.rabbit_pos is None and not k.tracked:
        return None  # единичные показы хвостовых запросов — не повод для работы
    if pos <= 3.5:
        return "defend"
    if pos <= 20:
        return "near_win"
    return "strategic" if k.tracked or k.imp >= MIN_IMP else None


def analyze(snap: Snapshot, words: set[str] | None = None) -> tuple[list[Rec], dict]:
    """Рекомендации по ключам и итоги для отчёта."""
    words = words or set()
    keys = list(snap.keys.values())
    for k in keys:
        k.brand = is_brand(k, words, snap.project)
    norms = ctr_norms(keys)
    by_page: dict[str, list[Key]] = defaultdict(list)
    for k in keys:
        if k.page and k.imp >= 10:
            by_page[k.page].append(k)

    def demand_order(k: Key):
        return (-(k.demand[0] or 0), -k.imp)

    # дорожка лидерства: самые востребованные небрендовые запросы, которые сайту подходят (Яндекс показывает сайт,
    # или они в семантике, RabbitSeo, продвигаемых)
    relevant = [k for k in keys if not k.brand and k.demand[0] and (k.tracked or k.imp >= MIN_IMP)]
    heads = {k.norm for k in sorted(relevant, key=demand_order)[:HEAD_TERMS]}
    candidates: dict[str, list[tuple[Key, str]]] = defaultdict(list)
    for k in keys:
        lane = lane_of(k, snap.steady)
        growth = lane in ("near_win", "strategic", "gap")
        if k.brand and growth:
            lane = None
        if k.norm in heads and (lane in (None, "near_win", "strategic", "gap")):
            lane = "head_term" if (k.position or 99) > 3.5 else lane
        if lane:
            candidates[lane].append((k, _priority(lane, k)))
        if k.pos is not None and k.pos <= 10 and k.imp >= CTR_MIN_IMP and band(k.pos) in norms \
                and (k.ctr or 0) < CTR_SHARE * norms[band(k.pos)]:
            candidates["ctr"].append((k, "now"))
        shown = [x for x in k.pages if x["days"] >= SWITCH_DAYS]
        if len(shown) >= 2 and k.imp + k.imp_prev >= MIN_IMP:
            candidates["consolidate"].append((k, "now" if k.imp >= MIN_IMP else "next"))
    recs = []
    for lane, items in candidates.items():
        items.sort(key=lambda x: demand_order(x[0]))
        chosen = items[:LIMITS[lane]] + [x for x in items[LIMITS[lane]:] if x[0].focus]
        for k, priority in chosen:
            if k.focus and lane not in ("defend",):
                priority = "now"
            recs.append(_rec(snap, k, lane, priority, by_page, norms))
    # продвигаемый ключ без единой рекомендации (нет данных) — всё равно получает план: найти страницу и начать
    covered = {r.norm for r in recs}
    for k in keys:
        if k.focus and k.norm not in covered:
            recs.append(_rec(snap, k, "gap" if k.position is None else "strategic", "now", by_page, norms))
    recs.sort(key=lambda r: (list(PRIORITIES).index(r.priority), not r.focus, list(LANES).index(r.lane)))
    return recs, summarize(snap, keys, recs, heads, norms)


def _priority(lane: str, k: Key) -> str:
    if lane in ("lost", "head_term"):
        return "now"  # ведущий запрос: страница-владелец и основа — сразу, хоть цель и долгая
    if lane == "near_win":
        return "now" if (k.position or 99) <= 10 else "next"
    if lane == "gap":
        return "next"
    return "later"  # defend, strategic


def summarize(snap: Snapshot, keys: list[Key], recs: list[Rec], heads: set[str], norms: dict[str, float]) -> dict:
    shown = [k for k in keys if k.imp > 0]
    imp, clicks = sum(k.imp for k in keys), sum(k.clicks for k in keys)
    imp_prev, clicks_prev = sum(k.imp_prev for k in keys), sum(k.clicks_prev for k in keys)
    bands = {"top3": 0, "top10": 0, "top20": 0, "deeper": 0}
    for k in shown:
        if k.pos is None:
            continue
        bands["top3" if k.pos <= 3.5 else "top10" if k.pos <= 10 else "top20" if k.pos <= 20 else "deeper"] += 1
    lanes = defaultdict(int)
    for r in recs:
        lanes[r.lane] += 1
    head_keys = sorted((snap.keys[n] for n in heads), key=lambda k: -(k.demand[0] or 0))
    findings = []
    if snap.last:
        findings.append(
            f"За {snap.d1:%d.%m}–{snap.last:%d.%m.%Y} у сайта показы по {fmt_int(len(shown))} запросам: в ТОП-3 — "
            f"{bands['top3']}, 4–10 — {bands['top10']}, 11–20 — {bands['top20']}, ниже — {bands['deeper']} "
            "(средняя позиция Вебмастера).")
        if imp_prev:
            findings.append(f"Показы {fmt_int(imp)} против {fmt_int(imp_prev)} за 28 дней до этого, клики {fmt_int(clicks)} "
                            f"против {fmt_int(clicks_prev)}; CTR {fmt_pct(100 * clicks / imp if imp else None)}.")
    near = [f"{LANES[x][0].lower() + LANES[x][1:]} — {lanes[x]}" for x in ("lost", "near_win", "ctr", "consolidate", "gap")
            if lanes.get(x)]
    if near:
        findings.append("Ближняя дорожка: " + ", ".join(near) + ".")
    if head_keys:
        findings.append("Ведущие запросы (по спросу): " + "; ".join(
            f"«{k.phrase}» — {_pos(k.position)}" for k in head_keys) + ". Цель по ним — ТОП-3.")
    else:
        findings.append("Ведущие запросы не определены: нет данных о спросе (Вебмастер, таблица семантики, RabbitSeo) — "
                        "сначала нужно собрать спрос.")
    focus = [k for k in keys if k.focus]
    if focus:
        findings.append(f"Продвигаем {len(focus)} ключ(а/ей): " + "; ".join(
            f"«{k.phrase}» — {_pos(k.position)}" for k in focus[:8]) + ("…" if len(focus) > 8 else "") + ".")
    limitations = [
        "Средняя позиция Вебмастера — среднее по показам, а не точное место в выдаче; точнее — RabbitSeo по своим запросам.",
        "Аналитику запросов Вебмастер отдаёт за 14 дней; история копится с запуска сборщика"
        + (f" (с {snap.first:%d.%m.%Y})." if snap.first else "."),
        "Выдачу Яндекса ассистент сам не смотрит: разбор выдачи в плане делает человек, с датой и регионом.",
        "Спрос из разных источников считается по-разному — источник указан рядом с числом.",
    ]
    return {
        "period": [snap.d1.isoformat(), snap.last.isoformat()] if snap.last else None,
        "prev": [snap.p1.isoformat(), snap.p2.isoformat()] if snap.last else None,
        "queries": len(shown), "bands": bands, "imp": imp, "imp_prev": imp_prev, "clicks": clicks,
        "clicks_prev": clicks_prev, "lanes": dict(lanes), "ctr_norms": norms, "findings": findings,
        "heads": [k.phrase for k in head_keys], "limitations": limitations,
        "sources": {"webmaster": snap.last.isoformat() if snap.last else None,
                    "rabbit": snap.rabbit_last.isoformat() if snap.rabbit_last else None,
                    "semantics": snap.sheet_last.isoformat() if snap.sheet_last else None},
    }


# ----- хранение -----

def save_report(conn: psycopg.Connection, snap: Snapshot, recs: list[Rec], summary: dict) -> int:
    """Новый отчёт. Рекомендация, которая уже открыта, отмечается подтверждённой (текст и дата — прежние); новая —
    добавляется; открытая, которой в отчёте нет, — «неактуальна». Ничего не удаляется."""
    with conn.transaction():
        report_id = conn.execute(
            "INSERT INTO seo_reports (host_id, period_from, period_to, summary) VALUES (%s, %s, %s, %s) RETURNING report_id",
            (snap.host_id, snap.d1 or date.today(), snap.last or date.today(), json.dumps(summary, ensure_ascii=False)),
        ).fetchone()[0]
        open_ = dict(conn.execute("SELECT key, rec_id FROM seo_recs WHERE host_id = %s AND status = 'open'",
                                  (snap.host_id,)).fetchall())
        seen = set()
        for r in recs:
            if r.key in seen:
                continue
            seen.add(r.key)
            if r.key in open_:
                conn.execute("UPDATE seo_recs SET seen_at = now(), report_id = %s, priority = %s, focus = %s "
                             "WHERE rec_id = %s", (report_id, r.priority, r.focus, open_[r.key]))
            else:
                conn.execute("""
                    INSERT INTO seo_recs (host_id, key, lane, track, priority, norm, phrase, page, title, observed,
                                          inferred, actions, acceptance, baseline, focus, report_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (snap.host_id, r.key, r.lane, r.track, r.priority, r.norm, r.phrase, r.page, r.title, r.observed,
                      r.inferred, json.dumps(r.actions, ensure_ascii=False), r.acceptance,
                      json.dumps(r.baseline, ensure_ascii=False), r.focus, report_id))
        stale = [rec_id for key, rec_id in open_.items() if key not in seen]
        if stale:
            conn.execute("UPDATE seo_recs SET status = 'stale', status_at = now() WHERE rec_id = ANY (%s)", (stale,))
    return report_id


def run(conn: psycopg.Connection, host_id: str, words: set[str] | None = None, with_semantics: bool = True,
        reason: str = "") -> int:
    snap = collect(conn, host_id, with_semantics)
    recs, summary = analyze(snap, words)
    summary["reason"] = reason
    return save_report(conn, snap, recs, summary)


def primary_host(conn: psycopg.Connection, counters: tuple[int, ...]) -> str | None:
    """Сайт первого счётчика в настройках — к нему относится таблица семантики."""
    if not counters:
        return None
    row = conn.execute("SELECT host_id FROM site_hosts WHERE counter_id = %s ORDER BY host_id LIMIT 1",
                       (counters[0],)).fetchone()
    return row[0] if row else None


def due(conn: psycopg.Connection, host_id: str) -> bool:
    """Пора пересчитать: отчёта нет или Вебмастер загрузил данные после последнего отчёта."""
    row = conn.execute("""
        SELECT (SELECT max(created_at) FROM seo_reports WHERE host_id = %(h)s),
               (SELECT max(loaded_at) FROM webmaster_query_daily WHERE host_id = %(h)s)
    """, {"h": host_id}).fetchone()
    return row[1] is not None and (row[0] is None or row[0] < row[1])


def set_focus(conn: psycopg.Connection, host_id: str, phrases: list[str], on: bool, target: int = 3) -> int:
    """Взять ключи в продвижение (или снять). Снятый ключ остаётся в истории с датой снятия."""
    n = 0
    for phrase in phrases:
        norm = normalize(phrase)
        if not norm:
            continue
        if on:
            conn.execute("""
                INSERT INTO seo_focus (host_id, norm, phrase, target) VALUES (%s, %s, %s, %s)
                ON CONFLICT (host_id, norm) DO UPDATE SET removed_at = NULL, target = EXCLUDED.target,
                    added_at = CASE WHEN seo_focus.removed_at IS NULL THEN seo_focus.added_at ELSE now() END
            """, (host_id, norm, phrase.strip()[:300], target))
        else:
            conn.execute("UPDATE seo_focus SET removed_at = now() WHERE host_id = %s AND norm = %s AND removed_at IS NULL",
                         (host_id, norm))
        n += 1
    return n


def set_status(conn: psycopg.Connection, host_id: str, rec_id: int, status: str, done_on: date | None = None,
               note: str = "") -> bool:
    """Отметить рекомендацию: внедрена (с датой), отложена владельцем, снова в работе."""
    if status not in ("done", "dismissed", "open"):
        raise ValueError(status)
    if status == "open":
        busy = conn.execute("""
            SELECT 1 FROM seo_recs a JOIN seo_recs b USING (host_id, key)
            WHERE b.rec_id = %s AND a.status = 'open' AND a.rec_id <> b.rec_id
        """, (rec_id,)).fetchone()
        if busy:
            return False  # такая рекомендация уже снова открыта новым отчётом
    cur = conn.execute("""
        UPDATE seo_recs SET status = %s, status_at = now(), done_on = %s, note = %s
        WHERE rec_id = %s AND host_id = %s
    """, (status, done_on if status == "done" else None, note.strip()[:1000] or None, rec_id, host_id))
    return cur.rowcount == 1


# ----- чтение для страницы -----

def _rows(conn: psycopg.Connection, sql: str, params) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        names = [c.name for c in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]


def reports(conn: psycopg.Connection, host_id: str, limit: int = 30) -> list[dict]:
    rows = _rows(conn, "SELECT report_id, created_at, period_from, period_to, summary FROM seo_reports "
                       "WHERE host_id = %s ORDER BY created_at DESC LIMIT %s", (host_id, limit))
    for r in rows:
        r["sources"] = {k: date.fromisoformat(v) if v else None for k, v in (r["summary"].get("sources") or {}).items()}
    return rows


def recs(conn: psycopg.Connection, host_id: str, statuses: tuple[str, ...]) -> list[dict]:
    rows = _rows(conn, "SELECT * FROM seo_recs WHERE host_id = %s AND status = ANY (%s) ORDER BY created_at DESC, rec_id",
                 (host_id, list(statuses)))
    for r in rows:
        r["lane_title"] = LANES.get(r["lane"], r["lane"])
        r["track_title"] = TRACKS.get(r["track"], r["track"])
        r["priority_title"] = PRIORITIES.get(r["priority"], r["priority"])
        r["status_title"] = STATUSES.get(r["status"], r["status"])
        r["page_path"] = _path(r["page"]) if r["page"] else None
    return rows


def focus_keys(conn: psycopg.Connection, host_id: str, with_removed: bool = False) -> list[dict]:
    return _rows(conn, "SELECT norm, phrase, target, added_at, removed_at FROM seo_focus WHERE host_id = %s"
                       + ("" if with_removed else " AND removed_at IS NULL") + " ORDER BY added_at, phrase", (host_id,))


# ----- оценка внедрённого -----

@dataclass
class Window:
    start: date
    end: date
    days: int = 0
    imp: int = 0
    clicks: int = 0
    pos: float | None = None

    @property
    def ctr(self) -> float | None:
        return 100.0 * self.clicks / self.imp if self.imp else None


@dataclass
class Impact:
    before: Window
    after: Window
    page_before: Window | None
    page_after: Window | None
    control_shift: float | None       # насколько в среднем сдвинулись позиции остальных запросов сайта
    control_clicks: float | None      # во сколько раз изменились клики остальных запросов (в день)
    pos_effect: float | None          # сдвиг позиции запроса сверх общего (минус — лучше)
    clicks_effect: float | None       # изменение кликов в день сверх общего, доля
    rabbit_before: float | None
    rabbit_after: float | None
    verdict: str
    tone: str                         # good | bad | neutral | wait
    text: str

    def as_dict(self) -> dict:
        return asdict(self)


def windows_for(done_on: date, last: date) -> tuple[date, date, date, date]:
    """До: 28 дней до внедрения; после: с 8-го дня после внедрения (неделя на переиндексацию) — до 28 дней."""
    b1, b2 = done_on - timedelta(days=WINDOW), done_on - timedelta(days=1)
    a1 = done_on + timedelta(days=LAG_DAYS)
    return b1, b2, a1, min(a1 + timedelta(days=WINDOW - 1), last)


def impact(conn: psycopg.Connection, host_id: str, norm: str | None, page: str | None, done_on: date,
           project: str | None = None, lag: bool = True) -> Impact | None:
    """Что стало с запросом после внедрения — в сравнении с остальными запросами сайта за те же дни (контроль):
    если упал или вырос весь сайт (апдейт Яндекса, сезон), это не заслуга и не вина правки."""
    last = conn.execute("SELECT max(date) FROM webmaster_query_daily WHERE host_id = %s", (host_id,)).fetchone()[0]
    if not last or not norm:
        return None
    b1, b2, a1, a2 = windows_for(done_on, last)
    if not lag:
        a1, a2 = max(done_on, last - timedelta(days=WINDOW - 1)), last
    rows = conn.execute(f"""
        WITH w AS (
            SELECT {_NORM} AS norm, top_url, date, impressions, clicks, position FROM webmaster_query_daily
            WHERE host_id = %(h)s AND date BETWEEN %(b1)s AND %(a2)s
        )
        SELECT norm, bool_or(top_url = %(page)s) AS on_page,
               coalesce(sum(impressions) FILTER (WHERE date <= %(b2)s), 0), coalesce(sum(clicks) FILTER (WHERE date <= %(b2)s), 0),
               sum(position * impressions) FILTER (WHERE date <= %(b2)s AND position IS NOT NULL)
                   / nullif(sum(impressions) FILTER (WHERE date <= %(b2)s AND position IS NOT NULL), 0),
               coalesce(sum(impressions) FILTER (WHERE date >= %(a1)s), 0), coalesce(sum(clicks) FILTER (WHERE date >= %(a1)s), 0),
               sum(position * impressions) FILTER (WHERE date >= %(a1)s AND position IS NOT NULL)
                   / nullif(sum(impressions) FILTER (WHERE date >= %(a1)s AND position IS NOT NULL), 0)
        FROM w GROUP BY norm
    """, {"h": host_id, "b1": b1, "b2": b2, "a1": a1, "a2": a2, "page": page or ""}).fetchall()
    days_b, days_a = conn.execute("""
        SELECT count(DISTINCT date) FILTER (WHERE date BETWEEN %(b1)s AND %(b2)s),
               count(DISTINCT date) FILTER (WHERE date BETWEEN %(a1)s AND %(a2)s)
        FROM webmaster_query_daily WHERE host_id = %(h)s AND date BETWEEN %(b1)s AND %(a2)s
    """, {"h": host_id, "b1": b1, "b2": b2, "a1": a1, "a2": a2}).fetchone()
    before, after = Window(b1, b2, days_b), Window(a1, a2, days_a)
    page_b, page_a = (Window(b1, b2, days_b), Window(a1, a2, days_a)) if page else (None, None)
    shift_num = shift_w = 0.0
    ctl_b = ctl_a = 0
    for n, on_page, ib, cb, pb, ia, ca, pa in rows:
        n = n.strip()
        if n == norm:
            before.imp, before.clicks, before.pos = ib, cb, float(pb) if pb is not None else None
            after.imp, after.clicks, after.pos = ia, ca, float(pa) if pa is not None else None
        if on_page and page_b:
            page_b.imp += ib
            page_b.clicks += cb
            page_a.imp += ia
            page_a.clicks += ca
        if n != norm and not on_page:
            ctl_b += cb
            ctl_a += ca
            if pb is not None and pa is not None and min(ib, ia) > 0:
                weight = min(ib, ia)
                shift_num += (float(pa) - float(pb)) * weight
                shift_w += weight
    control_shift = shift_num / shift_w if shift_w else None
    control_clicks = (ctl_a / days_a) / (ctl_b / days_b) if days_a and days_b and ctl_b else None
    rb = ra = None
    if project:
        # RabbitSeo бывает свежее Вебмастера: его окно «после» — те же 28 дней от начала, сколько уже есть
        rb, ra = conn.execute("""
            SELECT avg(position) FILTER (WHERE date BETWEEN %(b1)s AND %(b2)s),
                   avg(position) FILTER (WHERE date BETWEEN %(a1)s AND %(a2)s)
            FROM rabbit_positions WHERE project = %(p)s AND lower(query) = %(n)s
        """, {"p": project, "n": norm, "b1": b1, "b2": b2, "a1": a1,
              "a2": a1 + timedelta(days=WINDOW - 1) if lag else date.max}).fetchone()
        rb, ra = (float(rb) if rb is not None else None), (float(ra) if ra is not None else None)
    pos_effect = clicks_effect = None
    if before.pos is not None and after.pos is not None:
        pos_effect = (after.pos - before.pos) - (control_shift or 0.0)
    if before.clicks and days_b and days_a:
        ratio = (after.clicks / days_a) / (before.clicks / days_b)
        clicks_effect = ratio / (control_clicks or 1.0) - 1
    verdict, tone, text = _verdict(before, after, pos_effect, clicks_effect, rb, ra, lag)
    return Impact(before, after, page_b, page_a, control_shift, control_clicks, pos_effect, clicks_effect, rb, ra,
                  verdict, tone, text)


def _verdict(before: Window, after: Window, pos_effect, clicks_effect, rb, ra, lag: bool) -> tuple[str, str, str]:
    if lag and after.days < JUDGE_DAYS:
        return ("рано судить", "wait",
                f"после внедрения и недели на переиндексацию данных за {after.days} дн.; судим по 14+ дням")
    if not lag and after.days < 7:
        return "рано судить", "wait", f"с начала продвижения данных за {after.days} дн.; первые выводы — через неделю"
    if before.imp < MIN_IMP and after.imp >= MIN_IMP:
        return "появились показы", "good", f"было {fmt_int(before.imp)} показов, стало {fmt_int(after.imp)}"
    if before.imp < MIN_IMP and after.imp < MIN_IMP and (rb is None or ra is None):
        return "мало данных", "neutral", f"показов до {fmt_int(before.imp)}, после {fmt_int(after.imp)} — для вывода мало"
    rabbit = rb is not None and ra is not None and ra - rb
    better = (pos_effect is not None and pos_effect <= -1.0) or (clicks_effect is not None and clicks_effect >= 0.2
                                                                 and before.clicks + after.clicks >= 20) \
        or (rabbit is not False and rabbit <= -2)
    worse = (pos_effect is not None and pos_effect >= 1.0) or (clicks_effect is not None and clicks_effect <= -0.2
                                                               and before.clicks + after.clicks >= 20) \
        or (rabbit is not False and rabbit >= 2)
    parts = []
    if pos_effect is not None:
        parts.append(f"позиция {fmt_dec(before.pos, 1)} → {fmt_dec(after.pos, 1)}, сверх общего движения сайта "
                     f"{'+' if pos_effect > 0 else '−' if pos_effect < 0 else ''}{fmt_dec(abs(pos_effect), 1)}")
    if clicks_effect is not None:
        parts.append(f"клики в день {'+' if clicks_effect >= 0 else '−'}{fmt_pct(abs(clicks_effect) * 100, 0)} сверх общего")
    if rb is not None and ra is not None:
        parts.append(f"RabbitSeo {fmt_dec(rb, 1)} → {fmt_dec(ra, 1)}")
    text = "; ".join(parts) or "сравнить не с чем"
    if better and not worse:
        return "лучше", "good", text
    if worse and not better:
        return "хуже", "bad", text
    return "без заметного изменения", "neutral", text


def progress(conn: psycopg.Connection, host_id: str, norm: str, since: datetime | date, project: str) -> Impact | None:
    """Продвигаемый ключ: последние 28 дней против 28 дней до начала продвижения."""
    day = since.date() if isinstance(since, datetime) else since
    return impact(conn, host_id, norm, None, day, project, lag=False)
