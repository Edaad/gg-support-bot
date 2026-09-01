"""Create webhook_ingest_requests table for payment + Stripe webhook audit.

Usage:
    DATABASE_URL=... python migrate_webhook_ingest_requests.py

Idempotent: safe to run multiple times (IF NOT EXISTS).
"""

from sqlalchemy import text

from db.connection import init_engine

DDL = """
CREATE TABLE IF NOT EXISTS webhook_ingest_requests (
    id SERIAL PRIMARY KEY,
    source VARCHAR(32) NOT NULL,
    endpoint_path VARCHAR(255) NOT NULL,
    http_status_code INTEGER NOT NULL,
    outcome VARCHAR(32) NOT NULL,
    duration_ms INTEGER NOT NULL,
    source_external_id VARCHAR(255),
    payment_id INTEGER,
    method_owner VARCHAR(32),
    payer_summary VARCHAR(64),
    amount_cents INTEGER,
    is_test BOOLEAN,
    stripe_event_type VARCHAR(64),
    stripe_checkout_session_id VARCHAR(255),
    error_message TEXT,
    request_body JSONB,
    response_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_wir_source_created_at ON webhook_ingest_requests (source, created_at);",
    "CREATE INDEX IF NOT EXISTS ix_wir_outcome_created_at ON webhook_ingest_requests (outcome, created_at);",
    "CREATE INDEX IF NOT EXISTS ix_wir_source_external_id ON webhook_ingest_requests (source_external_id);",
    "CREATE INDEX IF NOT EXISTS ix_wir_payment_id ON webhook_ingest_requests (payment_id);",
    "CREATE INDEX IF NOT EXISTS ix_wir_stripe_checkout_session_id ON webhook_ingest_requests (stripe_checkout_session_id);",
]

if __name__ == "__main__":
    engine = init_engine()
    with engine.connect() as conn:
        conn.execute(text(DDL))
        for stmt in INDEXES:
            conn.execute(text(stmt))
        conn.commit()
        print("webhook_ingest_requests is ready.")
