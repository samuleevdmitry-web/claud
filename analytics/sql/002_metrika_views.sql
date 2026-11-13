-- Формулы показателей описаны в docs/metrics.md; менять их нужно в обоих местах.
-- Представления пересоздаются при каждом применении схемы: так меняются их колонки.
DROP VIEW IF EXISTS metrika_monthly, metrika_kpi, metrika_leads CASCADE;

-- Достижения целей-заявок: запасной вариант для строк без lead_visits (загруженных до его появления).
CREATE VIEW metrika_leads AS
SELECT s.counter_id, s.audience, s.grain, s.period_start, s.slice, s.dim_key,
       sum(s.reaches)::integer AS leads
FROM metrika_goal_stats s
JOIN metrika_goals g USING (counter_id, goal_id)
WHERE g.is_lead
GROUP BY s.counter_id, s.audience, s.grain, s.period_start, s.slice, s.dim_key;

CREATE VIEW metrika_kpi AS
SELECT t.counter_id, t.audience, t.grain, t.period_start, t.slice, t.dim_key, t.dim_label,
       t.visits, t.users, t.pageviews, t.bounce_rate, t.page_depth, t.avg_duration_sec,
       -- заявки — визиты с хоть одной целью-заявкой, как конверсия в Метрике
       coalesce(t.lead_visits, l.leads, 0) AS leads,
       round(100.0 * coalesce(t.lead_visits, l.leads, 0) / nullif(t.visits, 0), 2) AS conversion_pct,
       t.sampled
FROM metrika_traffic t
LEFT JOIN metrika_leads l USING (counter_id, audience, grain, period_start, slice, dim_key);

-- Месяц к прошлому месяцу и к тому же месяцу год назад (первый слайд отчёта).
CREATE VIEW metrika_monthly AS
WITH m AS (
    SELECT * FROM metrika_kpi WHERE grain = 'month' AND slice = 'total'
)
SELECT cur.counter_id,
       cur.audience,
       cur.period_start      AS month,
       cur.visits,           prev.visits           AS visits_prev,           yoy.visits AS visits_yoy,
       cur.users,            prev.users            AS users_prev,            yoy.users  AS users_yoy,
       cur.leads,            prev.leads            AS leads_prev,            yoy.leads  AS leads_yoy,
       cur.conversion_pct,   prev.conversion_pct   AS conversion_pct_prev,
       cur.bounce_rate,      prev.bounce_rate      AS bounce_rate_prev,
       cur.page_depth,       prev.page_depth       AS page_depth_prev,
       cur.avg_duration_sec, prev.avg_duration_sec AS avg_duration_sec_prev,
       cur.sampled
FROM m cur
LEFT JOIN m prev ON prev.counter_id = cur.counter_id AND prev.audience = cur.audience
                AND prev.period_start = (cur.period_start - interval '1 month')::date
LEFT JOIN m yoy  ON yoy.counter_id = cur.counter_id AND yoy.audience = cur.audience
                AND yoy.period_start = (cur.period_start - interval '1 year')::date;
