"""Ассистент SEO: дорожки по методике seo-research, хранение рекомендаций без дублей и удаления, оценка внедрённого."""

import os
from datetime import date, timedelta

import pytest

from analytics import db, seo
from analytics.seo import Key, Snapshot

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL не задан")

HOST = "https:www.example.ru:443"
LAST = date(2026, 11, 15)


def snapshot(*keys: Key) -> Snapshot:
    snap = Snapshot(HOST, "example.ru", date(2026, 9, 1), LAST, {k.norm: k for k in keys}, days_cur=28, days_prev=28)
    return snap


def key(phrase, **kw) -> Key:
    return Key(phrase, phrase, **kw)


def lanes(recs):
    return {(r.lane, r.norm): r for r in recs}


def test_lanes_follow_the_method(monkeypatch):
    monkeypatch.setattr(seo, "HEAD_TERMS", 1)  # в настоящих данных ведущих — 5 из сотен ключей со спросом
    # обычный CTR сайта на позициях 4–10 — около 6%: пять запросов для медианы
    usual = [key(f"ткань {i}", imp=200, clicks=12, pos=6.0, page="https://example.ru/a") for i in range(5)]
    keys = [
        *usual,
        key("оксфорд 600d", imp=300, clicks=21, pos=7.0, demand_day=40, page="https://example.ru/oxford", in_semantics=True),
        key("ткань дюспо", imp=300, clicks=2, pos=5.0, page="https://example.ru/dyuspo"),            # слабый сниппет
        key("мембранная ткань", imp=200, clicks=10, pos=9.0, imp_prev=200, clicks_prev=12, pos_prev=4.0,
            page="https://example.ru/membrane"),                                                     # просел
        key("кордура", imp=120, clicks=3, pos=14.0, page="https://example.ru/cordura",
            pages=[{"url": "https://example.ru/cordura", "days": 10, "imp": 80, "pos": 12.0},
                   {"url": "https://example.ru/catalog", "days": 7, "imp": 40, "pos": 18.0}]),      # две страницы
        key("ткань для палаток", in_semantics=True, freq_year=24000),                                # нет в выдаче
        key("купить ткань оптом", imp=500, clicks=5, pos=24.0, demand_day=900, in_semantics=True),   # ведущий запрос
        key("олдос ткани", imp=400, clicks=200, pos=8.0, demand_day=2000),                          # бренд
        key("ткань для чехлов", focus=True),                                                         # продвигаем, данных нет
    ]
    recs, summary = seo.analyze(snapshot(*keys), {"олдос"})
    got = lanes(recs)
    assert got[("near_win", "оксфорд 600d")].priority == "now"
    assert ("ctr", "ткань дюспо") in got
    assert got[("lost", "мембранная ткань")].priority == "now"
    assert ("near_win", "мембранная ткань") not in got  # просевший не числится «почти в ТОПе»
    consolidate = got[("consolidate", "кордура")]
    assert "/cordura" in consolidate.actions[0]  # владелец — страница с лучшей позицией
    assert got[("gap", "ткань для палаток")].priority == "next"
    head = got[("head_term", "купить ткань оптом")]
    assert head.track == "lead" and head.priority == "now" and "ТОП-3" in head.title
    assert not any(norm == "олдос ткани" and lane in ("near_win", "head_term") for lane, norm in got)
    plan = got[("gap", "ткань для чехлов")]
    assert plan.focus and plan.priority == "now"
    # наблюдение — с источником и датами, вывод — отдельно
    near = got[("near_win", "оксфорд 600d")]
    assert "Вебмастер 19.10–15.11.2026" in near.observed and "спрос ≈ 1\xa0200 в месяц (Вебмастер)" in near.observed
    assert near.inferred and near.actions and near.acceptance
    assert summary["heads"] == ["купить ткань оптом"]  # бренд — не ведущий запрос рынка


def test_no_drop_without_comparable_windows():
    """Показов меньше, но в прошлом окне меньше двух недель данных — это не падение, а неполная история."""
    k = key("ткань", imp=40, clicks=2, pos=6.0, imp_prev=200, pos_prev=6.0, in_semantics=True)
    assert seo.lane_of(k, steady=None) == "near_win"
    assert seo.lane_of(k, steady=1.0) == "lost"
    # сборщик не работал половину текущего окна — показов вдвое меньше ожидаемо
    assert seo.lane_of(key("ткань", imp=90, pos=6.0, imp_prev=200, pos_prev=6.0), steady=0.5) == "near_win"


