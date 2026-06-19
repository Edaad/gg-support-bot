"""Priority tier/rank helpers for migrated_group_recovery (no Telethon deps)."""


def classify_priority_tier(
    *,
    deposit_cents: int,
    active_in_past_30_days: bool,
) -> int:
    """Return priority tier: 1=deposits, 2=active, 3=rest."""
    if int(deposit_cents) > 0:
        return 1
    if active_in_past_30_days:
        return 2
    return 3


def compute_priority_rank(
    *,
    priority_tier: int,
    deposit_cents: int,
    last_activity_epoch: int,
    telegram_chat_id: int,
    sequence: int,
) -> int:
    """Lower rank = higher priority within global ordering (tier ASC, rank ASC)."""
    tier_base = int(priority_tier) * 10_000_000_000
    if priority_tier == 1:
        deposit_key = min(int(deposit_cents), 9_999_999_999)
        return tier_base + (9_999_999_999 - deposit_key) * 10 + int(sequence)
    if priority_tier == 2:
        activity_key = min(max(int(last_activity_epoch), 0), 9_999_999_999)
        return tier_base + (9_999_999_999 - activity_key) * 10 + int(sequence)
    return tier_base + abs(int(telegram_chat_id)) % 9_999_999_999 + int(sequence)
