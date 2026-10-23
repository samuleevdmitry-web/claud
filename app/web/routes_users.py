"""Пользователи: менеджеры с доступом только к тендерам и уведомлениям (страница администратора)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import User
from app.services.users import UserError, create_manager, generate_password, set_password
from app.web.auth import require_admin
from app.web.templating import templates

router = APIRouter(prefix="/users", dependencies=[Depends(require_admin)])


def _page(request: Request, db: Session, message: str = "", error: str = "", status_code: int = 200):
    s = get_settings()
    return templates.TemplateResponse(
        request,
        "users.html",
        {
            "users": db.scalars(select(User).order_by(User.login)).all(),
            "message": message,
            "error": error,
            "suggested_password": generate_password(),
            "smtp_ready": bool(s.smtp_host),
            "telegram_managers": bool(s.telegram_bot_token and s.telegram_managers_chat_id),
            "base_url": s.app_base_url,
        },
        status_code=status_code,
    )


def _user(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "Пользователь не найден")
    return user


@router.get("", response_class=HTMLResponse)
def users_page(request: Request, db: Session = Depends(get_db)):
    return _page(request, db, message=request.query_params.get("message", ""))


@router.post("", response_class=HTMLResponse)
def add_user(
    request: Request,
    login: str = Form(...),
    password: str = Form(...),
    name: str = Form(""),
    email: str = Form(""),
    db: Session = Depends(get_db),
):
    try:
        user = create_manager(db, login, password, name, email)
    except UserError as exc:
        return _page(request, db, error=f"Пользователь не добавлен: {exc}", status_code=400)
    return _page(
        request,
        db,
        message=f"Менеджер «{user.login}» добавлен. Пароль: {password} — передайте его менеджеру.",
    )


@router.post("/{user_id}/password", response_class=HTMLResponse)
def reset_password(request: Request, user_id: int, password: str = Form(...), db: Session = Depends(get_db)):
    user = _user(db, user_id)
    try:
        set_password(user, password)
    except UserError as exc:
        return _page(request, db, error=str(exc), status_code=400)
    return _page(request, db, message=f"Новый пароль «{user.login}»: {password}. Старый вход у него закрыт.")


@router.post("/{user_id}/update")
def update_user(
    user_id: int,
    name: str = Form(""),
    email: str = Form(""),
    notify_email: bool = Form(False),
    is_active: bool = Form(False),
    db: Session = Depends(get_db),
):
    user = _user(db, user_id)
    user.name, user.email = name.strip(), email.strip()
    user.notify_email, user.is_active = notify_email, is_active
    return RedirectResponse("/users?message=Сохранено", status_code=303)


@router.post("/{user_id}/delete")
def delete_user(user_id: int, db: Session = Depends(get_db)):
    db.delete(_user(db, user_id))
    return RedirectResponse("/users?message=Пользователь удалён", status_code=303)
