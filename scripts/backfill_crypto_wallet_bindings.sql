-- Idempotent: create crypto_wallet_bindings and backfill from bound crypto_payments.
-- Run: heroku pg:psql -a gg-support-bot-2025 -f scripts/backfill_crypto_wallet_bindings.sql

CREATE TABLE IF NOT EXISTS crypto_wallet_bindings (
    id SERIAL PRIMARY KEY,
    from_address_normalized VARCHAR(255) NOT NULL,
    alert_scope VARCHAR(32) NOT NULL,
    telegram_chat_id BIGINT NOT NULL,
    club_id INTEGER REFERENCES clubs(id) ON DELETE SET NULL,
    bound_group_title_at_bind VARCHAR(255),
    last_bound_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_bound_by_telegram_user_id BIGINT,
    CONSTRAINT uq_crypto_wallet_bindings_address_scope
        UNIQUE (from_address_normalized, alert_scope)
);

CREATE INDEX IF NOT EXISTS ix_crypto_wallet_bindings_telegram_chat_id
    ON crypto_wallet_bindings (telegram_chat_id);

INSERT INTO crypto_wallet_bindings (
    from_address_normalized,
    alert_scope,
    telegram_chat_id,
    club_id,
    bound_group_title_at_bind,
    last_bound_at,
    last_bound_by_telegram_user_id
)
SELECT DISTINCT ON (lower(trim(from_address)), alert_scope)
    lower(trim(from_address)),
    alert_scope,
    telegram_chat_id,
    club_id,
    bound_group_title_at_bind,
    COALESCE(bound_at, created_at),
    bound_by_telegram_user_id
FROM crypto_payments
WHERE telegram_chat_id IS NOT NULL
  AND trim(from_address) <> ''
ORDER BY lower(trim(from_address)), alert_scope, bound_at DESC NULLS LAST, id DESC
ON CONFLICT (from_address_normalized, alert_scope) DO NOTHING;

SELECT
    (SELECT COUNT(*) FROM crypto_wallet_bindings) AS bindings_total,
    (SELECT COUNT(*) FROM (
        SELECT DISTINCT lower(trim(from_address)), alert_scope
        FROM crypto_payments
        WHERE telegram_chat_id IS NOT NULL AND trim(from_address) <> ''
    ) w) AS source_wallets;
