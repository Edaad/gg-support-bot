import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

ROLE_ADMIN = "admin"
ROLE_ACCOUNT_MANAGER = "account_manager"

_SECRET = None
_ALGORITHM = "HS256"
_EXPIRY_HOURS = 24

security = HTTPBearer()


def _get_secret() -> str:
    global _SECRET
    if _SECRET is None:
        _SECRET = os.getenv("DASHBOARD_PASSWORD", "changeme")
    return _SECRET


def resolve_role(password: str) -> Optional[str]:
    """Map a login password to a dashboard role, or None if invalid."""
    admin_pw = os.getenv("DASHBOARD_PASSWORD", "changeme")
    if password == admin_pw:
        return ROLE_ADMIN
    am_pw = os.getenv("DASHBOARD_AM_PASSWORD")
    if am_pw and password == am_pw:
        return ROLE_ACCOUNT_MANAGER
    return None


def create_token(role: str = ROLE_ADMIN) -> str:
    payload = {
        "sub": role,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=_EXPIRY_HOURS),
    }
    return jwt.encode(payload, _get_secret(), algorithm=_ALGORITHM)


def verify_password(password: str) -> bool:
    return resolve_role(password) is not None


def get_current_admin(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> str:
    token = credentials.credentials
    try:
        payload = jwt.decode(token, _get_secret(), algorithms=[_ALGORITHM])
        return payload.get("role") or payload.get("sub") or ROLE_ADMIN
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")


def require_admin(role: str = Depends(get_current_admin)) -> str:
    """Reject non-admin dashboard roles (e.g. account_manager)."""
    if role != ROLE_ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin only")
    return role
