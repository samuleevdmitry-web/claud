"""Отпечатки почты и телефона вместо самих адресов.

Чтобы свести базу рассылок с лидами и заказами, нужно сравнивать адреса. Хранить их незачем: при загрузке
адрес приводится к единому виду и превращается в SHA-256 с «солью» (DATA_SALT из .env). Одинаковые адреса дают
одинаковый отпечаток, а восстановить адрес по отпечатку нельзя.
"""

from __future__ import annotations

import hashlib
import os
import re

DEFAULT_SALT = "oldos-analytics"


def _salt() -> str:
    return os.environ.get("DATA_SALT", "").strip() or DEFAULT_SALT


def norm_email(value) -> str | None:
    s = str(value or "").strip().lower()
    return s if re.fullmatch(r"[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+", s) else None


def norm_phone(value) -> str | None:
    """Последние 10 цифр: +7 (917) 470-64-44, 89174706444 и 9174706444 — один номер."""
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[-10:] if len(digits) >= 10 else None


def fingerprint(kind: str, value: str | None) -> str | None:
    if not value:
        return None
    return hashlib.sha256(f"{_salt()}:{kind}:{value}".encode()).hexdigest()


# Адрес внутри любого текста ячейки: «E-mail=ivan@x.ru», «a@b.ru; c@d.ru», «Почта: a@b.ru (бухгалтерия)».
_EMAIL = re.compile(r"[\w.%+\-]+@[\w\-]+(?:\.[\w\-]+)*\.[^\W\d_]{2,}")
# Номер внутри текста: цифры с пробелами, скобками и дефисами; запятая, «;», буквы (доб., моб.) его заканчивают.
_PHONE_RUN = re.compile(r"\+?\d[\d\s\-()]{5,}\d")


def emails(value) -> list[str]:
    """Все адреса из текста ячейки, в едином виде, без повторов."""
    out = []
    for m in _EMAIL.findall(str(value or "")):
        e = norm_email(m)
        if e and e not in out:
            out.append(e)
    return out


def email_hash(value) -> str | None:
    """Отпечаток почты. В ячейке бывает несколько адресов — берётся первый."""
    found = emails(value)
    return fingerprint("email", found[0]) if found else None


def email_hashes(value) -> list[str]:
    """Отпечатки всех адресов ячейки."""
    return [fingerprint("email", e) for e in emails(value)]


def phones(value) -> list[str]:
    """Все номера ячейки в едином виде (10 цифр): «+7 (917) 470-64-44, 8 999 000-11-22», «т.=(3452) 12-34-56 доб. 12»,
    два номера через пробел. Городские номера без кода (меньше 10 цифр) не берутся — их не сравнить."""
    out = []
    for run in _PHONE_RUN.findall(str(value or "")):
        digits = re.sub(r"\D", "", run)
        if len(digits) <= 12:
            candidates = [digits]
        else:  # несколько номеров подряд без запятой
            candidates = [d for d in (re.sub(r"\D", "", t) for t in run.split()) if len(d) >= 10]
            if not candidates and len(digits) % 2 == 0:
                candidates = [digits[:len(digits) // 2], digits[len(digits) // 2:]]
        for d in candidates:
            n = norm_phone(d)
            if n and n not in out:
                out.append(n)
    return out


def phone_hashes(value) -> list[str]:
    """Отпечатки всех номеров ячейки (см. phones)."""
    return [fingerprint("phone", n) for n in phones(value)]


def phone_hash(value) -> str | None:
    return fingerprint("phone", norm_phone(value))
