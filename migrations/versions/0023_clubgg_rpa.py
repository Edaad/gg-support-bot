"""Trace every ClubGG RPA call and lock stale /add and /cash commands.

One row per ClubGG request id. A staff /add or /cash also inserts a command
lock so a late worker cannot deposit or claim.

Revision ID: 0023_clubgg_rpa
Revises: 0022_zelle_email_tag
Create Date: 2026-10-05
"""

from migrations import helpers as h

revision = "0023_clubgg_rpa"
down_revision = "0022_zelle_email_tag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    h.create_table(
        "clubgg_rpa_events",
        "id SERIAL PRIMARY KEY,\n"
        "    request_id VARCHAR(128) NOT NULL,\n"
        "    operation VARCHAR(16) NOT NULL,\n"
        "    source VARCHAR(32) NOT NULL,\n"
        "    status VARCHAR(16) NOT NULL,\n"
        "    command_lock BOOLEAN NOT NULL DEFAULT FALSE,\n"
        "    telegram_chat_id BIGINT,\n"
        "    message_id BIGINT,\n"
        "    club_id INTEGER REFERENCES clubs(id) ON DELETE SET NULL,\n"
        "    group_title VARCHAR(255),\n"
        "    amount NUMERIC(14, 2),\n"
        "    bonus NUMERIC(14, 2),\n"
        "    message_sent_at TIMESTAMP WITH TIME ZONE,\n"
        "    started_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),\n"
        "    path VARCHAR(32),\n"
        "    rpa_status VARCHAR(32),\n"
        "    detail TEXT,\n"
        "    CONSTRAINT uq_clubgg_rpa_request_id UNIQUE (request_id)",
    )
    h.create_index(
        "ix_clubgg_rpa_started_at",
        "clubgg_rpa_events",
        "(started_at)",
    )
    h.create_index(
        "ix_clubgg_rpa_status",
        "clubgg_rpa_events",
        "(status)",
    )
    h.create_index(
        "uq_clubgg_rpa_command",
        "clubgg_rpa_events",
        "(telegram_chat_id, message_id) WHERE command_lock",
        unique=True,
    )


def downgrade() -> None:
    raise NotImplementedError
