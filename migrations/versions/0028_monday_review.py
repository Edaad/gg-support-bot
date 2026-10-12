"""Monday Review: configurable weekly head tasks, notes and completions.

Revision ID: 0028_monday_review
Revises: 0027_mcp_tool_calls
Create Date: 2026-10-12
"""

from migrations import helpers as h

revision = "0028_monday_review"
down_revision = "0027_mcp_tool_calls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    h.create_table(
        "monday_review_tasks",
        """
        id SERIAL PRIMARY KEY,
        title VARCHAR(200) NOT NULL,
        description TEXT NULL,
        links_json JSONB NOT NULL DEFAULT '[]'::jsonb,
        assignees_json JSONB NOT NULL DEFAULT '[]'::jsonb,
        sort_order INTEGER NOT NULL DEFAULT 0,
        archived_at TIMESTAMPTZ NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        """,
    )
    h.create_index(
        "ix_mrt_archived_sort",
        "monday_review_tasks",
        "USING btree (archived_at, sort_order)",
    )

    h.create_table(
        "monday_review_task_notes",
        """
        id SERIAL PRIMARY KEY,
        task_id INTEGER NOT NULL
            REFERENCES monday_review_tasks(id) ON DELETE CASCADE,
        kind VARCHAR(16) NOT NULL,
        body TEXT NOT NULL,
        week_of DATE NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        """,
    )
    h.add_constraint(
        "monday_review_task_notes",
        "ck_mrtn_kind",
        "CHECK (kind IN ('persistent', 'temporary'))",
    )
    h.add_constraint(
        "monday_review_task_notes",
        "ck_mrtn_week_of_matches_kind",
        "CHECK ((kind = 'persistent' AND week_of IS NULL) "
        "OR (kind = 'temporary' AND week_of IS NOT NULL))",
    )
    h.create_index(
        "ix_mrtn_task_week",
        "monday_review_task_notes",
        "USING btree (task_id, week_of)",
    )

    h.create_table(
        "monday_review_completions",
        """
        id SERIAL PRIMARY KEY,
        task_id INTEGER NOT NULL
            REFERENCES monday_review_tasks(id) ON DELETE CASCADE,
        week_of DATE NOT NULL,
        completed_by VARCHAR(64) NULL,
        notes TEXT NULL,
        completed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        """,
    )
    h.add_constraint(
        "monday_review_completions",
        "uq_mrc_task_week",
        "UNIQUE (task_id, week_of)",
    )
    h.create_index(
        "ix_mrc_week_of",
        "monday_review_completions",
        "USING btree (week_of)",
    )


def downgrade() -> None:
    raise NotImplementedError
