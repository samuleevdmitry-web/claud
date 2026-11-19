-- Позиции заказов 1С и позиции рассылок: какой товар был в рассылке и кто его потом купил.

-- Позиции заказов (номенклатура, цвет, сумма строки) — из построчных выгрузок; заказ целиком — в orders_1c.
CREATE TABLE IF NOT EXISTS order_lines (
    order_no  text           NOT NULL,
    line_no   integer        NOT NULL,
    item      text           NOT NULL,
    item_norm text           NOT NULL,
    color     text,
    amount    numeric(14, 2) NOT NULL DEFAULT 0,
    PRIMARY KEY (order_no, line_no)
);
CREATE INDEX IF NOT EXISTS order_lines_item ON order_lines (item_norm);

-- Позиции рассылок: тема рассылки (как её назвали в файле), дата и товар из неё.
CREATE TABLE IF NOT EXISTS mailing_items (
    mailing      text NOT NULL,
    mailing_date date,
    item         text NOT NULL,
    item_norm    text NOT NULL
);
CREATE INDEX IF NOT EXISTS mailing_items_item ON mailing_items (item_norm);
CREATE INDEX IF NOT EXISTS mailing_items_mailing ON mailing_items (mailing);
