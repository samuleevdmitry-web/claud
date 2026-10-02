"""Классификатор на реальном файле ключевых слов."""

from app.classifier import PositionText, Relevance, TenderText, classify


def rel(ks, title, customer="", **kw):
    return classify(ks, TenderText(title=title, customer_name=customer, **kw)).relevance


# --------------------------------------------------------------------------- обязательные кейсы из ТЗ


def test_soft_inventory_for_sanatorium_is_relevant(ks):
    assert (
        rel(
            ks,
            "Поставка мягкого инвентаря для нужд ФГБУ «Санаторий «Сосновый бор»",
            "ФГБУ «Санаторий «Сосновый бор» Управления делами Президента РФ",
        )
        == Relevance.RELEVANT
    )


def test_bed_linen_for_hospital_is_rejected(ks):
    assert rel(ks, "Поставка постельного белья", "ГБУЗ «Тверская областная больница»") == Relevance.REJECTED


def test_towels_without_marker_go_to_review(ks):
    assert rel(ks, "Поставка полотенец", "ООО «Альфа-Снаб»") == Relevance.REVIEW


def test_disposable_slippers_are_relevant(ks):
    assert rel(ks, "Закупка тапочек одноразовых") == Relevance.RELEVANT


def test_toppers_for_hotel_complex_are_relevant(ks):
    assert rel(ks, "Поставка топперов и наматрасников для гостиничного комплекса") == Relevance.RELEVANT


def test_rent_of_soft_inventory_for_sanatorium_goes_to_review(ks):
    result = classify(ks, TenderText("Аренда мягкого инвентаря", customer_name="АО «Санаторий «Дюны»"))
    assert result.relevance == Relevance.REVIEW
    assert any(m.kind == "minus" and m.fragment == "Аренда" for m in result.matches)


# --------------------------------------------------------------------------- дополнительные правила


def test_no_product_keys_returns_none(ks):
    result = classify(ks, TenderText("Поставка продуктов питания", customer_name="Санаторий «Дюны»"))
    assert result.relevance is None


def test_needs_marker_key_with_marker_in_delivery_place(ks):
    assert (
        rel(
            ks,
            "Поставка подушек",
            "ООО «Ромашка»",
            delivery_place="Краснодарский край, г. Сочи, пансионат «Волна»",
        )
        == Relevance.RELEVANT
    )


def test_strong_minus_only_in_positions_goes_to_review(ks):
    tender = TenderText(
        "Поставка гостиничного текстиля",
        positions=[PositionText("Полотенце махровое"), PositionText("Пелёнка медицинская")],
    )
    assert classify(ks, tender).relevance == Relevance.REVIEW


def test_medium_minus_downgrades_relevant_to_review(ks):
    assert rel(ks, "Поставка тапочек одноразовых для пассажирских вагонов") == Relevance.REVIEW


def test_strong_minus_in_title_with_marker_goes_to_review(ks):
    assert rel(ks, "Поставка постельного белья медицинского назначения для санатория") == Relevance.REVIEW


def test_self_key_with_strong_minus_and_no_marker_is_rejected(ks):
    assert rel(ks, "Закупка тапочек одноразовых", "ГБУЗ «Городская поликлиника № 3»") == Relevance.REJECTED


def test_okpd_code_with_marker_is_relevant(ks):
    tender = TenderText(
        "Поставка товаров",
        customer_name="ООО «Отель Белый Сад»",
        positions=[PositionText("Изделие текстильное", "13.92.12.110")],
    )
    result = classify(ks, tender)
    assert result.relevance == Relevance.RELEVANT
    assert any(m.kind == "code" and m.label == "13.92.12.110" for m in result.matches)


def test_okpd_code_without_marker_goes_to_review(ks):
    assert rel(ks, "Поставка товаров", "ООО «Ромашка»", codes=["13.92.14.110"]) == Relevance.REVIEW


def test_excluded_code_does_not_count(ks):
    tender = TenderText("Поставка товаров", customer_name="Санаторий «Дюны»", codes=["13.92.12.160"])
    result = classify(ks, tender)
    assert result.relevance is None
    assert [m.kind for m in result.matches if m.field == "codes"] == ["code_excluded"]


def test_minus_inside_product_phrase_is_suppressed(ks):
    result = classify(ks, TenderText("Поставка дорожек на кровать для отеля"))
    assert result.relevance == Relevance.RELEVANT
    minus = [m for m in result.matches if m.kind == "minus"]
    assert minus and all(m.suppressed for m in minus)


def test_otel_does_not_match_otdel(ks):
    result = classify(ks, TenderText("Поставка постельного белья", customer_name="Отдел образования"))
    assert not [m for m in result.matches if m.kind == "marker"]
    assert result.relevance == Relevance.REVIEW


def test_refining_only_goes_to_review(ks):
    assert rel(ks, "Поставка ткани страйп-сатин", "ООО «Ромашка»") == Relevance.REVIEW


def test_notice_text_is_searched(ks):
    tender = TenderText(
        "Поставка товаров",
        customer_name="ООО «Ромашка»",
        notice_text="Техническое задание: халат махровый гостиничный, 365 г/м2, для номерного фонда",
    )
    assert classify(ks, tender).relevance == Relevance.RELEVANT


def test_matches_store_field_fragment_and_offsets(ks):
    title = "Поставка «Тапочек одноразовых» для гостей"
    result = classify(ks, TenderText(title))
    m = next(m for m in result.matches if m.label == "тапочки одноразовые")
    assert m.field == "title"
    assert m.fragment == "Тапочек одноразовых"
    assert title[m.start : m.end] == m.fragment


# --------------------------------------------------------------------------- score


def test_score_orders_rich_hotel_tender_above_plain_one(ks):
    rich = classify(
        ks,
        TenderText(
            "Оснащение номерного фонда: постельное бельё страйп-сатин, полотенца, халаты, тапочки",
            customer_name="ООО «Гостиница Москва»",
        ),
    )
    plain = classify(ks, TenderText("Поставка полотенец", customer_name="ООО «Альфа»"))
    assert rich.score > plain.score
    assert len(rich.categories) >= 4


def test_customer_marker_adds_bonus(ks):
    in_customer = classify(ks, TenderText("Поставка подушек", customer_name="Санаторий «Дюны»"))
    in_title = classify(ks, TenderText("Поставка подушек для санатория", customer_name="ООО «Ромашка»"))
    assert in_customer.score > in_title.score


def test_minus_word_in_phrase_gap_is_not_suppressed(ks):
    # «детского» попало в промежуток фразы «комплект … постельного белья», но это не слово ключа
    result = classify(ks, TenderText("Поставка комплекта детского постельного белья с вышивкой",
                                     customer_name="БУ «Социально-реабилитационный центр»"))
    minus = [m for m in result.matches if m.kind == "minus" and m.fragment.lower() == "детского"]
    assert minus and not minus[0].suppressed
    assert result.relevance == Relevance.REVIEW
