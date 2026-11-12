-- RabbitSeo: позиции сайта по запросам проекта на каждый день съёма.
-- position IS NULL — в этот день сайт по запросу в выдаче не найден.
CREATE TABLE IF NOT EXISTS rabbit_positions (
    project   text        NOT NULL,  -- сайт проекта, например 'oldos.ru'
    date      date        NOT NULL,
    query     text        NOT NULL,
    frequency integer     NOT NULL,  -- частотность запроса по данным RabbitSeo
    position  integer,
    loaded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project, date, query)
);

-- Итоги проекта по дням: сколько запросов в ТОП-3, ТОП-10, найдено вообще.
CREATE OR REPLACE VIEW rabbit_daily AS
SELECT project, date,
       count(*) FILTER (WHERE position <= 3)::integer  AS top3,
       count(*) FILTER (WHERE position <= 10)::integer AS top10,
       count(position)::integer                        AS found,
       count(*)::integer                               AS queries
FROM rabbit_positions
GROUP BY project, date;
