"""Вход и права доступа.

Браузер входит через форму /login и получает подписанную cookie. HTTP Basic оставлен для
скриптов и API. Администратор (логин и пароль из .env) видит всё; менеджеру открыты только
тендеры и уведомления — остальные адреса закрыты по умолчанию (список разрешённых ниже).
"""

from __future__ import annotations

import re
from urllib.parse import quote

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import get_settings
from app.db import get_session_factory
from app.services.users import ADMIN, CurrentUser, authenticate, user_from_session

_security = HTTPBasic(auto_error=False, realm="Velmorium tenders")
COOKIE = "velmorium_session"

# Что можно менеджеру: список и карточки тендеров, оценка, статус, заметки, уведомления.
MANAGER_ROUTES = [
    ("GET", re.compile(r"^/$")),
    ("GET", re.compile(r"^/export\.xlsx$")),
    ("GET", re.compile(r"^/tenders/\d+$")),
    ("POST", re.compile(r"^/tenders/\d+/(seen|status|notes|verdict)$")),
    ("POST", re.compile(r"^/tenders/(seen-all|bulk)$")),
    ("GET", re.compile(r"^/notifications$")),
    ("POST", re.compile(r"^/notifications/read-all$")),
]


def manager_allowed(method: str, path: str) -> bool:
    return any(method == m and rx.match(path) for m, rx in MANAGER_ROUTES)


def _wants_html(request: Request) -> bool:
    return "text/html" in request.headers.get("accept", "")


def _unauthorized(request: Request) -> HTTPException:
    if request.headers.get("hx-request"):
        return HTTPException(status.HTTP_401_UNAUTHORIZED, "Нужен вход", headers={"HX-Redirect": "/login"})
    if request.method == "GET" and _wants_html(request):
        target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        return HTTPException(status.HTTP_303_SEE_OTHER, headers={"Location": f"/login?next={quote(target)}"})
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Нужен вход",
        headers={"WWW-Authenticate": 'Basic realm="Velmorium tenders", charset="UTF-8"'},
    )


def current_user(request: Request, credentials: HTTPBasicCredentials | None = None) -> CurrentUser | None:
    if not get_settings().app_password:
        return CurrentUser(login="dev", role=ADMIN, name="Разработка")
    with get_session_factory()() as session:
        user = user_from_session(session, request.cookies.get(COOKIE))
        if user is None and credentials is not None:
            user = authenticate(session, credentials.username, credentials.password)
            session.commit()
    return user


def require_auth(
    request: Request, credentials: HTTPBasicCredentials | None = Depends(_security)
) -> CurrentUser:
    user = current_user(request, credentials)
    if user is None:
        raise _unauthorized(request)
    request.state.user = user
    if not user.is_admin and not manager_allowed(request.method, request.url.path):
        if request.method == "GET" and _wants_html(request):
            raise HTTPException(status.HTTP_303_SEE_OTHER, headers={"Location": "/"})
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Раздел доступен только администратору")
    return user


def require_admin(user: CurrentUser = Depends(require_auth)) -> CurrentUser:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Действие доступно только администратору")
    return user
