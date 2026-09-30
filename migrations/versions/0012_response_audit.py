"""Response audit: response events, judge verdicts, automated staff messages.

Also adds the transcript tail column, the support-group internal flag, the
staff-handle id cache and the per-day builder run marker.

Revision ID: 0012_response_audit
Revises: 0011_alert_disable
Create Date: 2026-09-30
"""

from migrations import helpers as h

revision = "0012_response_audit"
down_revision = "0011_alert_disable"
branch_labels = None
depends_on = None


def upgrade() -> None:
    h.add_column("group_chat_daily_transcripts", "tail_messages", "JSONB")
    h.add_column(
        "support_group_chats",
        "is_internal",
        "BOOLEAN NOT NULL DEFAULT false",
    )

    h.create_table(
        "automated_staff_messages",
        """
        id BIGSERIAL PRIMARY KEY,
        chat_id BIGINT NOT NULL,
        message_id BIGINT NOT NULL,
        kind TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        """,
    )
    h.add_constraint(
        "automated_staff_messages",
        "uq_automated_staff_messages_chat_id_message_id",
        "UNIQUE (chat_id, message_id)",
    )

    h.create_table(
        "staff_handle_ids",
        """
        handle TEXT PRIMARY KEY,
        telegram_user_id BIGINT,
        error TEXT,
        resolved_at TIMESTAMPTZ NOT NULL DEFAULT now()
        """,
    )

    h.create_table(
        "response_events",
        """
        id BIGSERIAL PRIMARY KEY,
        activity_date DATE NOT NULL,
        chat_id BIGINT NOT NULL,
        club_id INTEGER NOT NULL,
        group_title TEXT,
        start_kind VARCHAR(32) NOT NULL,
        clock_start_at TIMESTAMPTZ NOT NULL,
        clock_start_msg_id BIGINT,
        escalation_event_id BIGINT,
        clock_stop_at TIMESTAMPTZ,
        clock_stop_msg_id BIGINT,
        responder_telegram_user_id BIGINT,
        response_seconds INTEGER,
        is_candidate BOOLEAN NOT NULL DEFAULT false,
        pre_labels JSONB NOT NULL DEFAULT '{}'::jsonb,
        excerpt JSONB,
        rule_version TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        """,
    )
    h.add_constraint(
        "response_events",
        "response_events_club_id_fkey",
        "FOREIGN KEY (club_id) REFERENCES clubs(id) ON DELETE CASCADE",
    )
    h.add_constraint(
        "response_events",
        "response_events_escalation_event_id_fkey",
        "FOREIGN KEY (escalation_event_id) REFERENCES escalation_events(id) "
        "ON DELETE SET NULL",
    )
    h.add_constraint(
        "response_events",
        "uq_response_events_natural_key",
        "UNIQUE (activity_date, chat_id, clock_start_msg_id, start_kind)",
    )
    h.add_constraint(
        "response_events",
        "ck_response_events_start_kind",
        "CHECK (start_kind IN ('player_question', 'bot_handoff', "
        "'slack_escalation', 'crypto_txid'))",
    )
    h.create_index(
        "ix_response_events_activity_date",
        "response_events",
        "(activity_date)",
    )
    h.create_index(
        "ix_response_events_is_candidate",
        "response_events",
        "(is_candidate)",
    )

    h.create_table(
        "response_audit_verdicts",
        """
        id BIGSERIAL PRIMARY KEY,
        response_event_id BIGINT NOT NULL,
        verdict TEXT NOT NULL,
        reason_code TEXT,
        summary TEXT,
        sling_user_id BIGINT,
        agent_name TEXT,
        judge_version TEXT,
        disputed BOOLEAN NOT NULL DEFAULT false,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        """,
    )
    h.add_constraint(
        "response_audit_verdicts",
        "response_audit_verdicts_response_event_id_fkey",
        "FOREIGN KEY (response_event_id) REFERENCES response_events(id) "
        "ON DELETE CASCADE",
    )
    h.add_constraint(
        "response_audit_verdicts",
        "uq_response_audit_verdicts_response_event_id",
        "UNIQUE (response_event_id)",
    )
    h.add_constraint(
        "response_audit_verdicts",
        "ck_response_audit_verdicts_verdict",
        "CHECK (verdict IN ('BREACH', 'EXCUSED', 'NOT_A_TRIGGER', 'OWNER_COVER', "
        "'NEEDS_REVIEW'))",
    )

    h.create_table(
        "response_audit_runs",
        """
        activity_date DATE PRIMARY KEY,
        ran_at TIMESTAMPTZ NOT NULL,
        rule_version TEXT NOT NULL,
        chats_scanned INTEGER NOT NULL DEFAULT 0,
        chats_excluded INTEGER NOT NULL DEFAULT 0,
        events INTEGER NOT NULL DEFAULT 0,
        candidates INTEGER NOT NULL DEFAULT 0
        """,
    )


def downgrade() -> None:
    raise NotImplementedError
