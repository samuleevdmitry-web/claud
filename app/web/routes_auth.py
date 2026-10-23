"""Страница входа и выход."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.services.users import SESSION_DAYS, authenticate, make_session
from app.web.auth import COOKIE, current_user
from app.web.templating import templates

router = APIRouter()

# Защита от подбора пароля: не больше MAX_FAILURES неудачных входов с одного адреса за окно.
MAX_FAILURES = 10
FAILURE_WINDOW = 15 * 60
_failures: dict[str, deque[float]] = defaultdict(deque)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


def _blocked(ip: str) -> bool:
    attempts = _failures[ip]
    while attempts and attempts[0] < time.monotonic() - FAILURE_WINDOW:
        attempts.popleft()
    return len(attempts) >= MAX_FAILURES


def _safe_next(target: str) -> str:
    return target if target.startswith("/") and not target.startswith("//") else "/"


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/"):
    if not get_settings().app_password or current_user(request):
        return RedirectResponse(_safe_next(next), status_code=303)
    return templates.TemplateResponse(request, "login.html", {"next": _safe_next(next), "error": None})


@router.post("/login", response_class=HTMLResponse)
async def login(
    request: Request,
    login: str = Form(""),
    password: str = Form(""),
    next: str = Form("/"),
    db: Session = Depends(get_db),
):
    ip = _client_ip(request)
    if _blocked(ip):
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "next": _safe_next(next),
                "error": "Слишком много неудачных попыток. Подождите 15 минут.",
                "login": login,
            },
            status_code=429,
        )
    user = authenticate(db, login, password)
    if user is None:
        _failures[ip].append(time.monotonic())
        await asyncio.sleep(1)  # замедляем подбор пароля
        return templates.TemplateResponse(
            request,
            "login.html",
            {"next": _safe_next(next), "error": "Неверный логин или пароль", "login": login},
            status_code=401,
        )
    _failures.pop(ip, None)
    response = RedirectResponse(_safe_next(next), status_code=303)
    response.set_cookie(
        COOKIE,
        make_session(db, user),
        max_age=SESSION_DAYS * 86400,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    return response


@router.post("/logout")
def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(COOKIE)
    return response
