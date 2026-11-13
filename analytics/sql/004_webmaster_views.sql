-- Формулы показателей описаны в docs/metrics.md; менять их нужно в обоих местах.

-- Поиск по месяцам с прошлым месяцем для сравнения. Средние позиции взвешены
-- показами (позиция показа) и кликами (позиция клика), как в интерфейсе Вебмастера.
CREATE OR REPLACE VIEW webmaster_search_monthly AS
WITH m AS (
    SELECT host_id,
           date_trunc('month', date)::date AS month,
           sum(shows)::integer  AS shows,
           sum(clicks)::integer AS clicks,
           round(sum(avg_show_position * shows) / nullif(sum(shows) FILTER (WHERE avg_show_position IS NOT NULL), 0), 2)
               AS avg_show_position,
           round(sum(avg_click_position * clicks) / nullif(sum(clicks) FILTER (WHERE avg_click_position IS NOT NULL), 0), 2)
               AS avg_click_position,
           count(*)::integer AS days_with_data
    FROM webmaster_search_daily
    GROUP BY host_id, date_trunc('month', date)
)
SELECT cur.host_id,
       cur.month,
       cur.shows,
       cur.clicks,
       round(100.0 * cur.clicks / nullif(cur.shows, 0), 2)   AS ctr_pct,
       cur.avg_show_position,
       cur.avg_click_position,
       cur.days_with_data,
       extract(day from cur.month + interval '1 month' - interval '1 day')::integer AS days_in_month,
       prev.shows                                             AS shows_prev,
       prev.clicks                                            AS clicks_prev,
       round(100.0 * prev.clicks / nullif(prev.shows, 0), 2) AS ctr_pct_prev,
       prev.avg_show_position                                 AS avg_show_position_prev,
       prev.avg_click_position                                AS avg_click_position_prev
FROM m cur
LEFT JOIN m prev ON prev.host_id = cur.host_id
                AND prev.month = (cur.month - interval '1 month')::date;

-- Показы по диапазонам позиций (по средней позиции запроса за день).
CREATE OR REPLACE VIEW webmaster_position_buckets AS
SELECT host_id,
       date,
       CASE WHEN round(position) <= 1  THEN '1'
            WHEN round(position) <= 3  THEN '2-3'
            WHEN round(position) <= 10 THEN '4-10'
            WHEN round(position) <= 50 THEN '11-50'
            ELSE '51+' END AS position_range,
       sum(impressions)::integer AS impressions,
       sum(clicks)::integer      AS clicks
FROM webmaster_query_daily
WHERE impressions > 0 AND position IS NOT NULL
GROUP BY host_id, date, 3;

-- Свежесть данных: если Вебмастер давно не обновлялся, мониторинг должен об этом сказать.
-- Отставание считается от дня последней успешной загрузки Вебмастера, а не от сегодня: сбор идёт раз в неделю,
-- и к концу недели данные в базе закономерно «старее» — это не задержка Вебмастера.
CREATE OR REPLACE VIEW webmaster_freshness AS
SELECT host_id, stream, last_date,
       coalesce((SELECT max(finished_at) FROM sync_runs WHERE source = 'webmaster' AND status = 'ok')::date,
                current_date) - last_date AS days_stale
FROM (
    SELECT host_id, 'search' AS stream, max(date) AS last_date FROM webmaster_search_daily GROUP BY host_id
    UNION ALL
    SELECT host_id, 'query_analytics', max(date) FROM webmaster_query_daily GROUP BY host_id
    UNION ALL
    SELECT host_id, 'pages_in_search', max(date) FROM webmaster_pages_in_search GROUP BY host_id
    UNION ALL
    SELECT host_id, 'crawl', max(date) FROM webmaster_crawl_daily GROUP BY host_id
    UNION ALL
    SELECT host_id, 'sqi', max(date) FROM webmaster_sqi GROUP BY host_id
) s;
