"""Пользователи и вход: администратор из .env (APP_USERNAME/APP_PASSWORD) и менеджеры в БД.

Пароли менеджеров хранятся как PBKDF2-SHA256 с солью. Сессия — подписанная HMAC cookie;
ключ подписи — SECRET_KEY из .env или случайный, сохранённый в data/secret_key.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from functools import lru_cache

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import PROJECT_ROOT, get_settings
from app.models import User, utcnow

ADMIN, MANAGER = "admin", "manager"
ROLE_LABELS = {ADMIN: "администратор", MANAGER: "менеджер"}
SESSION_DAYS = 30
_ITERATIONS = 200_000


@dataclass(frozen=True)
class CurrentUser:
    login: str
    role: str
    name: str = ""
    user_id: int | None = None

    @property
    def is_admin(self) -> bool:
        return self.role == ADMIN

    @property
    def display(self) -> str:
        return self.name or self.login


# --------------------------------------------------------------------------- пароли


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return f"pbkdf2_sha256${_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt, digest = stored.split("$")
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    check = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations))
    return hmac.compare_digest(check.hex(), digest)


class UserError(ValueError):
    pass


def validate_password(password: str) -> None:
    if len(password) < 8:
        raise UserError("пароль — не короче 8 символов")


def create_manager(session: Session, login: str, password: str, name: str = "", email: str = "") -> User:
    login = login.strip()
    if not login or not all(c.isalnum() or c in "._-@" for c in login):
        raise UserError("логин — латиница, цифры и . _ - @")
    if login.lower() == get_settings().app_username.lower():
        raise UserError("этот логин занят администратором")
    if session.scalar(select(User).where(func.lower(User.login) == login.lower())):
        raise UserError(f"пользователь «{login}» уже есть")
    validate_password(password)
    user = User(
        login=login,
        name=name.strip(),
        email=email.strip(),
        password_hash=hash_password(password),
        role=MANAGER,
        is_active=True,
        notify_email=True,
        created_at=utcnow(),
        notifications_seen_at=utcnow(),  # старые уведомления новому менеджеру не показываем как непрочитанные
    )
    session.add(user)
    session.flush()
    return user


def set_password(user: User, password: str) -> None:
    validate_password(password)
    user.password_hash = hash_password(password)  # старые сессии пользователя перестают действовать


def generate_password() -> str:
    return secrets.token_urlsafe(9)


def authenticate(session: Session, login: str, password: str) -> CurrentUser | None:
    s = get_settings()
    if s.app_password and hmac.compare_digest(login.encode(), s.app_username.encode()):
        if hmac.compare_digest(password.encode(), s.app_password.encode()):
            return CurrentUser(login=s.app_username, role=ADMIN, name="Администратор")
        return None
    user = session.scalar(select(User).where(func.lower(User.login) == login.strip().lower()))
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        return None
    user.last_login_at = utcnow()
    return CurrentUser(login=user.login, role=user.role, name=user.name, user_id=user.id)


def _load(session: Session, login: str) -> tuple[CurrentUser, str] | None:
    """Пользователь и отпечаток его пароля: при смене пароля старые сессии перестают действовать."""
    s = get_settings()
    if s.app_password and login == s.app_username:
        stamp = hashlib.sha256(s.app_password.encode()).hexdigest()[:16]
        return CurrentUser(login=s.app_username, role=ADMIN, name="Администратор"), stamp
    user = session.scalar(select(User).where(User.login == login))
    if user is None or not user.is_active:
        return None
    stamp = hashlib.sha256(user.password_hash.encode()).hexdigest()[:16]
    return CurrentUser(login=user.login, role=user.role, name=user.name, user_id=user.id), stamp


# --------------------------------------------------------------------------- сессия


@lru_cache
def _secret() -> bytes:
    configured = get_settings().secret_key
    if configured:
        return configured.encode()
    path = PROJECT_ROOT / "data" / "secret_key"
    if path.exists():
        return path.read_bytes().strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_hex(32).encode()
    path.write_bytes(key)
    return key


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make_session(session: Session, user: CurrentUser) -> str:
    loaded = _load(session, user.login)
    stamp = loaded[1] if loaded else ""
    data = {"u": user.login, "v": stamp, "exp": int(time.time()) + SESSION_DAYS * 86400}
    payload = _b64(json.dumps(data).encode())
    sig = _b64(hmac.new(_secret(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def user_from_session(session: Session, token: str | None) -> CurrentUser | None:
    """Пользователь из cookie: подпись верна, срок не истёк, пароль с момента входа не менялся."""
    if not token or "." not in token:
        return None
    payload, sig = token.rsplit(".", 1)
    expected = _b64(hmac.new(_secret(), payload.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        data = json.loads(_unb64(payload))
    except ValueError:
        return None
    if int(data.get("exp", 0)) < time.time():
        return None
    loaded = _load(session, str(data.get("u", "")))
    if loaded is None or not hmac.compare_digest(loaded[1], str(data.get("v", ""))):
        return None
    return loaded[0]
