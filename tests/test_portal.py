"""Сайт менеджеров (portal/, PHP) и обмен с ним — на настоящем PHP-сервере с SQLite."""

import os
import re
import shutil
import socket
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.db import Base
from app.models import Tender, User
from app.services import portal
from app.services.ingest import ingest
from app.services.keywords import activate_keyword_file, get_active_keyword_set
from app.services.tenders import delete_tenders, set_verdict
from app.services.users import create_manager, set_password
from app.sources.base import Position, TenderDetails
from tests.test_web import AUTH, client  # noqa: F401 — фикстура приложения

PORTAL_DIR = Path(__file__).resolve().parent.parent / "portal"
TOKEN = "test-sync-token-0123456789"
pytestmark = pytest.mark.skipif(shutil.which("php") is None, reason="нужен PHP")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


TABLES = (
    "portal_tenders portal_managers portal_manager_state portal_views portal_changes portal_notifications"
)
TABLES += " portal_login_failures portal_meta"


@pytest.fixture
def site(tmp_path):
    # По умолчанию — SQLite; PORTAL_TEST_MYSQL="dsn|пользователь|пароль" — проверка на MySQL/MariaDB.
    mysql = os.environ.get("PORTAL_TEST_MYSQL")
    if mysql:
        dsn, user, password = mysql.split("|")
        drop = "foreach (explode(' ', $argv[4]) as $t) { $p->exec('DROP TABLE IF EXISTS ' . $t); }"
        subprocess.run(
            ["php", "-r", f"$p = new PDO($argv[1], $argv[2], $argv[3]); {drop}", dsn, user, password, TABLES],
            check=True,
        )
        db = f"'db_dsn' => '{dsn}', 'db_user' => '{user}', 'db_password' => '{password}'"
    else:
        db = f"'db_dsn' => 'sqlite:{tmp_path / 'portal.db'}', 'db_user' => null, 'db_password' => null"
    config = tmp_path / "config.php"
    config.write_text(
        f"<?php return [{db}, 'sync_token' => '{TOKEN}', 'timezone' => 'Europe/Moscow'];",
        encoding="utf-8",
    )
    port = _free_port()
    proc = subprocess.Popen(
        ["php", "-S", f"127.0.0.1:{port}", "-t", str(PORTAL_DIR)],
        env={"PORTAL_CONFIG": str(config), "PATH": "/usr/bin:/bin"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            httpx.get(url + "/login.php", timeout=1)
            break
        except httpx.HTTPError:
            time.sleep(0.1)
    yield url
    proc.terminate()
    proc.wait(5)


@pytest.fixture
def factory(keywords_file):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    f = sessionmaker(engine, expire_on_commit=False)
    with f() as s:
        activate_keyword_file(s, keywords_file.read_bytes(), keywords_file.name)
        create_manager(s, "ivanov", "password1", "Иван Иванов", "ivanov@example.com")
        version, ks = get_active_keyword_set(s)
        rows = [
            ("1", "Поставка постельного белья", "ООО «Гостиница Москва»"),
            ("2", "Поставка полотенец махровых", "ООО «Альфа»"),
            ("3", "Поставка подушек", "ГБУЗ «Городская больница»"),  # отклонён — на сайт не попадает
        ]
        for ext, title, customer in rows:
            d = TenderDetails(
                "onlinecontract",
                ext,
                f"https://onlinecontract.ru/tenders/{ext}",
                title,
                customer_name=customer,
                application_deadline=datetime.now(UTC) + timedelta(days=5),
                positions=[Position("Полотенце махровое 50х90", 10, "шт")],
            )
            ingest(s, d, ks, version.id)
        s.commit()
    return f


def client_for(url: str) -> portal.PortalClient:
    return portal.PortalClient(url, TOKEN)


def manager_session(url: str, login="ivanov", password="password1") -> httpx.Client:
    c = httpx.Client(base_url=url, follow_redirects=True, timeout=10)
    csrf = re.search(r'name="csrf" value="([0-9a-f]+)"', c.get("/login.php").text).group(1)
    r = c.post("/login.php", data={"login": login, "password": password, "csrf": csrf, "next": "index.php"})
    assert r.status_code == 200 and "Выйти" in r.text, r.text[:300]
    return c


def csrf_of(html: str) -> str:
    return re.search(r'name="csrf" value="([0-9a-f]+)"', html).group(1)


def test_sync_roundtrip_with_manager_marks(site, factory):
    pc = client_for(site)
    res = portal.sync(factory, client=pc)
    assert not res.errors and res.pushed == 2 and res.managers == 1

    m = manager_session(site)
    page = m.get("/index.php?tab=all").text
    assert "Поставка постельного белья" in page and "Поставка полотенец махровых" in page
    assert "Поставка подушек" not in page  # отклонённые автоматикой не выкладываются
    assert 'pill pill-new">новый' in page
    with factory() as s:
        hotel = s.query(Tender).filter_by(title="Поставка постельного белья").one()
        towels = s.query(Tender).filter_by(title="Поставка полотенец махровых").one()

    card = m.get(f"/tender.php?id={towels.id}").text
    assert "Полотенце махровое 50х90" in card and "onlinecontract.ru/tenders/2" in card
    token = csrf_of(card)
    base = {"csrf": token, "tender_id": towels.id, "back": f"tender.php?id={towels.id}"}
    m.post("/action.php", data=base | {"field": "verdict", "value": "yes"})
    m.post("/action.php", data=base | {"field": "user_status", "value": "в работе"})
    r = m.post("/action.php", data=base | {"field": "notes", "value": "Позвонить заказчику"})
    assert 'class="yes on"' in r.text and "Позвонить заказчику" in r.text
    assert (
        m.post("/action.php", data=base | {"csrf": "bad", "field": "verdict", "value": "no"}).status_code
        == 400
    )

    res = portal.sync(factory, client=pc)
    assert res.pulled == 3 and res.applied == 3
    with factory() as s:
        t = s.get(Tender, towels.id)
        assert (t.verdict, t.user_status, t.notes) == ("yes", "в работе", "Позвонить заказчику")
        assert t.change_log[-1]["source"] == "сайт: ivanov"
        # на компьютере правят позже — на сайт уходит новое значение
        t.user_status, t.user_status_at = "подаём заявку", datetime.now(UTC) + timedelta(seconds=5)
        s.commit()
    portal.sync(factory, client=pc)
    assert "подаём заявку" in m.get(f"/tender.php?id={towels.id}").text

    # удалённый на компьютере тендер пропадает с сайта
    with factory() as s:
        delete_tenders(s, [hotel.id])
        s.commit()
    portal.sync(factory, client=pc)
    assert "Поставка постельного белья" not in m.get("/index.php?tab=all").text
    assert "Тендер не найден" in m.get(f"/tender.php?id={hotel.id}").text


def test_older_site_mark_does_not_override_newer_local(site, factory):
    pc = client_for(site)
    portal.sync(factory, client=pc)
    m = manager_session(site)
    with factory() as s:
        towels = s.query(Tender).filter_by(title="Поставка полотенец махровых").one()
    token = csrf_of(m.get(f"/tender.php?id={towels.id}").text)
    m.post(
        "/action.php",
        data={"csrf": token, "tender_id": towels.id, "field": "verdict", "value": "no", "back": "index.php"},
    )
    with factory() as s:  # администратор оценил позже, ещё до обмена
        set_verdict(s, towels.id, "yes")
        s.get(Tender, towels.id).verdict_at = datetime.now(UTC) + timedelta(minutes=1)
        s.commit()
    res = portal.sync(factory, client=pc)
    assert res.pulled == 1 and res.applied == 0
    with factory() as s:
        assert s.get(Tender, towels.id).verdict == "yes"
    assert 'class="yes on"' in m.get(f"/tender.php?id={towels.id}").text


def test_manager_access_follows_local_accounts(site, factory):
    pc = client_for(site)
    portal.sync(factory, client=pc)
    m = manager_session(site)
    with factory() as s:
        set_password(s.query(User).filter_by(login="ivanov").one(), "newpassword")
        s.commit()
    portal.sync(factory, client=pc)
    assert "Вход" in m.get("/index.php").text  # старая сессия закрыта после смены пароля
    manager_session(site, password="newpassword")
    with factory() as s:
        s.query(User).filter_by(login="ivanov").one().is_active = False
        s.commit()
    portal.sync(factory, client=pc)
    c = httpx.Client(base_url=site, follow_redirects=True)
    csrf = csrf_of(c.get("/login.php").text)
    r = c.post("/login.php", data={"login": "ivanov", "password": "newpassword", "csrf": csrf})
    assert r.status_code == 401 and "Неверный логин или пароль" in r.text


def test_site_notifications_and_security(site, factory):
    from app.services.notify import new_tenders_notification

    with factory() as s:
        new_tenders_notification(s, datetime.now(UTC) - timedelta(days=1))
        s.commit()
    pc = client_for(site)
    portal.sync(factory, client=pc)
    m = manager_session(site)
    assert '<span class="badge">1</span>' in m.get("/index.php").text
    r = m.get("/notifications.php")
    assert "Новые тендеры: 1 релевантных, 1 на проверку" in r.text and "unread" in r.text
    assert '<span class="badge">1</span>' not in m.get("/index.php").text

    # ключ обмена
    r = httpx.post(site + "/api.php", json={"token": "wrong", "action": "ping"})
    assert r.status_code == 403
    assert portal.ping(pc)["ok"] is True
    # без входа — на страницу входа
    assert "Вход" in httpx.get(site + "/index.php", follow_redirects=True).text
    # ограничение подбора пароля
    c = httpx.Client(base_url=site, follow_redirects=True)
    for _ in range(10):
        c.post(
            "/login.php", data={"login": "ivanov", "password": "x", "csrf": csrf_of(c.get("/login.php").text)}
        )
    r = c.post(
        "/login.php",
        data={"login": "ivanov", "password": "password1", "csrf": csrf_of(c.get("/login.php").text)},
    )
    assert r.status_code == 429


def test_manager_changes_reach_site_at_once(site, client, monkeypatch):  # noqa: F811
    monkeypatch.setenv("PORTAL_URL", site)
    monkeypatch.setenv("PORTAL_TOKEN", TOKEN)
    get_settings.cache_clear()
    r = client.post("/users", data={"login": "petrov", "password": "password1"}, auth=AUTH)
    assert "Сайт менеджеров обновлён" in r.text
    manager_session(site, "petrov", "password1")  # без «Обменяться сейчас»

    uid = re.search(r'action="/users/(\d+)/password"', r.text).group(1)
    assert 'placeholder="пусто — придумаем"' in r.text
    r = client.post(f"/users/{uid}/password", data={"password": ""}, auth=AUTH)
    new = re.search(r"Новый пароль «petrov»: ([\w-]+)\.", r.text).group(1)
    assert "Сайт менеджеров обновлён" in r.text
    manager_session(site, "petrov", new)

    r = client.post(f"/users/{uid}/update", data={"name": "Петров"}, auth=AUTH)  # доступ снят
    assert "Сохранено. Сайт менеджеров обновлён." in r.text
    c = httpx.Client(base_url=site, follow_redirects=True)
    r = c.post(
        "/login.php", data={"login": "petrov", "password": new, "csrf": csrf_of(c.get("/login.php").text)}
    )
    assert r.status_code == 401


def test_sync_error_is_recorded(factory):
    pc = portal.PortalClient("http://127.0.0.1:9", TOKEN, timeout=1)
    res = portal.sync(factory, client=pc)
    assert res.errors and "недоступен" in res.errors[0]
    with factory() as s:
        assert "недоступен" in portal.status(s)["error"]


def test_bad_database_settings_give_clear_message(tmp_path):
    config = tmp_path / "config.php"
    config.write_text(
        "<?php return ['db_dsn' => 'mysql:host=127.0.0.1;port=1;dbname=x', 'db_user' => 'Dm', "
        f"'db_password' => 'secret', 'sync_token' => '{TOKEN}'];",
        encoding="utf-8",
    )
    port = _free_port()
    proc = subprocess.Popen(
        [
            "php",
            "-d",
            "display_errors=1",
            "-d",
            "error_reporting=E_ALL",
            "-S",
            f"127.0.0.1:{port}",
            "-t",
            str(PORTAL_DIR),
        ],
        env={"PORTAL_CONFIG": str(config), "PATH": "/usr/bin:/bin"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        url = f"http://127.0.0.1:{port}"
        for _ in range(50):
            try:
                r = httpx.get(url + "/login.php", timeout=2)
                break
            except httpx.HTTPError:
                time.sleep(0.1)
        assert r.status_code == 500 and r.text.startswith("Нет подключения к базе")
        assert "Warning" not in r.text and "secret" not in r.text
        r = httpx.post(url + "/api.php", json={"token": TOKEN, "action": "ping"})
        assert r.status_code == 500 and "Нет подключения к базе" in r.json()["error"]
    finally:
        proc.terminate()
        proc.wait(5)
