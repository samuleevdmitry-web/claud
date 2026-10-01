from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import get_settings

_security = HTTPBasic(auto_error=False, realm="Velmorium tenders")


def require_auth(credentials: HTTPBasicCredentials | None = Depends(_security)) -> str:
    """HTTP Basic с одним паролем из .env. Пустой APP_PASSWORD отключает проверку (разработка)."""
    settings = get_settings()
    if not settings.app_password:
        return "dev"
    ok = credentials is not None and (
        secrets.compare_digest(credentials.username.encode(), settings.app_username.encode())
        & secrets.compare_digest(credentials.password.encode(), settings.app_password.encode())
    )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Нужен вход",
            headers={"WWW-Authenticate": 'Basic realm="Velmorium tenders", charset="UTF-8"'},
        )
    return credentials.username
