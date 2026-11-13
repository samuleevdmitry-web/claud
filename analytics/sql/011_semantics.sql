-- Семантическое ядро: фразы с частотностью и позициями из таблицы семантики (Google Таблица или её выгрузка).
CREATE TABLE IF NOT EXISTS semantic_keywords (
    norm       text        PRIMARY KEY,  -- фраза в нижнем регистре, «ё» → «е», без лишних пробелов
    phrase     text        NOT NULL,
    frequency  integer,                  -- частотность за год
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS semantic_positions (
    norm     text    NOT NULL,
    engine   text    NOT NULL,  -- yandex | google
    date     date    NOT NULL,
    position integer,           -- пусто — нет в проверяемой выдаче («--»)
    PRIMARY KEY (norm, engine, date)
);

-- Запросы Вебмастера сравниваются с фразами по той же нормализации.
CREATE INDEX IF NOT EXISTS webmaster_query_daily_norm
    ON webmaster_query_daily (host_id, (regexp_replace(replace(lower(query), 'ё', 'е'), '\s+', ' ', 'g')), date);
