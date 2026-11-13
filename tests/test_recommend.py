"""Правила рекомендаций и выводов по новым отчётам Метрики — на синтетических цифрах."""

from analytics import insights as ins


def row(label, visits, leads, key=None, conv=None):
    return {"dim_key": key or label, "dim_label": label, "visits": visits, "leads": leads,
            "conversion_pct": conv if conv is not None else (leads / visits * 100 if visits else 0)}


def page(key, views, users=None, prev=None):
    return {"key": key, "pageviews": views, "users": users or views, "pageviews_prev": prev}


BASE = {"total": 20000, "conv": 1.2}


def titles(d):
    return [r.title for r in ins.recommend({**BASE, **d})]


def test_not_found_from_own_pages_is_a_broken_link():
    d = {"domain": "oldos.ru", "nf": {"visits": 5900}, "not_found": [page("https://oldos.ru/catalog/x/category130/", 893)],
         "not_found_from": [page("", 5000), page("https://oldos.ru/", 300), page("https://oldos.ru/?utm=1", 20)]}
    recs = ins.recommend({**BASE, **d})
    assert recs[0].priority == 1 and recs[0].title == "Убрать «Страница не найдена»"
    assert "29,5%" in recs[0].why and "/catalog/x/category130/" in recs[0].why and "Переходят на них с: / 300" in recs[0].why
    assert "Убрать «Страница не найдена»" not in titles({"nf": {"visits": 100}})  # 0,5% — не проблема


def test_not_found_from_portals_looks_like_bots():
    d = {"domain": "oldos.ru", "leads": 291, "total": 23037, "conv": 1.26, "nf": {"visits": 5915},
         "not_found": [page("https://oldos.ru/catalog/x/category130/", 893)],
         "not_found_from": [page("https://google.com/", 2065), page("https://ya.ru/", 926), page("https://oldos.ru/", 10)]}
    rec = ins.recommend(d)[0]
    assert rec.title == "Разобраться с визитами на несуществующие адреса"
    assert "google.com 2" in rec.why and "около 1,70%" in rec.why
    found = ins.not_found_findings({"visits": 5915}, 23037, d["not_found"], d["not_found_from"], "oldos.ru")
    assert [f.kind for f in found] == ["minus", "question", "check"]


def test_weak_section_with_broken_urls_points_to_404_first():
    d = {"not_found": [page("https://oldos.ru/catalog/naznach/x/category130/", 893)],
         "landing_section": [row("https://oldos.ru/catalog/naznach/", 7662, 19), row("https://oldos.ru/catalog/furnitura/", 1051, 2)]}
    got = titles(d)
    assert "Пересчитать разделы без визитов на 404" in got and "Доработать разделы с трафиком без заявок" in got


def test_direct_autotargeting_and_idle_campaign():
    d = {"direct_campaign": [row("55444350", 333, 0), row("55508767", 322, 4)],
         "direct_phrase": [row("Автотаргетинг", 562, 2), row("продажа ткань", 23, 2)]}
    got = titles(d)
    assert "Сузить автотаргетинг" in got and "Кампания 55444350 без заявок" in got
    found = ins.direct_findings(d["direct_campaign"], d["direct_phrase"], 1.2)
    assert any("автотаргетинг — 85,8%" in f.text for f in found)


def test_channels_sections_mobile_and_cart():
    d = {"source": [row("Переходы из рекомендательных систем", 1249, 0), row("Переходы из поисковых систем", 11936, 202)],
         "landing_section": [row("https://oldos.ru/catalog/furnitura/", 1051, 2), row("https://oldos.ru/catalog/tkani/", 4617, 80)],
         "device": [row("ПК", 10000, 120, "desktop"), row("Смартфоны", 9000, 40, "mobile")],
         "shop": {"cart_visits": 354, "purchase_visits": 12}}
    got = titles(d)
    assert "Разобраться с каналом «Переходы из рекомендательных систем»" in got
    assert "Доработать разделы с трафиком без заявок" in got
    assert "Упростить путь до заявки с телефона" in got
    assert "Довести корзину до заказа" in got
    assert got == sorted(got, key=lambda t: next(r.priority for r in ins.recommend({**BASE, **d}) if r.title == t))


def test_site_search_loyalty_and_foreign():
    d = {"site_search": [page("оксфорд", 196, 50), page("флис", 189, 97), page(" ", 120)],
         "visit_number": [row("1", 15000, 60), row("2", 1500, 20), row("3", 800, 15)],
         "country": [row("Россия", 18000, 280), row("Китай", 1900, 0), row("США", 90, 0)]}
    recs = {r.title: r for r in ins.recommend({**BASE, **d})}
    assert "«оксфорд», «флис»." in recs["Вынести в меню то, что ищут поиском"].why
    assert "Возвращать тех, кто не решился сразу" in recs
    assert "Китай 1 900" in recs["Отсечь нецелевые страны"].why.replace("\xa0", " ")


def test_quiet_month_gives_no_recommendations():
    assert ins.recommend({**BASE}) == []


def test_findings_for_new_reports():
    nf = ins.not_found_findings({"visits": 5900}, 20000, [page("https://oldos.ru/a/", 893)], [page("https://oldos.ru/", 300)],
                                "oldos.ru")
    assert [f.kind for f in nf] == ["minus", "check", "check"]
    search = ins.site_search_findings([page("оксфорд", 196, 50, prev=60), page("флис", 189, 97, prev=180)])
    assert search[0].kind == "question" and "«оксфорд» 60 → 196" in search[1].text
    loyal = ins.loyalty_findings([row("1", 15000, 60), row("2", 1500, 30)])
    assert loyal[0].kind == "question"
    shop = ins.products_findings([{"product": "Ткань", "basket_sum": 50000, "purchased_sum": 0}],
                                 {"basket_sum": 50000}, 354, 12)
    assert [f.kind for f in shop] == ["minus", "check"]
