-- Вордстат. Ключи берутся из wordstat.toml и перезаписываются при каждой загрузке.
CREATE TABLE IF NOT EXISTS wordstat_keywords (
    phrase    text    NOT NULL,
    kind      text    NOT NULL CHECK (kind IN ('brand', 'market', 'category')),
    direction text    NOT NULL DEFAULT '',
    category  text    NOT NULL DEFAULT '',
    position  integer NOT NULL,  -- порядок в файле, для вывода
    PRIMARY KEY (kind, direction, category, phrase)
);

-- Частотность фразы по месяцам. Месячные данные Вордстат публикует с задержкой (в середине ноября
-- последний месяц — сентябрь), поэтому последний месяц в таблице обычно отстаёт от календаря.
CREATE TABLE IF NOT EXISTS wordstat_monthly (
    phrase    text             NOT NULL,
    region_id integer          NOT NULL,
    month     date             NOT NULL,
    count     integer          NOT NULL,
    share     double precision NOT NULL,
    loaded_at timestamptz      NOT NULL DEFAULT now(),
    PRIMARY KEY (phrase, region_id, month)
);

-- Популярные формулировки за 30 дней до загрузки; month — месяц загрузки, внутри месяца перезаписывается.
CREATE TABLE IF NOT EXISTS wordstat_top (
    phrase    text        NOT NULL,
    region_id integer     NOT NULL,
    month     date        NOT NULL,
    query     text        NOT NULL,
    count     integer     NOT NULL,
    loaded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (phrase, region_id, month, query)
);

-- Спрос группы — сумма её фраз. Фразы внутри группы подобраны так, чтобы почти не пересекаться
-- (через минус-слова), но небольшое пересечение возможно: это оценка спроса, а не точное число людей.
CREATE OR REPLACE VIEW wordstat_groups_monthly AS
SELECT k.kind, k.direction, k.category, m.region_id, m.month, sum(m.count)::integer AS demand
FROM wordstat_keywords k
JOIN wordstat_monthly m USING (phrase)
GROUP BY k.kind, k.direction, k.category, m.region_id, m.month;

-- Направление каталога — сумма уникальных фраз его категорий (фраза двух категорий считается один раз).
CREATE OR REPLACE VIEW wordstat_directions_monthly AS
SELECT d.direction, m.region_id, m.month, sum(m.count)::integer AS demand
FROM (SELECT DISTINCT direction, phrase FROM wordstat_keywords WHERE kind = 'category') d
JOIN wordstat_monthly m USING (phrase)
GROUP BY d.direction, m.region_id, m.month;
