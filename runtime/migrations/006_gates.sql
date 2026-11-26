-- Источники данных для гейтов: оплаты (вебхук кассы), отчёты Bizon, клики по прокси-ссылкам.
-- Все времена – timestamptz (внутри UTC, человеку – МСК).

-- ── оплаты ───────────────────────────────────────────────────────────────────
-- Одна строка на платёж кассы (идемпотентность по внешнему id). Запуск к платежу
-- не привязан: касса шлёт вебхук на один адрес. Какому запуску платёж «свой»,
-- решается при проверке: подпись проверена секретом запуска (verified_refs) и
-- тариф есть в его настройках.
CREATE TABLE gate_payments (
    id              bigserial PRIMARY KEY,
    external_id     text NOT NULL UNIQUE,
    event           text NOT NULL DEFAULT '',
    status          text NOT NULL,            -- paid / refunded / failed / cancelled / …
    tariff_id       text NOT NULL DEFAULT '',
    tariff_name     text,
    amount          numeric,
    currency        text,
    client_uid      bigint,                   -- id человека из ссылки (bot_meta.id / telegram_id)
    client_platform text,                     -- tg / max, если касса его вернула
    phone10         text,                     -- последние 10 цифр телефона плательщика
    email           text,
    verified_refs   text[] NOT NULL DEFAULT '{}',  -- секреты (env:ИМЯ), которыми подпись сошлась
    payload         jsonb NOT NULL,
    paid_at         timestamptz,
    received_at     timestamptz NOT NULL,
    updated_at      timestamptz NOT NULL
);
CREATE INDEX gate_payments_uid ON gate_payments (client_uid) WHERE client_uid IS NOT NULL;
CREATE INDEX gate_payments_phone ON gate_payments (phone10) WHERE phone10 IS NOT NULL;

-- Привязка платежа к человеку запуска – журнал «событие paid уже случилось».
-- Сам гейт читает gate_payments напрямую (так он видит и оплату, пришедшую
-- раньше, чем человек поделился телефоном).
CREATE TABLE gate_payment_links (
    tenant_id   bigint NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    payment_id  bigint NOT NULL REFERENCES gate_payments(id) ON DELETE CASCADE,
    platform    text NOT NULL,
    uid         bigint NOT NULL,
    product     text NOT NULL CHECK (product IN ('vip', 'course')),
    matched_by  text NOT NULL CHECK (matched_by IN ('id', 'phone')),
    created_at  timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, payment_id, platform, uid)
);

-- ── Bizon ────────────────────────────────────────────────────────────────────
-- account – ссылка на секрет токена (env:ИМЯ): у разных запусков могут быть
-- разные аккаунты Bizon, кэш не должен смешиваться.
CREATE TABLE gate_bizon_sync (
    account    text PRIMARY KEY,
    listed_at  timestamptz NOT NULL
);

CREATE TABLE gate_bizon_reports (
    account             text NOT NULL,
    webinar_id          text NOT NULL,
    room                text NOT NULL,
    started_at          timestamptz,
    viewers_fetched_at  timestamptz,   -- NULL – зрителей ещё не забрали (или эфир идёт, §27)
    viewers_count       int,
    seen_at             timestamptz NOT NULL,
    PRIMARY KEY (account, webinar_id)
);
CREATE INDEX gate_bizon_reports_room ON gate_bizon_reports (account, room, started_at);

CREATE TABLE gate_bizon_viewers (
    account     text NOT NULL,
    webinar_id  text NOT NULL,
    n           int NOT NULL,
    uid         bigint,            -- id=… из адреса, по которому зритель пришёл
    phone10     text,
    PRIMARY KEY (account, webinar_id, n)
);
CREATE INDEX gate_bizon_viewers_uid ON gate_bizon_viewers (account, uid) WHERE uid IS NOT NULL;
CREATE INDEX gate_bizon_viewers_phone ON gate_bizon_viewers (account, phone10) WHERE phone10 IS NOT NULL;

-- ── прокси-ссылки и клики ────────────────────────────────────────────────────
-- Одна ссылка на (запуск, адрес, метка, откуда); человек – в ?u= (как /go в bot-engine).
CREATE TABLE gate_links (
    id          bigserial PRIMARY KEY,
    tenant_id   bigint NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    code        text NOT NULL UNIQUE,
    target_url  text NOT NULL,
    label       text NOT NULL DEFAULT '',
    source      text NOT NULL CHECK (source IN ('button', 'text')),
    created_at  timestamptz NOT NULL,
    UNIQUE (tenant_id, target_url, label, source)
);

CREATE TABLE gate_clicks (
    id          bigserial PRIMARY KEY,
    tenant_id   bigint NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    link_id     bigint REFERENCES gate_links(id) ON DELETE SET NULL,
    platform    text NOT NULL,
    uid         bigint NOT NULL,
    label       text NOT NULL DEFAULT '',
    -- button / text / vip. В MAX ссылку ИЗ ТЕКСТА платформа «открывает» сама при
    -- доставке (§26) – человеческим кликом там считается только кнопка.
    source      text NOT NULL,
    target_url  text NOT NULL DEFAULT '',
    user_agent  text,
    is_bot      boolean NOT NULL DEFAULT false,   -- превью-роботы (TelegramBot и т.п.)
    clicked_at  timestamptz NOT NULL
);
CREATE INDEX gate_clicks_user ON gate_clicks (tenant_id, platform, uid, label text_pattern_ops);
CREATE INDEX gate_clicks_time ON gate_clicks (tenant_id, clicked_at);
