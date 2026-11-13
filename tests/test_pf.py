"""Правила раздела «ПФ и риски» на синтетических цифрах."""

from datetime import date, timedelta

from analytics import insights as ins

D = date(2026, 10, 6)


def window(boosted_far=(26, 34), others_far=(47, 4563), full_ctr=()):
    return {"first_day": D - timedelta(days=13), "last_day": D, "full_ctr": list(full_ctr),
            "boosted": {"clicks_far": boosted_far[0], "impressions_far": boosted_far[1]},
            "others": {"clicks_far": others_far[0], "impressions_far": others_far[1]}}


def week(day, yandex, google):
    return {"week": day, "yandex": yandex, "google": google}


def weeks(last_yandex, last_google):
    start = date(2026, 9, 7)
    base = [week(start + timedelta(weeks=i), 1000, 1200) for i in range(4)]
    return base + [week(start + timedelta(weeks=4), last_yandex, last_google)]


def test_far_ctr_of_boosted_queries_is_a_trace():
    s = ins.pf_far_ctr(window())
    assert s.status == "alert" and "76,5%" in s.text and "1,03%" in s.text
    assert ins.pf_far_ctr(window(boosted_far=(1, 40))).status == "ok"
    assert ins.pf_far_ctr(window(boosted_far=(3, 5))).status == "ok"  # мало показов — не судим
    assert ins.pf_far_ctr(None).status == "nodata"


def test_full_ctr_counts_queries():
    rows = [{"query": f"q{i}", "boosted": i % 2 == 0, "impressions": 3, "clicks": 3, "position": 11.0} for i in range(8)]
    assert ins.pf_full_ctr(window(full_ctr=rows)).status == "alert"
    assert ins.pf_full_ctr(window(full_ctr=rows[:3])).status == "warn"
    assert ins.pf_full_ctr(window()).status == "ok"


def test_domain_queries_skip_brand():
    brand = {"олдос", "oldos"}
    assert ins.brand_free("ткань таслан oldos.ru", "oldos.ru", brand)
    assert not ins.brand_free("олдос ростов oldos.ru", "oldos.ru", brand)
    rows = [{"query": f"ткань {i} oldos.ru", "clicks": 2, "first_seen": D, "from_project": True} for i in range(5)]
    s = ins.pf_domain(rows, "oldos.ru")
    assert s.status == "alert" and "5\xa0запросов" in s.text and "10 кликов" in s.text
    assert ins.pf_domain([], "oldos.ru").status == "ok"


def test_engine_share_tells_filter_from_season():
    assert ins.pf_engine_share(weeks(600, 1180)).status == "alert"   # упал только Яндекс
    both = ins.pf_engine_share(weeks(600, 700))                        # упали оба — не санкции
    assert both.status == "warn" and "сезон" in both.text
    assert ins.pf_engine_share(weeks(990, 1210)).status == "ok"
    assert ins.pf_engine_share(weeks(990, 1210)[:4]).status == "nodata"
    spike = weeks(950, 1150)
    spike[2] = week(spike[2]["week"], 1900, 2200)  # всплеск в базе не должен делать обычную неделю провалом
    assert ins.pf_engine_share(spike).status == "ok"
    tiny = [week(w["week"], 5, 14) for w in weeks(3, 14)]
    assert ins.pf_engine_share(tiny).status == "nodata"


def test_rabbit_mass_drop():
    now, then = {"date": D}, {"date": D - timedelta(days=7)}
    stable = [{"query": f"s{i}", "position": 5, "position_prev": 5} for i in range(10)]
    lost = [{"query": f"q{i}", "position": None, "position_prev": 20} for i in range(6)]
    assert ins.pf_rabbit_drop(stable + lost, now, then).status == "alert"
    assert ins.pf_rabbit_drop(stable, now, then).status == "ok"
    # с 40–50-х мест запросы и так то пропадают, то появляются — это не просадка
    noise = [{"query": f"n{i}", "position": None, "position_prev": 46} for i in range(6)]
    assert ins.pf_rabbit_drop(stable + noise, now, then).status == "ok"
    fell = [{"query": f"t{i}", "position": 25, "position_prev": 3} for i in range(4)]
    assert ins.pf_rabbit_drop(fell + stable[:2], now, then).status == "alert"   # ТОП-10 сократился втрое
    assert ins.pf_rabbit_drop([], now, then).status == "nodata"


def test_benefit_and_verdict():
    rows = [{"query": "пвх ткани", "frequency": 1627, "position": None, "position_prev": 36},
            {"query": "ткань кордура", "frequency": 255, "position": 49, "position_prev": 39},
            {"query": "эластичная ткань", "frequency": 624, "position": 36, "position_prev": 34},
            {"query": "мембранная ткань", "frequency": 1803, "position": 3, "position_prev": 2},
            {"query": "ленточная фурнитура", "frequency": 5, "position": 1, "position_prev": 1}]
    benefit = ins.pf_benefit(rows, {"date": D})
    assert benefit.status == "warn" and "частотностью меньше 20" in benefit.text

    traces = ins.pf_far_ctr(window())
    calm = ins.pf_engine_share(weeks(990, 1210))
    status, text = ins.pf_verdict([traces, calm, benefit])
    assert status == "warn" and "Санкций пока не видно" in text
    status, _ = ins.pf_verdict([traces, ins.pf_engine_share(weeks(600, 1180))])
    assert status == "alert"
    assert ins.pf_verdict([calm])[0] == "ok"
    assert [f.kind for f in ins.pf_findings([traces, calm, benefit])] == ["minus", "check"]
