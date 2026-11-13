-- Выгрузки из Битрикс24 и 1С, которые раз в месяц кладутся в папку imports (лиды с форм, входящие звонки,
-- заказы по ним). Персональных данных нет: имён, телефонов, почты и названий клиентов база не хранит.

CREATE TABLE IF NOT EXISTS crm_imports (
    sha256      text        PRIMARY KEY,
    file_name   text        NOT NULL,
    kind        text        NOT NULL,  -- forms | calls
    month       date        NOT NULL,
    rows_loaded integer     NOT NULL,
    imported_at timestamptz NOT NULL DEFAULT now()
);

-- Итоговые показатели из шапки файла — как их посчитал автор сводки.
CREATE TABLE IF NOT EXISTS crm_summary (
    month  date    NOT NULL,
    kind   text    NOT NULL,
    metric text    NOT NULL,
    value  numeric,
    note   text,
    PRIMARY KEY (month, kind, metric)
);

CREATE TABLE IF NOT EXISTS crm_form_leads (
    lead_id     bigint      PRIMARY KEY,
    month       date        NOT NULL,
    created_at  timestamp,
    stage       text        NOT NULL,
    in_1c       boolean     NOT NULL,  -- клиент нашёлся в 1С с заказом после лида
    orders      integer     NOT NULL DEFAULT 0,
    amount      numeric(14, 2) NOT NULL DEFAULT 0,      -- сумма заказов после лида
    closed      numeric(14, 2) NOT NULL DEFAULT 0,      -- из них «Закрыт» / «Готов к закрытию»
    first_order date,
    match       text,       -- точно / вероятно / возможно
    manager_1c  text
);

CREATE TABLE IF NOT EXISTS crm_call_leads (
    lead_id     bigint      PRIMARY KEY,
    month       date        NOT NULL,
    called_at   timestamp,  -- в выгрузке у части звонков даты нет
    call_type   text        NOT NULL,  -- Входящий | Пропущенный входящий
    stage       text        NOT NULL,
    responsible text,
    line        text,                  -- куда звонили: номер линии или её название
    in_1c       boolean     NOT NULL
);

-- Заказы 1С после входящих звонков: новая продажа или заказ действующего клиента.
CREATE TABLE IF NOT EXISTS crm_call_orders (
    month      date           NOT NULL,
    order_no   text           NOT NULL,
    category   text           NOT NULL,  -- new | existing
    order_date date,
    amount     numeric(14, 2) NOT NULL,
    status_1c  text,
    lead_id    bigint,
    manager_1c text,
    PRIMARY KEY (month, order_no)
);
