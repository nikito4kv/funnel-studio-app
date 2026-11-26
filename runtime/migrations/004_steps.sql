-- Этап 3, пункт 3: виды шагов опрос / звонок / SMS / закреп эфира / rich / переход.
-- Все времена – timestamptz (внутри UTC, человеку – МСК).

-- ответы опроса: одна строка на человека запуска, ключ ответа – survey.qid
-- (как <slug>_survey_responses.answers в bot-engine)
CREATE TABLE survey_responses (
    tenant_id     bigint NOT NULL,
    platform      text NOT NULL,
    uid           bigint NOT NULL,
    answers       jsonb NOT NULL DEFAULT '{}'::jsonb,
    submitted_at  timestamptz,           -- опрос пройден (вопрос с «final»)
    created_at    timestamptz NOT NULL,
    updated_at    timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, platform, uid),
    FOREIGN KEY (tenant_id, platform, uid) REFERENCES participants ON DELETE CASCADE
);

-- заданный человеку вопрос: открыт, пока не ответил. Ответ на закрытый вопрос
-- (повторное нажатие той же кнопки) игнорируется – один ответ = один переход.
-- Повторная отправка вопроса (перезапуск воронки) открывает его снова.
CREATE TABLE survey_prompts (
    tenant_id    bigint NOT NULL,
    platform     text NOT NULL,
    uid          bigint NOT NULL,
    funnel_id    text NOT NULL,
    version      int NOT NULL,
    step_id      uuid NOT NULL,
    step_key     text NOT NULL DEFAULT '',
    qid          text NOT NULL,
    free         boolean NOT NULL DEFAULT false,   -- свободный ответ текстом
    status       text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'answered')),
    selected     jsonb NOT NULL DEFAULT '[]'::jsonb,  -- multi: отмеченные варианты
    message_id   bigint,                             -- сообщение с кнопками вопроса
    opened_at    timestamptz NOT NULL,
    answered_at  timestamptz,
    PRIMARY KEY (tenant_id, platform, uid, step_id),
    FOREIGN KEY (tenant_id, platform, uid) REFERENCES participants ON DELETE CASCADE
);
CREATE INDEX survey_prompts_free ON survey_prompts (tenant_id, platform, uid, opened_at)
    WHERE status = 'open' AND free;

-- survey.edit: следующий шаг не шлётся новым сообщением, а заменяет сообщение вопроса
CREATE TABLE survey_edits (
    tenant_id   bigint NOT NULL,
    platform    text NOT NULL,
    uid         bigint NOT NULL,
    funnel_id   text NOT NULL,
    step_id     uuid NOT NULL,              -- какой шаг встанет на место сообщения
    message_id  bigint NOT NULL,
    created_at  timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, platform, uid, funnel_id, step_id),
    FOREIGN KEY (tenant_id, platform, uid) REFERENCES participants ON DELETE CASCADE
);

-- закреп с отсчётом до эфира: у человека один (как <slug>_air_pins в bot-engine)
CREATE TABLE air_pins (
    tenant_id   bigint NOT NULL,
    platform    text NOT NULL,
    uid         bigint NOT NULL,
    funnel_id   text NOT NULL,           -- из какой воронки (эфир человека – её позиция)
    message_id  bigint NOT NULL,
    state       text NOT NULL DEFAULT '',    -- что сейчас нарисовано: wait:0:d3 / soon:0 / live:0 / done
    body        text NOT NULL DEFAULT '',    -- текст шага под шапкой (закреп из обычного шага)
    kind        text NOT NULL DEFAULT 'text' CHECK (kind IN ('text', 'caption')),
    next_at     timestamptz,             -- когда перерисовать (смена состояния / отсчёта)
    pin_at      timestamptz,             -- отложенное закрепление (pin_delay_sec)
    tries       int NOT NULL DEFAULT 0,
    created_at  timestamptz NOT NULL,
    updated_at  timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, platform, uid),
    FOREIGN KEY (tenant_id, platform, uid) REFERENCES participants ON DELETE CASCADE
);
CREATE INDEX air_pins_due ON air_pins (next_at) WHERE next_at IS NOT NULL;
CREATE INDEX air_pins_pin_due ON air_pins (pin_at) WHERE pin_at IS NOT NULL;
