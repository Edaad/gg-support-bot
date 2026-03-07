import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

_SECRET = None
_ALGORITHM = "HS256"
_EXPIRY_HOURS = 24

security = HTTPBearer()


def _get_secret() -> str:
    global _SECRET
    if _SECRET is None:
        _SECRET = os.getenv("DASHBOARD_PASSWORD", "changeme")
    return _SECRET


def create_token() -> str:
    payload = {
        "sub": "admin",
        "exp": datetime.now(timezone.utc) + timedelta(hours=_EXPIRY_HOURS),
    }
    return jwt.encode(payload, _get_secret(), algorithm=_ALGORITHM)


def verify_password(password: str) -> bool:
    return password == _get_secret()


def get_current_admin(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> str:
    token = credentials.credentials
    try:
        payload = jwt.decode(token, _get_secret(), algorithms=[_ALGORITHM])
        return payload.get("sub", "admin")
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
