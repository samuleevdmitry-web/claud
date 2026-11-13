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


def email_hash(value) -> str | None:
    """Отпечаток почты. В ячейке бывает несколько адресов через запятую — берётся первый."""
    first = str(value or "").replace(";", ",").split(",")[0]
    return fingerprint("email", norm_email(first))


def email_hashes(value) -> list[str]:
    """Отпечатки всех адресов ячейки (через запятую или точку с запятой)."""
    out = []
    for part in str(value or "").replace(";", ",").split(","):
        h = fingerprint("email", norm_email(part))
        if h and h not in out:
            out.append(h)
    return out


def phone_hash(value) -> str | None:
    return fingerprint("phone", norm_phone(value))
