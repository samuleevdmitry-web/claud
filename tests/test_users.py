"""Пользователи, вход и права менеджера."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.services.users import (
    UserError,
    authenticate,
    create_manager,
    hash_password,
    make_session,
    set_password,
    user_from_session,
    verify_password,
)
from app.web.auth import manager_allowed
from tests.test_web import AUTH, _seed, client  # noqa: F401 — фикстура приложения


@pytest.fixture
def session(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "secret")
    from app.config import get_settings

    get_settings.cache_clear()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = sessionmaker(engine, expire_on_commit=False)()
    yield s
    s.close()
    get_settings.cache_clear()


def test_password_hash():
    stored = hash_password("correct horse")
    assert stored.startswith("pbkdf2_sha256$") and "correct" not in stored
    assert verify_password("correct horse", stored) and not verify_password("wrong", stored)
    assert hash_password("x" * 8) != hash_password("x" * 8)  # соль


def test_create_manager_validation(session):
    create_manager(session, "ivanov", "password1", "Иванов")
    with pytest.raises(UserError, match="уже есть"):
        create_manager(session, "IVANOV", "password1")
    with pytest.raises(UserError, match="8 символов"):
        create_manager(session, "petrov", "short")
    with pytest.raises(UserError, match="администратором"):
        create_manager(session, "velmorium", "password1")
    with pytest.raises(UserError, match="латиница"):
        create_manager(session, "иван ов", "password1")


def test_authenticate_and_session(session):
    user = create_manager(session, "ivanov", "password1", "Иванов")
    me = authenticate(session, "Ivanov", "password1")
    assert me is not None and me.role == "manager" and not me.is_admin and user.last_login_at
    assert authenticate(session, "ivanov", "wrong") is None
    admin = authenticate(session, "velmorium", "secret")
    assert admin is not None and admin.is_admin

    token = make_session(session, me)
    assert user_from_session(session, token).login == "ivanov"
    assert user_from_session(session, token[:-2] + "xx") is None  # подпись
    set_password(user, "newpassword")
    assert user_from_session(session, token) is None  # смена пароля закрывает старые входы
    user.is_active = False
    assert authenticate(session, "ivanov", "newpassword") is None


def test_manager_allowlist():
    assert manager_allowed("GET", "/") and manager_allowed("GET", "/tenders/5")
    assert manager_allowed("POST", "/tenders/5/verdict") and manager_allowed("GET", "/notifications")
    for method, path in [
        ("GET", "/sources"),
        ("GET", "/keywords"),
        ("GET", "/feedback"),
        ("GET", "/import"),
        ("GET", "/users"),
        ("POST", "/tenders/5/delete"),
        ("POST", "/runs/start"),
        ("POST", "/api/import"),
        ("GET", "/keywords/download"),
    ]:
        assert not manager_allowed(method, path), path


# --------------------------------------------------------------------------- через веб


def _login(client, login, password):  # noqa: F811
    return client.post(
        "/login", data={"login": login, "password": password, "next": "/"}, follow_redirects=False
    )


def test_login_form_and_logout(client):  # noqa: F811
    r = client.get("/", headers={"Accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
    assert "Вход" in client.get("/login").text
    r = _login(client, "velmorium", "wrong")
    assert r.status_code == 401 and "Неверный логин или пароль" in r.text
    r = _login(client, "velmorium", "secret")
    assert r.status_code == 303 and "velmorium_session" in r.cookies
    page = client.get("/")
    assert page.status_code == 200 and "Пользователи" in page.text and "Выйти" in page.text
    client.post("/logout")
    assert client.get("/", follow_redirects=False).status_code == 401


def test_manager_sees_only_tenders_and_notifications(client):  # noqa: F811
    t1, _ = _seed(client)
    r = client.post(
        "/users",
        data={"login": "ivanov", "password": "password1", "name": "Иванов", "email": "i@x.ru"},
        auth=AUTH,
    )
    assert "Менеджер «ivanov» добавлен" in r.text
    client.cookies.clear()
    assert _login(client, "ivanov", "password1").status_code == 303

    page = client.get("/?tab=all")
    assert page.status_code == 200 and "Иванов" in page.text
    for hidden in (
        'href="/sources"',
        'href="/keywords"',
        'href="/import"',
        'href="/users"',
        'href="/feedback"',
    ):
        assert hidden not in page.text, hidden
    assert "tab=trash" not in page.text and 'value="delete"' not in page.text
    assert "✓ Да" in page.text and "Экспорт в Excel" in page.text
    assert "Площадка" not in page.text.split("<main>")[0]  # нет красной полосы про площадки

    r = client.post(f"/tenders/{t1}/verdict", data={"verdict": "no"}, headers={"HX-Request": "true"})
    assert 'class="no on"' in r.text and "поправить ключи" not in r.text
    card = client.get(f"/tenders/{t1}").text
    assert 'id="fix"' not in card and "Удалить" not in card

    # закрытые разделы: страница — на список тендеров, действие — 403
    r = client.get("/keywords", headers={"Accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert client.get("/sources").status_code == 403
    assert client.post(f"/tenders/{t1}/delete").status_code == 403
    assert (
        client.post("/tenders/bulk?tab=all", data={"action": "delete", "scope": "filter"}).status_code == 403
    )
    assert client.post("/runs/start").status_code == 403
    assert (
        client.get("/?tab=trash").status_code == 200
    )  # корзина не показывается — открывается обычная вкладка
    assert client.post("/tenders/bulk?tab=all", data={"action": "seen", "ids": [str(t1)]}).status_code == 200


def test_manager_notifications_only_new_tenders(client):  # noqa: F811
    from app.db import get_session_factory
    from app.models import Notification

    client.post("/users", data={"login": "ivanov", "password": "password1"}, auth=AUTH)
    with get_session_factory()() as s:
        s.add(Notification(kind="source_failure", level="error", title="Площадка X не проверена"))
        s.add(
            Notification(
                kind="new_tenders", level="info", title="Новые тендеры: 2 релевантных, 1 на проверку"
            )
        )
        s.commit()
    client.cookies.clear()
    _login(client, "ivanov", "password1")
    page = client.get("/?tab=all").text
    assert '<span class="badge">1</span>' in page  # одно непрочитанное
    r = client.get("/notifications")
    assert "Новые тендеры: 2 релевантных" in r.text and "Площадка X" not in r.text
    assert "notification level-info unread" in r.text
    assert '<span class="badge">1</span>' not in client.get("/?tab=all").text  # просмотрено


def test_admin_manages_users(client):  # noqa: F811
    r = client.post("/users", data={"login": "petrov", "password": "password1"}, auth=AUTH)
    uid = r.text.split('action="/users/')[1].split("/")[0]
    r = client.post(f"/users/{uid}/password", data={"password": "short"}, auth=AUTH)
    assert r.status_code == 400
    r = client.post(f"/users/{uid}/password", data={"password": "newpassword"}, auth=AUTH)
    assert "Новый пароль «petrov»" in r.text
    client.post(f"/users/{uid}/update", data={"name": "Петров", "email": "p@x.ru"}, auth=AUTH)  # доступ снят
    client.cookies.clear()
    assert _login(client, "petrov", "newpassword").status_code == 401
    client.post(f"/users/{uid}/delete", auth=AUTH)
    assert "Менеджеров пока нет" in client.get("/users", auth=AUTH).text


def test_login_rate_limit_and_no_api_docs(client, monkeypatch):  # noqa: F811
    import app.web.routes_auth as auth_routes

    async def no_sleep(_):
        return None

    monkeypatch.setattr(auth_routes.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(auth_routes, "_failures", auth_routes.defaultdict(auth_routes.deque))
    for _ in range(auth_routes.MAX_FAILURES):
        assert _login(client, "velmorium", "wrong").status_code == 401
    r = _login(client, "velmorium", "secret")  # даже верный пароль — после 10 ошибок ждём
    assert r.status_code == 429 and "Подождите 15 минут" in r.text
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code in (401, 404)
    assert client.get("/docs", auth=AUTH).status_code == 404
