-- MAX в движке: общий (зонтичный) бот на несколько запусков.
--
-- У бота в `bots` по-прежнему один «хозяин» (tenant_id) – для TG это и есть
-- его запуск. MAX-бот бывает общим: какие запуски он обслуживает и по какой
-- метке входа их различать – в bot_tenants. Человек MAX – отдельный участник
-- (participants/positions/jobs уже ключуются платформой), с TG он не сливается.

ALTER TABLE bots ADD COLUMN IF NOT EXISTS platform_bot_id bigint;   -- id бота в MAX (из /me)
ALTER TABLE bots ADD COLUMN IF NOT EXISTS poll_marker bigint;       -- long-poll MAX: с какого места читать

CREATE TABLE IF NOT EXISTS bot_tenants (
    bot_id      bigint NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
    tenant_id   bigint NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    -- метка входа: payload /start, начинающийся с неё, ведёт в этот запуск,
    -- метка снимается, остаток разбирается как обычно (reg_…/ref_…)
    prefix      text NOT NULL,
    -- запуск для /start без метки (или с незнакомой меткой)
    is_default  boolean NOT NULL DEFAULT false,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (bot_id, tenant_id),
    UNIQUE (bot_id, prefix)
);
CREATE UNIQUE INDEX IF NOT EXISTS bot_tenants_one_default ON bot_tenants (bot_id) WHERE is_default;

-- id сообщений MAX – строки («mid.…»), в bigint[] они не ложатся
ALTER TABLE sent_messages ADD COLUMN IF NOT EXISTS ext_message_ids text[];
