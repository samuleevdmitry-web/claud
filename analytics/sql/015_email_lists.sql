-- Списки получателей рассылок (EMAIL_LISTS=on): адреса получателей и сотрудники.
-- При выключенном EMAIL_LISTS адреса и имена здесь не хранятся (стираются при следующей загрузке UniSender).

CREATE TABLE IF NOT EXISTS email_contacts (
    email_hash text        PRIMARY KEY,
    email      text        NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Сотрудники из файла пользователей (Битрикс24): чтобы видеть, кто из них читает рассылки, и не путать их с клиентами.
CREATE TABLE IF NOT EXISTS staff (
    email_hash text,
    phone_hash text,
    name       text,  -- только при EMAIL_LISTS=on
    email      text   -- только при EMAIL_LISTS=on
);
CREATE INDEX IF NOT EXISTS staff_email ON staff (email_hash);
