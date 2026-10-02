"""TLS для российских госресурсов.

Сайты вроде zakupki.gov.ru подписаны «Russian Trusted Root CA» Минцифры, которого нет в
стандартном наборе сертификатов. Сертификат скачивается с официального сайта gu-st.ru при
первом запуске и добавляется только в SSL-контекст запросов приложения к площадкам — системные
настройки компьютера не меняются. Можно положить файл вручную: data/certs/russian_trusted_root_ca.pem.
"""

from __future__ import annotations

import logging
import ssl
from functools import lru_cache
from pathlib import Path

import certifi
import httpx

from app.config import PROJECT_ROOT

log = logging.getLogger(__name__)

CERT_DIR = PROJECT_ROOT / "data" / "certs"
RUSSIAN_CA = CERT_DIR / "russian_trusted_root_ca.pem"
RUSSIAN_CA_URLS = (
    "https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt",
    "https://gu-st.ru/content/Other/doc/russian_trusted_root_ca.cer",
)


def _looks_like_pem(data: bytes) -> bool:
    return b"-----BEGIN CERTIFICATE-----" in data


def ensure_russian_ca(timeout: float = 20) -> Path | None:
    """Скачивает корневой сертификат Минцифры, если его ещё нет. Ошибки не роняют приложение."""
    if RUSSIAN_CA.exists():
        return RUSSIAN_CA
    for url in RUSSIAN_CA_URLS:
        try:
            r = httpx.get(url, timeout=timeout, follow_redirects=True)
            if r.status_code != 200:
                continue
            pem = r.text if _looks_like_pem(r.content) else ssl.DER_cert_to_PEM_cert(r.content)
            ssl.create_default_context().load_verify_locations(cadata=pem)  # проверка, что это сертификат
            CERT_DIR.mkdir(parents=True, exist_ok=True)
            RUSSIAN_CA.write_text(pem, encoding="ascii")
            log.info("Сохранён корневой сертификат Минцифры: %s", RUSSIAN_CA)
            ssl_context.cache_clear()
            return RUSSIAN_CA
        except (httpx.HTTPError, ssl.SSLError, OSError, ValueError) as exc:
            log.info("Не удалось скачать сертификат Минцифры с %s: %s", url, exc)
    log.warning("Нет сертификата Минцифры: zakupki.gov.ru и другие госсайты будут недоступны")
    return None


@lru_cache
def ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context(cafile=certifi.where())
    for extra in sorted(CERT_DIR.glob("*.pem")) if CERT_DIR.exists() else []:
        try:
            ctx.load_verify_locations(cafile=str(extra))
        except ssl.SSLError as exc:
            log.warning("Сертификат %s не загружен: %s", extra, exc)
    return ctx
