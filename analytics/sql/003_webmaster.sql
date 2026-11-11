-- Яндекс Вебмастер. host_id в формате API: 'https:www.oldos.ru:443'.
-- Дней, за которые Вебмастер ещё не отдал данные, в таблицах нет (это не нули).

CREATE TABLE IF NOT EXISTS webmaster_search_daily (
    host_id            text        NOT NULL,
    date               date        NOT NULL,
    shows              integer     NOT NULL,
    clicks             integer     NOT NULL,
    avg_show_position  numeric(7, 2),
    avg_click_position numeric(7, 2),
    loaded_at          timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (host_id, date)
);

-- Популярные запросы за месяц (для текущего месяца — с начала месяца).
CREATE TABLE IF NOT EXISTS webmaster_queries_monthly (
    host_id            text        NOT NULL,
    month              date        NOT NULL,
    query_id           text        NOT NULL,
    query_text         text        NOT NULL,
    shows              integer     NOT NULL,
    clicks             integer     NOT NULL,
    avg_show_position  numeric(7, 2),
    avg_click_position numeric(7, 2),
    loaded_at          timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (host_id, month, query_id)
);

-- Аналитика запросов: API отдаёт только последние 14 дней, история копится от запуска к запуску.
CREATE TABLE IF NOT EXISTS webmaster_query_daily (
    host_id     text        NOT NULL,
    date        date        NOT NULL,
    query       text        NOT NULL,
    top_url     text,
    impressions integer     NOT NULL,
    clicks      integer     NOT NULL,
    position    numeric(7, 2),
    demand      integer,      -- спрос: сколько раз запрос задавали в Яндексе
    loaded_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (host_id, date, query)
);

CREATE TABLE IF NOT EXISTS webmaster_sqi (
    host_id   text        NOT NULL,
    date      date        NOT NULL,
    sqi       integer     NOT NULL,
    loaded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (host_id, date)
);

CREATE TABLE IF NOT EXISTS webmaster_pages_in_search (
    host_id   text        NOT NULL,
    date      date        NOT NULL,
    pages     integer,
    appeared  integer,
    removed   integer,
    loaded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (host_id, date)
);

-- Страницы, загруженные роботом, по кодам ответа.
CREATE TABLE IF NOT EXISTS webmaster_crawl_daily (
    host_id   text        NOT NULL,
    date      date        NOT NULL,
    http_2xx  integer     NOT NULL,
    http_3xx  integer     NOT NULL,
    http_4xx  integer     NOT NULL,
    http_5xx  integer     NOT NULL,
    other     integer     NOT NULL,
    loaded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (host_id, date)
);

-- Текущее состояние проверок сайта; severity: FATAL, CRITICAL, POSSIBLE_PROBLEM, RECOMMENDATION.
CREATE TABLE IF NOT EXISTS webmaster_diagnostics (
    host_id           text        NOT NULL,
    problem           text        NOT NULL,
    severity          text        NOT NULL,
    state             text        NOT NULL,
    last_state_update timestamptz,
    checked_at        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (host_id, problem)
);

-- Сводка сайта на день запуска сборщика (API отдаёт только текущее значение).
CREATE TABLE IF NOT EXISTS webmaster_summary (
    host_id                 text    NOT NULL,
    snapshot_date           date    NOT NULL,
    sqi                     integer,
    searchable_pages        integer,
    excluded_pages          integer,
    problems_fatal          integer NOT NULL DEFAULT 0,
    problems_critical       integer NOT NULL DEFAULT 0,
    problems_possible       integer NOT NULL DEFAULT 0,
    problems_recommendation integer NOT NULL DEFAULT 0,
    PRIMARY KEY (host_id, snapshot_date)
);
