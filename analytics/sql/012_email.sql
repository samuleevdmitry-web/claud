-- Канал e-mail: рассылки UniSender, их статистика и база контактов (только отпечатки почты и телефона).
CREATE TABLE IF NOT EXISTS unisender_lists (
    list_id integer PRIMARY KEY,
    title   text
);

CREATE TABLE IF NOT EXISTS unisender_campaigns (
    campaign_id bigint    PRIMARY KEY,  -- он же utm_campaign в ссылках писем (utm_source=Unisender, utm_medium=email)
    start_time  timestamp,
    status      text,
    list_id     integer,
    message_id  bigint,
    subject     text
);

CREATE TABLE IF NOT EXISTS unisender_stats (
    campaign_id    bigint      PRIMARY KEY,
    total          integer     NOT NULL,
    sent           integer     NOT NULL,
    delivered      integer     NOT NULL,
    read_unique    integer     NOT NULL,  -- открыли письмо или перешли по ссылке (уникальные)
    read_all       integer     NOT NULL,
    clicked_unique integer     NOT NULL,  -- перешли по ссылкам (уникальные)
    clicked_all    integer     NOT NULL,
    unsubscribed   integer     NOT NULL,
    spam           integer     NOT NULL,
    updated_at     timestamptz NOT NULL DEFAULT now()
);

-- База рассылок: отпечатки почты и телефона (analytics/privacy.py), без самих адресов.
CREATE TABLE IF NOT EXISTS email_base (
    email_hash text,
    phone_hash text,
    status     text,
    list_ids   integer[] NOT NULL DEFAULT '{}',
    loaded_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS email_base_email ON email_base (email_hash);
CREATE INDEX IF NOT EXISTS email_base_phone ON email_base (phone_hash);

-- Отпечатки почты и телефона у лидов Б24 — чтобы найти среди них подписчиков.
ALTER TABLE crm_form_leads ADD COLUMN IF NOT EXISTS email_hashes text[] NOT NULL DEFAULT '{}';
ALTER TABLE crm_form_leads ADD COLUMN IF NOT EXISTS phone_hash text;
ALTER TABLE crm_call_leads ADD COLUMN IF NOT EXISTS phone_hash text;
