-- Ассистент SEO: продвигаемые ключи, отчёты и рекомендации с внедрением. Ничего не удаляется: у ключа есть дата
-- снятия с продвижения, у рекомендации — даты (впервые, последнее подтверждение, внедрение) и статус.

-- Ключи, которые продвигаем (выбраны на странице «Ассистент SEO»).
CREATE TABLE IF NOT EXISTS seo_focus (
    host_id    text        NOT NULL,
    norm       text        NOT NULL,  -- фраза в нижнем регистре, «ё» → «е», без лишних пробелов
    phrase     text        NOT NULL,
    target     smallint    NOT NULL DEFAULT 3,  -- цель — ТОП-3; 1-е место — сверхцель
    added_at   timestamptz NOT NULL DEFAULT now(),
    removed_at timestamptz,                     -- снят с продвижения (история остаётся)
    PRIMARY KEY (host_id, norm)
);

-- Отчёт ассистента: когда построен, за какие дни данных, итоги и выводы.
CREATE TABLE IF NOT EXISTS seo_reports (
    report_id   bigserial   PRIMARY KEY,
    host_id     text        NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    period_from date        NOT NULL,
    period_to   date        NOT NULL,
    summary     jsonb       NOT NULL
);
CREATE INDEX IF NOT EXISTS seo_reports_host ON seo_reports (host_id, created_at DESC);

-- Рекомендации. Одна и та же (вид + запрос) не дублируется: повторный отчёт отмечает, что она подтвердилась;
-- пропала из отчёта — статус stale («неактуальна»), но запись и даты остаются.
CREATE TABLE IF NOT EXISTS seo_recs (
    rec_id     bigserial   PRIMARY KEY,
    host_id    text        NOT NULL,
    key        text        NOT NULL,           -- вид:запрос
    lane       text        NOT NULL,           -- lost | defend | near_win | ctr | consolidate | gap | head_term | strategic
    track      text        NOT NULL,           -- near (ближняя дорожка) | lead (лидерство)
    priority   text        NOT NULL,           -- now | next | later
    norm       text,
    phrase     text,
    page       text,                           -- страница-владелец (как её показывает Яндекс)
    title      text        NOT NULL,
    observed   text        NOT NULL,           -- что видно в данных (источник и даты)
    inferred   text        NOT NULL,           -- вывод — гипотеза, которую проверяет внедрение
    actions    jsonb       NOT NULL,           -- шаги
    acceptance text        NOT NULL,           -- по чему поймём, что сработало
    baseline   jsonb       NOT NULL,           -- метрики на момент рекомендации
    focus      boolean     NOT NULL DEFAULT false,  -- план продвигаемого ключа
    report_id  bigint,
    created_at timestamptz NOT NULL DEFAULT now(),  -- впервые
    seen_at    timestamptz NOT NULL DEFAULT now(),  -- последний отчёт, где подтвердилась
    status     text        NOT NULL DEFAULT 'open', -- open | done | dismissed | stale
    status_at  timestamptz,
    done_on    date,                                -- когда внедрили
    note       text                                 -- комментарий к внедрению или откладыванию
);
CREATE UNIQUE INDEX IF NOT EXISTS seo_recs_open ON seo_recs (host_id, key) WHERE status = 'open';
CREATE INDEX IF NOT EXISTS seo_recs_host ON seo_recs (host_id, status, created_at DESC);
