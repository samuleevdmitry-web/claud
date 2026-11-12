from datetime import date

from analytics import insights as ins

PREV = date(2026, 9, 1)


def row(label, visits, prev=None, leads=0, bounce=15.0, key=None):
    return {"dim_key": key or label, "dim_label": label, "visits": visits, "visits_prev": prev, "leads": leads,
            "conversion_pct": round(100 * leads / visits, 2) if visits else 0, "bounce_rate": bounce}


def texts(findings, kind):
    return [f.text for f in findings if f.kind == kind]


def test_slice_rules_growth_conversion_and_bounce():
    rows = [
        row("Поиск", 6000, prev=5000, leads=60),
        row("Прямые заходы", 2000, prev=2100, leads=2, bounce=50.0),
        row("Рассылки", 800, prev=700, leads=20),
        row("Не определено", 1200, prev=200, key="(none)"),
    ]
    found = ins.slice_findings("source", "Каналы", rows, total_visits=10000, site_conv=1.0, site_bounce=17.0, prev=PREV)
    assert any("«Поиск» — визиты +1 000" in t for t in texts(found, "plus"))
    assert any("«Рассылки» конвертирует" in t for t in texts(found, "plus"))      # 2,5% против 1%
    assert any("«Прямые заходы»" in t and "конверсия" in t for t in texts(found, "minus"))
    assert any("«Прямые заходы» — отказы 50" in t for t in texts(found, "check"))
    assert not any("Не определено" in f.text for f in found)                      # «нет значения» не комментируем


def test_foreign_countries_flagged_as_possible_bots():
    rows = [row("Россия", 9000, prev=8000), row("Китай", 900, prev=100), row("Беларусь", 100)]
    found = ins.slice_findings("country", "Страны", rows, 10000, 1.0, 17.0, PREV)
    assert any("Китай 900" in t for t in texts(found, "question"))
    assert texts(found, "check")


def test_daily_spike_is_a_question():
    daily = [{"day": date(2026, 10, d), "visits": 700, "leads": 4} for d in range(1, 32)]
    for d in (12, 13, 14):
        daily[d - 1]["visits"] = 1700
    found = ins.daily_spikes(daily)
    assert len(found) == 1 and "12.10–14.10" in found[0].text and "1 700 визитов" in found[0].text


def test_robots_and_audience_hint():
    m = {"visits": 23000, "visits_prev": 17500, "leads": 138, "leads_prev": 138,
         "bounce_rate": 17.2, "bounce_rate_prev": 18.1}
    found = ins.traffic_summary(m, robots_share=40.0, robots_share_prev=10.0, daily=[], prev=PREV,
                                yoy_partial=False, audience="all")
    assert any("Роботы — 40%" in t for t in texts(found, "minus"))
    assert any("«Только люди»" in t for t in texts(found, "check"))


def test_summary_groups_in_fixed_order_without_duplicates():
    summary = ins.summarize([ins.Finding("check", "a"), ins.Finding("plus", "b"), ins.Finding("plus", "b")])
    assert [(kind, items) for kind, _, items in summary.groups] == [("plus", ["b"]), ("check", ["a"])]
    assert not ins.summarize([])


def test_search_movers_findings():
    movers = {"up": [{"query": "ткань оксфорд", "position": 4.0, "position_prev": 7.5}], "down": [],
              "lost": [{"query": "пвх ткань"}], "new": []}
    found = ins.search_findings(None, False, movers, stale_days=37, problems=[], prev=PREV)
    assert any("1 запрос" in t and "7,5 → 4,0" in t for t in texts(found, "plus"))
    assert any("«пвх ткань»" in t for t in texts(found, "check"))
    assert any("37 дн." in t for t in texts(found, "minus"))
