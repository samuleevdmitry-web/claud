-- Рассылки по базе и заказы: кому ушло письмо и что с ним сделали, о чём письмо, все заказы 1С.
-- Почта, телефоны и названия клиентов — только отпечатками (analytics/privacy.py).

-- Отчёт о доставке UniSender: статус письма у каждого адреса.
CREATE TABLE IF NOT EXISTS unisender_delivery (
    campaign_id bigint  NOT NULL,
    email_hash  text    NOT NULL,
    status      text    NOT NULL,  -- как у UniSender: ok_delivered, ok_read, ok_link_visited, err_…
    level       smallint NOT NULL, -- 0 не доставлено, 1 доставлено, 2 открыто, 3 переход по ссылке
    PRIMARY KEY (campaign_id, email_hash)
);
CREATE INDEX IF NOT EXISTS unisender_delivery_email ON unisender_delivery (email_hash);

-- Ссылки из текста письма — по ним видно, про какие товары письмо.
CREATE TABLE IF NOT EXISTS unisender_links (
    campaign_id bigint NOT NULL,
    url         text   NOT NULL,
    text        text   NOT NULL DEFAULT '',
    PRIMARY KEY (campaign_id, url, text)
);

-- Когда забирали отчёт о доставке и ссылки рассылки (открытия копятся, свежие рассылки перечитываются).
CREATE TABLE IF NOT EXISTS unisender_recipients_runs (
    campaign_id bigint      PRIMARY KEY,
    recipients  integer     NOT NULL,
    fetched_at  timestamptz NOT NULL DEFAULT now()
);

-- Все заказы 1С из ежемесячной выгрузки (папка imports).
CREATE TABLE IF NOT EXISTS orders_1c (
    order_no     text           PRIMARY KEY,
    order_date   date           NOT NULL,
    amount       numeric(14, 2) NOT NULL DEFAULT 0,
    status       text,
    client_hash  text,                           -- отпечаток названия клиента: собрать его заказы вместе
    email_hashes text[]         NOT NULL DEFAULT '{}',
    phone_hashes text[]         NOT NULL DEFAULT '{}',
    items        text[]         NOT NULL DEFAULT '{}',  -- номенклатура
    groups       text[]         NOT NULL DEFAULT '{}'   -- группы номенклатуры
);
CREATE INDEX IF NOT EXISTS orders_1c_date ON orders_1c (order_date);
CREATE INDEX IF NOT EXISTS orders_1c_client ON orders_1c (client_hash);
