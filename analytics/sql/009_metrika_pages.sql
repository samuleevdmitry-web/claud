-- Отчёты по просмотрам страниц и товарам — помесячно, по всему трафику (фильтр роботности к ним не применяется).
CREATE TABLE IF NOT EXISTS metrika_pages (
    counter_id bigint      NOT NULL,
    month      date        NOT NULL,
    kind       text        NOT NULL,  -- page, title, not_found, not_found_from, site_search, not_found_visits
    key        text        NOT NULL,  -- адрес, заголовок, поисковая фраза…; '' для not_found_visits
    pageviews  integer     NOT NULL,  -- у not_found_visits — визиты, где был хоть один просмотр страницы 404
    users      integer     NOT NULL,
    loaded_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (counter_id, month, kind, key)
);

CREATE TABLE IF NOT EXISTS metrika_products (
    counter_id    bigint        NOT NULL,
    month         date          NOT NULL,
    product       text          NOT NULL,
    basket_sum    numeric(14, 2) NOT NULL,  -- добавлено в корзину, ₽
    basket_qty    numeric(14, 3) NOT NULL,
    purchased_sum numeric(14, 2) NOT NULL,  -- куплено, ₽
    purchased_qty numeric(14, 3) NOT NULL,
    loaded_at     timestamptz   NOT NULL DEFAULT now(),
    PRIMARY KEY (counter_id, month, product)
);

-- Какие отчёты уже догружены за всю историю: новый отчёт догружается один раз, даже если данных в нём нет.
CREATE TABLE IF NOT EXISTS report_backfills (
    counter_id bigint      NOT NULL,
    report     text        NOT NULL,
    done_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (counter_id, report)
);