def _rows(query, page, days, impressions, clicks, position, demand=None):
    return [(HOST, d, query, page, impressions, clicks, position, demand) for d in days]


def _fill(conn, rows):
    conn.cursor().executemany(
        "INSERT INTO webmaster_query_daily (host_id, date, query, top_url, impressions, clicks, position, demand) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)", rows)


@needs_db
def test_reports_keep_history_and_judge_impact():
    days = [LAST - timedelta(days=i) for i in range(56)]
    with db.connect(TEST_DATABASE_URL) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        db.apply_schema(conn)
        _fill(conn, _rows("оксфорд 600d", "https://example.ru/oxford", days, 10, 1, 8.0)
              + _rows("кордура", "https://example.ru/cordura", days, 10, 1, 12.0)
              + [r for i in range(8) for r in _rows(f"ткань {i}", "https://example.ru/a", days, 10, 1, 5.0)])
        first = seo.run(conn, HOST, set(), reason="тест")
        recs = seo.recs(conn, HOST, ("open",))
        oxford = next(r for r in recs if r["lane"] == "near_win" and r["norm"] == "оксфорд 600d")
        assert oxford["observed"] and oxford["baseline"]["pos"] == 8.0
        # повторный отчёт не плодит дубли: рекомендация та же, подтверждена
        second = seo.run(conn, HOST, set())
        again = seo.recs(conn, HOST, ("open",))
        assert len(again) == len(recs) and second != first
        assert next(r for r in again if r["rec_id"] == oxford["rec_id"])["report_id"] == second
        # кордура ушла из данных — её рекомендация становится «неактуальной», но остаётся с датами
        conn.execute("DELETE FROM webmaster_query_daily WHERE query = 'кордура'")
        seo.run(conn, HOST, set())
        stale = seo.recs(conn, HOST, ("stale",))
        assert any(r["norm"] == "кордура" for r in stale) and all(r["status_at"] for r in stale)

        # внедрили 01.11: «оксфорд» поднялся с 8 до 3, остальные запросы сайта стоят — эффект в пользу правки
        done_on = date(2026, 10, 10)
        conn.execute("UPDATE webmaster_query_daily SET position = 3.0, clicks = 4 "
                     "WHERE query = 'оксфорд 600d' AND date >= %s", (done_on + timedelta(days=7),))
        assert seo.set_status(conn, HOST, oxford["rec_id"], "done", done_on, "переписали title, добавили ссылки")
        impact = seo.impact(conn, HOST, "оксфорд 600d", oxford["page"], done_on, "example.ru")
        assert impact.before.pos == 8.0 and impact.after.pos == 3.0
        assert impact.verdict == "лучше" and impact.pos_effect == pytest.approx(-5.0)
        assert impact.clicks_effect == pytest.approx(3.0)  # кликов вчетверо больше при неизменном фоне
        # совсем свежее внедрение — «рано судить»
        assert seo.impact(conn, HOST, "оксфорд 600d", None, LAST - timedelta(days=3), "example.ru").verdict == "рано судить"
        # отложенную можно вернуть, но не если такая уже открыта новым отчётом
        rec = seo.recs(conn, HOST, ("open",))[0]
        assert seo.set_status(conn, HOST, rec["rec_id"], "dismissed", note="не наш товар")
        seo.run(conn, HOST, set())  # отчёт снова открыл такую же
        assert not seo.set_status(conn, HOST, rec["rec_id"], "open")

        # продвигаемый ключ: снятие не удаляет историю
        seo.set_focus(conn, HOST, ["Ткань для Чехлов"], True)
        assert [x["norm"] for x in seo.focus_keys(conn, HOST)] == ["ткань для чехлов"]
        seo.set_focus(conn, HOST, ["ткань для чехлов"], False)
        assert seo.focus_keys(conn, HOST) == [] and seo.focus_keys(conn, HOST, with_removed=True)[0]["removed_at"]
        assert seo.due(conn, HOST) is False
