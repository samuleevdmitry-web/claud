-- Справочник компаний (контактов, лидов) из Битрикс24: связывает клиента 1С с его почтами и телефонами.
-- Название, почты и телефоны — только при EMAIL_LISTS=on; отпечатки — всегда.

CREATE TABLE IF NOT EXISTS companies (
    company_id  bigserial PRIMARY KEY,
    source      text NOT NULL,  -- sha256 файла: каждый файл заменяет только свои строки
    row_no      integer NOT NULL DEFAULT 0,  -- номер строки в файле: по нему пакетом добавляются контакты
    company_key text,           -- отпечаток названия без формы собственности (crm_files.company_key)
    inn_hash    text,
    name        text,
    emails      text[] NOT NULL DEFAULT '{}',
    phones      text[] NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS companies_key ON companies (company_key);
CREATE INDEX IF NOT EXISTS companies_inn ON companies (inn_hash);
CREATE INDEX IF NOT EXISTS companies_source ON companies (source, row_no);

CREATE TABLE IF NOT EXISTS company_contacts (
    company_id bigint NOT NULL,
    email_hash text,
    phone_hash text
);
CREATE INDEX IF NOT EXISTS company_contacts_company ON company_contacts (company_id);
CREATE INDEX IF NOT EXISTS company_contacts_email ON company_contacts (email_hash);
CREATE INDEX IF NOT EXISTS company_contacts_phone ON company_contacts (phone_hash);

-- У заказов 1С — ключ названия компании и ИНН для сверки со справочником.
ALTER TABLE orders_1c ADD COLUMN IF NOT EXISTS company_key text;
ALTER TABLE orders_1c ADD COLUMN IF NOT EXISTS inn_hash text;
CREATE INDEX IF NOT EXISTS orders_1c_company ON orders_1c (company_key);
