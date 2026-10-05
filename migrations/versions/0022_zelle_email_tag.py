"""Fill Zelle email tags the previous revision left empty.

0021 read the optional "Email" word instead of the address, so only phone
rows were stored. This copies Zelle: name@domain.com into tag.

Revision ID: 0022_zelle_email_tag
Revises: 0021_dest_tag
Create Date: 2026-10-04
"""

from migrations import helpers as h

revision = "0022_zelle_email_tag"
down_revision = "0021_dest_tag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    h.execute_data(
        """
        UPDATE club_payment_tier_variants AS v
        SET tag = lower((regexp_match(
          coalesce(v.response_text, '') || E'\\n' || coalesce(v.response_caption, ''),
          'Zelle: *([^ \\n@]+@[^ \\n]+)',
          'i'
        ))[1])
        FROM club_payment_methods AS m
        WHERE m.id = v.method_id
          AND lower(m.slug) = 'zelle'
          AND v.tag IS NULL
          AND (regexp_match(
            coalesce(v.response_text, '') || E'\\n' || coalesce(v.response_caption, ''),
            'Zelle: *([^ \\n@]+@[^ \\n]+)',
            'i'
          ))[1] IS NOT NULL
        """
    )


def downgrade() -> None:
    raise NotImplementedError
