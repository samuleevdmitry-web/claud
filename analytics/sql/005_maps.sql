-- Счётчики Метрики: сайт ('site') и карточки на Картах ('maps'), которые создаёт Яндекс Бизнес.
CREATE TABLE IF NOT EXISTS metrika_counters (
    counter_id bigint      PRIMARY KEY,
    name       text        NOT NULL,
    site       text,
    kind       text        NOT NULL CHECK (kind IN ('site', 'maps')),
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Действия в карточке: цели счётчика Карт, разобранные по названию («Клик на позвонить - Олдос» и т. п.).
CREATE OR REPLACE VIEW maps_actions AS
SELECT s.counter_id, s.grain, s.period_start, s.slice, s.dim_key, a.action,
       sum(s.reaches)::integer AS reaches
FROM metrika_goal_stats s
JOIN metrika_goals g USING (counter_id, goal_id)
JOIN metrika_counters c USING (counter_id)
CROSS JOIN LATERAL (
    SELECT CASE
        WHEN g.name LIKE 'Клик на позвонить%'                       THEN 'calls'
        WHEN g.name LIKE 'Построить маршрут%'                       THEN 'routes'
        WHEN g.name LIKE 'Переход на сайт%'                         THEN 'site'
        WHEN g.name LIKE 'Движение по маршруту%'                    THEN 'route_trips'
        WHEN g.name LIKE 'Переход в мессенджеры и социальные сети%' THEN 'messengers'
    END AS action
) a
WHERE c.kind = 'maps' AND a.action IS NOT NULL
GROUP BY s.counter_id, s.grain, s.period_start, s.slice, s.dim_key, a.action;

-- Карточка по месяцам с прошлым месяцем (слайд «Яндекс Карты» в отчёте).
CREATE OR REPLACE VIEW maps_monthly AS
WITH m AS (
    SELECT t.counter_id,
           t.period_start AS month,
           t.visits       AS card_visits,
           coalesce(sum(a.reaches) FILTER (WHERE a.action = 'calls'), 0)       AS calls,
           coalesce(sum(a.reaches) FILTER (WHERE a.action = 'routes'), 0)      AS routes,
           coalesce(sum(a.reaches) FILTER (WHERE a.action = 'site'), 0)        AS site,
           coalesce(sum(a.reaches) FILTER (WHERE a.action = 'route_trips'), 0) AS route_trips,
           coalesce(sum(a.reaches) FILTER (WHERE a.action = 'messengers'), 0)  AS messengers
    FROM metrika_traffic t
    JOIN metrika_counters c ON c.counter_id = t.counter_id AND c.kind = 'maps'
    LEFT JOIN maps_actions a ON a.counter_id = t.counter_id AND a.grain = t.grain
                            AND a.period_start = t.period_start AND a.slice = t.slice AND a.dim_key = t.dim_key
    WHERE t.grain = 'month' AND t.slice = 'total'
    GROUP BY t.counter_id, t.period_start, t.visits
)
SELECT cur.counter_id, c.name, cur.month,
       cur.card_visits, prev.card_visits AS card_visits_prev,
       cur.calls,       prev.calls       AS calls_prev,
       cur.routes,      prev.routes      AS routes_prev,
       cur.site,        prev.site        AS site_prev,
       cur.route_trips, cur.messengers
FROM m cur
JOIN metrika_counters c USING (counter_id)
LEFT JOIN m prev ON prev.counter_id = cur.counter_id AND prev.month = (cur.month - interval '1 month')::date;
