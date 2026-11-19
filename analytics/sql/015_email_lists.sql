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

-- Клиенты из выгрузок 1С для списка «нет в базе рассылок»: название, адреса и номера — только при EMAIL_LISTS=on.
ALTER TABLE orders_1c ADD COLUMN IF NOT EXISTS client_name text;
ALTER TABLE orders_1c ADD COLUMN IF NOT EXISTS emails text[] NOT NULL DEFAULT '{}';
ALTER TABLE orders_1c ADD COLUMN IF NOT EXISTS phones text[] NOT NULL DEFAULT '{}';
-- Файл разобран с сохранением контактов (EMAIL_LISTS=on): если списки включат позже, файл перечитается.
ALTER TABLE crm_imports ADD COLUMN IF NOT EXISTS contacts boolean NOT NULL DEFAULT false;
