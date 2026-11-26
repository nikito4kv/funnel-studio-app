-- Этап 3: схема движка воронок Студии.
-- Все времена – timestamptz (внутри UTC, человеку – МСК).

-- запуск: всё, что раньше жило в launch.yaml + launch_settings + .env
CREATE TABLE tenants (
    id          bigserial PRIMARY KEY,
    slug        text NOT NULL UNIQUE,
    title       text NOT NULL DEFAULT '',
    -- канал, эфир, ВИП, подарок, ночной режим, откуда брать static, политика /start…
    settings    jsonb NOT NULL DEFAULT '{}'::jsonb,
    status      text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- боты запуска; токена здесь НЕТ – только ссылка на секрет (env:ИМЯ)
CREATE TABLE bots (
    id          bigserial PRIMARY KEY,
    tenant_id   bigint NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    platform    text NOT NULL DEFAULT 'tg' CHECK (platform IN ('tg', 'max', 'vk')),
    secret_ref  text NOT NULL,
    tg_bot_id   bigint,                -- id бота в Telegram (из токена / getMe)
    username    text,
    enabled     boolean NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (platform, secret_ref)
);

-- опубликованные версии воронок: неизменяемые, откат = публикация старой как новой
CREATE TABLE funnel_versions (
    id            bigserial PRIMARY KEY,
    tenant_id     bigint NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    funnel_id     text NOT NULL,
    version       int NOT NULL,
    graph         jsonb NOT NULL,
    source        text NOT NULL DEFAULT '',   -- откуда взят граф (файл Студии и т.п.)
    mode          text NOT NULL DEFAULT 'new-only',
    published_at  timestamptz NOT NULL DEFAULT now(),
    published_by  text NOT NULL DEFAULT '',
    comment       text NOT NULL DEFAULT '',
    report        jsonb,                      -- отчёт по живым людям на момент публикации
    UNIQUE (tenant_id, funnel_id, version)
);

-- люди запуска (одна строка на человека и платформу)
CREATE TABLE participants (
    tenant_id      bigint NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    platform       text NOT NULL,
    uid            bigint NOT NULL,
    first_name     text,
    last_name      text,
    username       text,
    phone          text,
    email          text,
    -- метки ПЕРВОГО входа (first-touch); каждый /start отдельно пишется в actions
    utm_source     text,
    utm_medium     text,
    utm_campaign   text,
    utm_content    text,
    utm_term       text,
    trigger        text,
    referrer_code  text,
    first_payload  text,
    phone_auto_skipped boolean NOT NULL DEFAULT false,
    blocked        boolean NOT NULL DEFAULT false,
    blocked_at     timestamptz,
    created_at     timestamptz NOT NULL,
    updated_at     timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, platform, uid)
);

-- ЯВНАЯ позиция человека в воронке (раньше: «id джобы в Redis + последний sent»)
CREATE TABLE positions (
    tenant_id   bigint NOT NULL,
    platform    text NOT NULL,
    uid         bigint NOT NULL,
    funnel_id   text NOT NULL,
    version     int NOT NULL,
    step_id     uuid,                  -- последний выполненный шаг (по id, не по имени)
    step_key    text,
    branch      text,
    webinar_at  timestamptz,           -- эфир этого человека (из branch_router)
    status      text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'done', 'stopped')),
    awaiting    jsonb,                 -- ждём от человека: {"kind": "contact"|"email", "step_id", "version"}
    entered_at  timestamptz NOT NULL,
    updated_at  timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, platform, uid, funnel_id),
    FOREIGN KEY (tenant_id, platform, uid) REFERENCES participants ON DELETE CASCADE
);

-- очередь шагов: вместо пиклов APScheduler в Redis
CREATE TABLE jobs (
    id             bigserial PRIMARY KEY,
    tenant_id      bigint NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    platform       text NOT NULL,
    uid            bigint NOT NULL,
    funnel_id      text NOT NULL,
    version        int NOT NULL,
    step_id        uuid NOT NULL,
    step_key       text NOT NULL DEFAULT '',
    -- fire – выполнить шаг; also – то же, пришло параллельной стрелкой;
    -- skip – «человек не ответил» у запроса телефона / почты
    kind           text NOT NULL CHECK (kind IN ('fire', 'also', 'skip')),
    slot           text GENERATED ALWAYS AS (CASE WHEN kind = 'skip' THEN 'skip' ELSE 'fire' END) STORED,
    run_at         timestamptz NOT NULL,
    status         text NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending', 'running', 'done', 'failed', 'cancelled')),
    attempts       int NOT NULL DEFAULT 0,
    lease_until    timestamptz,
    locked_by      text,
    reason         text NOT NULL DEFAULT '',  -- откуда пришла; для отмены/сбоя – почему
    idem_key       text NOT NULL UNIQUE,
    parent_job_id  bigint,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    finished_at    timestamptz
);
-- «один ожидающий таймер на шаг человека» – как replace_existing у APScheduler
CREATE UNIQUE INDEX jobs_one_pending ON jobs (tenant_id, platform, uid, funnel_id, step_id, slot)
    WHERE status = 'pending';
CREATE INDEX jobs_due ON jobs (run_at) WHERE status = 'pending';
CREATE INDEX jobs_lease ON jobs (lease_until) WHERE status = 'running';
CREATE INDEX jobs_user ON jobs (tenant_id, platform, uid) WHERE status IN ('pending', 'running');
CREATE INDEX jobs_step ON jobs (tenant_id, funnel_id, step_id) WHERE status = 'pending';

-- что и когда ушло; одна запись на задачу – по ней повтор задачи не шлёт второй раз
CREATE TABLE sent_messages (
    id           bigserial PRIMARY KEY,
    tenant_id    bigint NOT NULL,
    platform     text NOT NULL,
    uid          bigint NOT NULL,
    funnel_id    text,
    version      int,
    step_id      uuid,
    step_key     text,
    job_id       bigint,
    status       text NOT NULL CHECK (status IN ('sending', 'ok', 'error')),
    message_ids  bigint[] NOT NULL DEFAULT '{}',
    error        text,
    created_at   timestamptz NOT NULL,
    sent_at      timestamptz
);
CREATE UNIQUE INDEX sent_one_per_job ON sent_messages (job_id) WHERE job_id IS NOT NULL;
CREATE INDEX sent_user ON sent_messages (tenant_id, platform, uid, created_at);

-- лог событий: каждый /start с сырым payload, шаги, клеймы, контакты, гейты…
CREATE TABLE actions (
    id          bigserial PRIMARY KEY,
    tenant_id   bigint NOT NULL,
    platform    text NOT NULL,
    uid         bigint NOT NULL,
    action      text NOT NULL,
    funnel_id   text,
    version     int,
    step_id     uuid,
    step_key    text,
    payload     text,
    data        jsonb,
    job_id      bigint,
    created_at  timestamptz NOT NULL
);
CREATE INDEX actions_user ON actions (tenant_id, platform, uid, created_at);
CREATE INDEX actions_kind ON actions (tenant_id, action, created_at);

-- медиа: sha256 файла + бот → file_id (TG заливается байтами один раз на бот)
CREATE TABLE media_cache (
    sha256      text NOT NULL,
    bot_id      bigint NOT NULL,
    kind        text NOT NULL,        -- photo / video / video_note / animation
    file_id     text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (sha256, bot_id, kind)
);
