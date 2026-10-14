"""Queue for the flood-safe contact restore worker job.

Revision ID: 0030_contact_restore
Revises: 0029_paypal_echeck
Create Date: 2026-10-14
"""

from migrations import helpers as h

revision = "0030_contact_restore"
down_revision = "0029_paypal_echeck"
branch_labels = None
depends_on = None


def upgrade() -> None:
    h.create_table(
        "contact_restore_rows",
        """
        id SERIAL PRIMARY KEY,
        club_key VARCHAR(64) NOT NULL,
        contact_user_id BIGINT NOT NULL,
        username VARCHAR(64) NULL,
        first_name VARCHAR(64) NOT NULL DEFAULT '',
        last_name VARCHAR(64) NOT NULL DEFAULT '',
        access_hash BIGINT NULL,
        status VARCHAR(16) NOT NULL DEFAULT 'pending',
        resolve_via VARCHAR(16) NULL,
        username_miss BOOLEAN NOT NULL DEFAULT false,
        last_error TEXT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT contact_restore_rows_club_key_contact_user_id_key
            UNIQUE (club_key, contact_user_id)
        """,
    )
    h.create_index(
        "ix_contact_restore_rows_club_status",
        "contact_restore_rows",
        "USING btree (club_key, status)",
    )
    h.create_table(
        "contact_restore_groups",
        """
        id SERIAL PRIMARY KEY,
        club_key VARCHAR(64) NOT NULL,
        chat_id BIGINT NOT NULL,
        access_hash BIGINT NOT NULL DEFAULT 0,
        peer_type VARCHAR(16) NOT NULL,
        scanned_at TIMESTAMPTZ NULL,
        CONSTRAINT uq_contact_restore_groups_club_chat UNIQUE (club_key, chat_id)
        """,
    )
    h.create_index(
        "ix_contact_restore_groups_club_scanned",
        "contact_restore_groups",
        "USING btree (club_key, scanned_at)",
    )
    h.create_table(
        "contact_restore_control",
        """
        id INTEGER PRIMARY KEY,
        last_tick_at TIMESTAMPTZ NULL,
        flood_resume_at JSONB NULL,
        dialogs_filled_at JSONB NULL,
        auto_disabled_at TIMESTAMPTZ NULL,
        auto_disabled_reason TEXT NULL
        """,
    )


def downgrade() -> None:
    raise NotImplementedError
