"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""

from migrations import helpers as h

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    # Use migrations.helpers (guarded, re-runnable) instead of raw op.* DDL.
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    raise NotImplementedError("Forward-only: add a new revision instead.")
