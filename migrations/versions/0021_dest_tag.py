"""Shared destination tag, link, and response mode on deposit variants.

Copies Venmo and Cash App values into the shared columns and fills Zelle tags
from response text when a single recipient is present. The rail-specific
columns stay in place for the previous release.

Revision ID: 0021_dest_tag
Revises: 0020_variant_pause
Create Date: 2026-10-04
"""

from migrations import helpers as h

revision = "0021_dest_tag"
down_revision = "0020_variant_pause"
branch_labels = None
depends_on = None


def upgrade() -> None:
    h.add_column("club_payment_tier_variants", "tag", "VARCHAR(200)")
    h.add_column("club_payment_tier_variants", "link", "VARCHAR(128)")
    h.add_column("club_payment_tier_variants", "response_mode", "VARCHAR(16)")
    h.execute_data(
        """
        UPDATE club_payment_tier_variants AS v
        SET
          tag = CASE
            WHEN lower(m.slug) = 'cashapp' THEN NULLIF(v.cashapp_tag, '')
            WHEN lower(m.slug) = 'venmo' THEN NULLIF(v.venmo_tag, '')
            ELSE NULLIF(COALESCE(NULLIF(v.venmo_tag, ''), NULLIF(v.cashapp_tag, '')), '')
          END,
          link = CASE
            WHEN lower(m.slug) = 'cashapp' THEN NULLIF(v.cashapp_link, '')
            WHEN lower(m.slug) = 'venmo' THEN NULLIF(v.venmo_link, '')
            ELSE NULLIF(COALESCE(NULLIF(v.venmo_link, ''), NULLIF(v.cashapp_link, '')), '')
          END,
          response_mode = CASE
            WHEN lower(m.slug) = 'cashapp' THEN NULLIF(v.cashapp_response_mode, '')
            WHEN lower(m.slug) = 'venmo' THEN NULLIF(v.venmo_response_mode, '')
            ELSE NULLIF(
              COALESCE(
                NULLIF(v.venmo_response_mode, ''),
                NULLIF(v.cashapp_response_mode, '')
              ),
              ''
            )
          END
        FROM club_payment_methods AS m
        WHERE m.id = v.method_id
          AND v.tag IS NULL
          AND (
            v.venmo_tag IS NOT NULL
            OR v.cashapp_tag IS NOT NULL
            OR v.venmo_link IS NOT NULL
            OR v.cashapp_link IS NOT NULL
            OR v.venmo_response_mode IS NOT NULL
            OR v.cashapp_response_mode IS NOT NULL
          )
        """
    )
    h.execute_data(
        """
        UPDATE club_payment_tier_variants AS v
        SET tag = lower((regexp_match(
          coalesce(v.response_text, '') || E'\\n' || coalesce(v.response_caption, ''),
          'Zelle( Email)?:[[:space:]]*([^[:space:]@]+@[^[:space:]]+)',
          'i'
        ))[1])
        FROM club_payment_methods AS m
        WHERE m.id = v.method_id
          AND lower(m.slug) = 'zelle'
          AND v.tag IS NULL
          AND (regexp_match(
            coalesce(v.response_text, '') || E'\\n' || coalesce(v.response_caption, ''),
            'Zelle( Email)?:[[:space:]]*([^[:space:]@]+@[^[:space:]]+)',
            'i'
          ))[1] IS NOT NULL
        """
    )
    h.execute_data(
        """
        UPDATE club_payment_tier_variants AS v
        SET tag = regexp_replace(
          (regexp_match(
            coalesce(v.response_text, '') || E'\\n' || coalesce(v.response_caption, ''),
            'Zelle:[[:space:]]*([0-9+(). -]*[0-9][0-9+(). -]*)',
            'i'
          ))[1],
          '[^0-9]',
          '',
          'g'
        )
        FROM club_payment_methods AS m
        WHERE m.id = v.method_id
          AND lower(m.slug) = 'zelle'
          AND v.tag IS NULL
          AND length(regexp_replace(
            coalesce(
              (regexp_match(
                coalesce(v.response_text, '') || E'\\n' || coalesce(v.response_caption, ''),
                'Zelle:[[:space:]]*([0-9+(). -]*[0-9][0-9+(). -]*)',
                'i'
              ))[1],
              ''
            ),
            '[^0-9]',
            '',
            'g'
          )) BETWEEN 10 AND 15
        """
    )


def downgrade() -> None:
    raise NotImplementedError
