-- Журнал запусков сборщиков: по нему мониторинг поймёт, что данные перестали обновляться.
CREATE TABLE IF NOT EXISTS sync_runs (
    id          bigserial PRIMARY KEY,
    source      text        NOT NULL,
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    status      text        NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'ok', 'error')),
    date_from   date,
    date_to     date,
    rows_loaded integer,
    error       text
);

CREATE TABLE IF NOT EXISTS metrika_goals (
    counter_id bigint      NOT NULL,
    goal_id    bigint      NOT NULL,
    name       text        NOT NULL,
    type       text,
    is_lead    boolean     NOT NULL DEFAULT false,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (counter_id, goal_id)
);

-- Трафик по срезам. Месячные строки (grain = 'month') грузятся из Метрики отдельно, а не
-- суммируются из дневных: посетители и средние показатели из дней не складываются.
CREATE TABLE IF NOT EXISTS metrika_traffic (
    counter_id       bigint         NOT NULL,
    grain            text           NOT NULL CHECK (grain IN ('day', 'month')),
    period_start     date           NOT NULL,
    slice            text           NOT NULL,  -- total | source | utm | city | device
    dim_key          text           NOT NULL,  -- ключ значения среза, '' для total
    dim_label        text           NOT NULL,
    visits           integer        NOT NULL,
    users            integer        NOT NULL,
    pageviews        integer        NOT NULL,
    bounce_rate      numeric(6, 2),
    page_depth       numeric(8, 3),
    avg_duration_sec numeric(10, 1),
    sampled          boolean        NOT NULL DEFAULT false,
    loaded_at        timestamptz    NOT NULL DEFAULT now(),
    PRIMARY KEY (counter_id, grain, period_start, slice, dim_key)
);

-- Строки с нулями не хранятся: нет строки — цель в этом срезе не достигалась.
CREATE TABLE IF NOT EXISTS metrika_goal_stats (
    counter_id   bigint      NOT NULL,
    grain        text        NOT NULL CHECK (grain IN ('day', 'month')),
    period_start date        NOT NULL,
    slice        text        NOT NULL,
    dim_key      text        NOT NULL,
    goal_id      bigint      NOT NULL,
    reaches      integer     NOT NULL,  -- достижения цели
    visits       integer     NOT NULL,  -- визиты, в которых цель достигнута
    loaded_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (counter_id, grain, period_start, slice, dim_key, goal_id)
);
