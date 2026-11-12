-- Представления для дашборда.
DROP VIEW IF EXISTS metrika_contacts, metrika_month_coverage CASCADE;

-- Контакты: клики по телефону, почте и мессенджерам (по типу цели Метрики), кроме целей-заявок.
CREATE VIEW metrika_contacts AS
SELECT s.counter_id, s.audience, s.grain, s.period_start, s.slice, s.dim_key,
       sum(s.reaches)::integer AS contacts
FROM metrika_goal_stats s
JOIN metrika_goals g USING (counter_id, goal_id)
WHERE g.type IN ('phone', 'email', 'messenger') AND NOT g.is_lead
GROUP BY s.counter_id, s.audience, s.grain, s.period_start, s.slice, s.dim_key;

-- Сайт в Метрике и тот же сайт в Вебмастере: совпадение домена без www
-- ('oldos.ru' и 'https:www.oldos.ru:443').
CREATE OR REPLACE VIEW site_hosts AS
SELECT c.counter_id, h.host_id
FROM metrika_counters c
JOIN (SELECT DISTINCT host_id FROM webmaster_summary) h
  ON regexp_replace(split_part(h.host_id, ':', 2), '^www\.', '')
   = regexp_replace(lower(coalesce(c.site, '')), '^(https?://)?(www\.)?', '')
WHERE c.kind = 'site';

-- Сколько дней месяца есть в данных счётчика: месяц с провалом нельзя честно сравнивать.
CREATE VIEW metrika_month_coverage AS
SELECT counter_id,
       audience,
       date_trunc('month', period_start)::date AS month,
       count(*)::integer                        AS days_with_data
FROM metrika_traffic
WHERE grain = 'day' AND slice = 'total' AND visits > 0
GROUP BY counter_id, audience, date_trunc('month', period_start);
