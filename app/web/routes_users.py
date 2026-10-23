"""Пользователи: менеджеры с доступом только к тендерам и уведомлениям (страница администратора)."""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import User
from app.services import portal
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
            "portal": portal.status(db),
        },
        status_code=status_code,
    )


def _to_portal(db: Session) -> str:
    """Сразу отправить изменения менеджеров на сайт, чтобы не ждать обмена по расписанию."""
    from app.db import get_session_factory

    if not portal.configured():
        return ""
    db.commit()
    result = portal.sync(get_session_factory())
    db.expire_all()
    if result.errors:
        errors = "; ".join(result.errors)
        return f" На сайт менеджеров пока не отправлено ({errors}) — уйдёт при ближайшем обмене."
    return " Сайт менеджеров обновлён."


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
        message=f"Менеджер «{user.login}» добавлен. Пароль: {password} — передайте его менеджеру, "
        "потом посмотреть его будет нельзя." + _to_portal(db),
    )


@router.post("/portal-sync", response_class=HTMLResponse)
async def portal_sync_now(request: Request, db: Session = Depends(get_db)):
    """Обмен с сайтом менеджеров сейчас (отметки менеджеров → сюда, тендеры и менеджеры → туда)."""
    import asyncio

    from app.db import get_session_factory

    if not portal.configured():
        return _page(
            request,
            db,
            error="Сайт менеджеров не настроен: задайте PORTAL_URL и PORTAL_TOKEN в .env",
            status_code=400,
        )
    result = await asyncio.to_thread(portal.sync, get_session_factory())
    db.expire_all()
    if result.errors:
        return _page(request, db, error="Обмен не удался: " + "; ".join(result.errors), status_code=502)
    return _page(
        request,
        db,
        message=f"Обмен выполнен: на сайте тендеров {result.pushed}, менеджеров {result.managers}; "
        f"отметок с сайта {result.pulled}, применено {result.applied}.",
    )


@router.post("/portal-ping", response_class=HTMLResponse)
async def portal_ping(request: Request, db: Session = Depends(get_db)):
    import asyncio

    if not portal.configured():
        return _page(
            request,
            db,
            error="Сайт менеджеров не настроен: задайте PORTAL_URL и PORTAL_TOKEN в .env",
            status_code=400,
        )
    try:
        info = await asyncio.to_thread(portal.ping)
    except portal.PortalError as exc:
        return _page(request, db, error=f"Связи с сайтом нет: {exc}", status_code=502)
    return _page(
        request, db, message=f"Связь с сайтом есть (PHP {info.get('php')}, база {info.get('driver')})."
    )


@router.post("/{user_id}/password", response_class=HTMLResponse)
def reset_password(request: Request, user_id: int, password: str = Form(""), db: Session = Depends(get_db)):
    user = _user(db, user_id)
    password = password.strip() or generate_password()
    try:
        set_password(user, password)
    except UserError as exc:
        return _page(request, db, error=str(exc), status_code=400)
    return _page(
        request,
        db,
        message=f"Новый пароль «{user.login}»: {password}. Старый вход у него закрыт." + _to_portal(db),
    )


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
    return RedirectResponse(
        f"/users?{urlencode({'message': 'Сохранено.' + _to_portal(db)})}", status_code=303
    )


@router.post("/{user_id}/delete")
def delete_user(user_id: int, db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if user is None:  # второе нажатие, пока первое ещё выполнялось
        return RedirectResponse(
            f"/users?{urlencode({'message': 'Пользователь уже удалён.'})}", status_code=303
        )
    db.delete(user)
    message = "Пользователь удалён." + _to_portal(db)
    return RedirectResponse(f"/users?{urlencode({'message': message})}", status_code=303)
