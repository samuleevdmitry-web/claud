from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class UserStatus:
    NEW = "новый"
    IN_WORK = "в работе"
    APPLYING = "подаём заявку"
    APPLIED = "подали"
    NOT_INTERESTED = "не интересно"
    ALL = (NEW, IN_WORK, APPLYING, APPLIED, NOT_INTERESTED)


class KeywordSetVersion(Base):
    """Загруженная версия файла ключевых слов (таблица keyword_sets)."""

    __tablename__ = "keyword_sets"

    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(255))
    content: Mapped[bytes] = mapped_column(LargeBinary)
    sha256: Mapped[str] = mapped_column(String(64))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    uploaded_by: Mapped[str] = mapped_column(String(100), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    issues: Mapped[list] = mapped_column(JSON, default=list)


class Tender(Base):
    __tablename__ = "tenders"
    __table_args__ = (
        Index("ix_tenders_relevance_deadline", "relevance", "application_deadline"),
        Index("ix_tenders_is_new", "is_new"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    eis_number: Mapped[str | None] = mapped_column(String(64), unique=True)
    # Нормализованный ключ «заказчик + название + дата публикации» для дедупликации без номера ЕИС.
    dedup_key: Mapped[str] = mapped_column(String(64), index=True)

    title: Mapped[str] = mapped_column(Text)
    customer_name: Mapped[str] = mapped_column(Text, default="")
    customer_inn: Mapped[str | None] = mapped_column(String(12), index=True)
    region: Mapped[str | None] = mapped_column(String(255))
    delivery_place: Mapped[str] = mapped_column(Text, default="")
    law: Mapped[str] = mapped_column(String(20), default="коммерческая")  # 44-ФЗ / 223-ФЗ / коммерческая
    procedure_type: Mapped[str | None] = mapped_column(String(255))
    nmck: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    application_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str | None] = mapped_column(String(100))  # статус на площадке
    okpd2_codes: Mapped[list] = mapped_column(JSON, default=list)
    positions: Mapped[list] = mapped_column(JSON, default=list)  # [{name, qty, unit, code, price}]
    notice_text: Mapped[str] = mapped_column(Text, default="")

    relevance: Mapped[str | None] = mapped_column(String(20), index=True)  # relevant / review / rejected
    score: Mapped[float] = mapped_column(Float, default=0.0)
    matches: Mapped[list] = mapped_column(JSON, default=list)
    categories: Mapped[list] = mapped_column(JSON, default=list)
    classification_reasons: Mapped[list] = mapped_column(JSON, default=list)
    keyword_set_id: Mapped[int | None] = mapped_column(ForeignKey("keyword_sets.id", ondelete="SET NULL"))

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_new: Mapped[bool] = mapped_column(Boolean, default=True)
    is_updated: Mapped[bool] = mapped_column(Boolean, default=False)
    change_log: Mapped[list] = mapped_column(JSON, default=list)

    user_status: Mapped[str] = mapped_column(String(30), default=UserStatus.NEW)
    notes: Mapped[str] = mapped_column(Text, default="")

    sources: Mapped[list[TenderSource]] = relationship(
        back_populates="tender", cascade="all, delete-orphan", lazy="selectin"
    )


class TenderSource(Base):
    __tablename__ = "tender_sources"
    __table_args__ = (
        UniqueConstraint("source_code", "external_id", name="uq_tender_sources_source_external"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id", ondelete="CASCADE"), index=True)
    source_code: Mapped[str] = mapped_column(String(50))
    external_id: Mapped[str] = mapped_column(String(255))
    url: Mapped[str] = mapped_column(Text, default="")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    tender: Mapped[Tender] = relationship(back_populates="sources")


class SourceSetting(Base):
    """Настройки и состояние площадки: включена ли, когда был последний успешный прогон."""

    __tablename__ = "source_settings"

    code: Mapped[str] = mapped_column(String(50), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_nonzero_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str | None] = mapped_column(String(30))
    last_error: Mapped[str | None] = mapped_column(Text)
    options: Mapped[dict] = mapped_column(JSON, default=dict)


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    trigger: Mapped[str] = mapped_column(String(20), default="schedule")  # schedule / manual
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="running")  # running / success / partial / failed
    new_count: Mapped[int] = mapped_column(Integer, default=0)
    new_relevant: Mapped[int] = mapped_column(Integer, default=0)
    new_review: Mapped[int] = mapped_column(Integer, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)

    source_stats: Mapped[list[RunSourceStat]] = relationship(
        back_populates="run", cascade="all, delete-orphan", lazy="selectin"
    )


class RunSourceStat(Base):
    __tablename__ = "run_source_stats"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    source_code: Mapped[str] = mapped_column(String(50))
    # pending / running / ok / empty / failed / blocked / captcha / skipped
    status: Mapped[str] = mapped_column(String(20), default="pending")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    window_from: Mapped[date | None] = mapped_column(Date)
    window_to: Mapped[date | None] = mapped_column(Date)
    queries_total: Mapped[int] = mapped_column(Integer, default=0)
    queries_done: Mapped[int] = mapped_column(Integer, default=0)
    found: Mapped[int] = mapped_column(Integer, default=0)
    new: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[list] = mapped_column(JSON, default=list)
    duration_sec: Mapped[float | None] = mapped_column(Float)

    run: Mapped[Run] = relationship(back_populates="source_stats")


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    kind: Mapped[str] = mapped_column(String(30))  # run_summary / source_failure / keywords
    level: Mapped[str] = mapped_column(String(10), default="info")  # info / warning / error
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text, default="")
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"))


class AppSetting(Base):
    """Настройки, меняемые из UI (расписание и т.п.)."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
