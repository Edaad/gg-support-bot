"""Staff authorization for GGCashier."""

from config import ADMIN_USER_IDS
from bot.services.club import get_club_allows_admin_commands, is_club_staff


def can_use_cashier(user_id: int, club_id: int) -> bool:
    if is_club_staff(user_id, club_id):
        return True
    if user_id in ADMIN_USER_IDS:
        return get_club_allows_admin_commands(club_id)
    return False


def can_access_job(user_id: int, job_initiated_by: int, club_id: int) -> bool:
    if user_id == job_initiated_by:
        return True
    if user_id in ADMIN_USER_IDS and get_club_allows_admin_commands(club_id):
        return True
    return False
