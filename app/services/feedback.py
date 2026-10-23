"""Разбор оценок «подходит / не подходит»: где ошиблась автоматика, какие ключи, маркеры и
минус-слова к этому привели, что поправить в файле и как правка изменит сохранённые тендеры."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.classifier import Relevance, classify
from app.keywords.editor import (
    ADD_MARKER,
    ADD_MINUS,
    MINUS_STRENGTH,
    PRODUCT_TYPE,
    REMOVE,
    Edit,
    suggest_mask,
)
from app.keywords.loader import KeyType, KeywordSet, Strength
from app.keywords.text import tokenize
from app.models import Tender
from app.services.keywords import tender_to_text
from app.services.tenders import VERDICT_NO, VERDICT_YES

KIND_LABELS = {"product": "товарный ключ", "marker": "маркер заказчика", "minus": "минус-слово"}


def is_error(verdict: str | None, relevance: str | None) -> bool:
    """Ошибка автоматики по оценке пользователя. «На проверку» для неподходящего — не ошибка."""
    if verdict == VERDICT_YES:
        return relevance != Relevance.RELEVANT
    if verdict == VERDICT_NO:
        return relevance == Relevance.RELEVANT
    return False


@dataclass
class EntryStat:
    """Запись файла (ключ, маркер, минус-слово) и как она сработала на оценённых тендерах."""

    kind: str
    label: str
    mask: str
    detail: str = ""  # тип ключа или сила минус-слова
    yes: int = 0
    no: int = 0
    wrong: list[int] = field(default_factory=list)  # тендеры, где запись привела к ошибке
    fixes: list[tuple[Edit, str]] = field(default_factory=list)


@dataclass
class Candidate:
    word: str
    mask: str
    count: int


@dataclass
class Report:
    rated: int = 0
    yes: int = 0
    no: int = 0
    false_positive: list[Tender] = field(default_factory=list)  # «не подходит», а автоматика — релевантный
    missed: list[Tender] = field(default_factory=list)  # «подходит», а автоматика — нет
    decided_review: int = 0  # «на проверку» с оценкой — подсказки для уточнения
    suspicious: list[EntryStat] = field(default_factory=list)
    minus_candidates: list[Candidate] = field(default_factory=list)
    marker_candidates: list[Candidate] = field(default_factory=list)


def _active(matches: list[dict], kind: str) -> list[dict]:
    return [m for m in matches or [] if m.get("kind") == kind and not m.get("suppressed")]


def _entry_masks(ks: KeywordSet) -> dict[str, dict[str, object]]:
    return {
        "product": {p.mask.source: p for p in ks.products},
        "marker": {m.mask.source: m for m in ks.markers},
        "minus": {n.mask.source: n for n in ks.minus_words},
    }


_STOP = set(
    "общество ограниченной ответственностью акционерное публичное закрытое открытое ооо ао пао зао оао "
    "ип индивидуальный предприниматель для нужд поставка закупка поставку товара товаров продукции "
    "города город области область края край республики республика района район имени им россии "
    "российской федерации рф года году номер".split()
)


def _candidate_words(
    tenders: list[Tender], other: list[Tender], fields: tuple[str, ...], min_count: int = 1
) -> list[Candidate]:
    """Слова, частые в одной группе тендеров и отсутствующие в другой; не внутри уже найденных ключей."""

    def words(t: Tender) -> set[str]:
        out: set[str] = set()
        for fld in fields:
            text = t.customer_name if fld == "customer" else t.title
            spans = [(m["start"], m["end"]) for m in t.matches or [] if m.get("field") == fld]
            for tok in tokenize(text or ""):
                if len(tok.text) < 3 or tok.text.isdigit() or tok.text in _STOP:
                    continue
                if any(s <= tok.start and tok.end <= e for s, e in spans):
                    continue
                out.add(tok.text)
        return out

    counts = Counter(w for t in tenders for w in words(t))
    seen_other = {w for t in other for w in words(t)}
    return [
        Candidate(w, suggest_mask(w), n)
        for w, n in counts.most_common(40)
        if w not in seen_other and n >= min_count
    ][:12]


def build_report(session: Session, ks: KeywordSet) -> Report:
    tenders = session.scalars(
        select(Tender)
        .where(Tender.deleted_at.is_(None), Tender.verdict.is_not(None))
        .order_by(Tender.id.desc())
    ).all()
    report = Report(rated=len(tenders))
    entries = _entry_masks(ks)
    stats: dict[tuple[str, str], EntryStat] = {}

    def stat(kind: str, m: dict) -> EntryStat | None:
        entry = entries[kind].get(m.get("mask"))
        if entry is None:  # записи уже нет в действующем файле
            return None
        key = (kind, m["mask"])
        if key not in stats:
            detail = ""
            if kind == "product":
                detail = "самодостаточный" if entry.type == KeyType.SELF else "нужен маркер"
            elif kind == "minus":
                detail = "сильное" if entry.strength == Strength.STRONG else "среднее"
            stats[key] = EntryStat(kind, m.get("label", ""), m["mask"], detail)
        return stats[key]

    for t in tenders:
        yes = t.verdict == VERDICT_YES
        report.yes += yes
        report.no += not yes
        if t.relevance == Relevance.REVIEW:
            report.decided_review += 1
        error = is_error(t.verdict, t.relevance)
        if error:
            (report.missed if yes else report.false_positive).append(t)
        for kind in ("product", "marker", "minus"):
            for m in {m["mask"]: m for m in _active(t.matches, kind)}.values():
                s = stat(kind, m)
                if s is None:
                    continue
                s.yes += yes
                s.no += not yes
                # Запись «виновата»: маркер/самодостаточный ключ — в лишнем релевантном,
                # минус-слово — в подходящем, но не релевантном тендере.
                culprit = (kind == "minus" and yes and t.relevance != Relevance.RELEVANT) or (
                    kind in ("marker", "product")
                    and not yes
                    and t.relevance == Relevance.RELEVANT
                    and (kind == "marker" or s.detail == "самодостаточный")
                )
                if culprit:
                    s.wrong.append(t.id)

    for s in stats.values():
        if not s.wrong:
            continue
        if s.kind == "marker":
            s.fixes.append((Edit(REMOVE, "marker", s.mask, s.label), "удалить маркер"))
        elif s.kind == "product":
            s.fixes.append(
                (Edit(PRODUCT_TYPE, "product", s.mask, s.label, "Нужен маркер"), "сделать «Нужен маркер»")
            )
        elif s.kind == "minus":
            if s.detail == "сильное":
                s.fixes.append((Edit(MINUS_STRENGTH, "minus", s.mask, s.label, "Средний"), "сделать средним"))
            s.fixes.append((Edit(REMOVE, "minus", s.mask, s.label), "удалить минус-слово"))
        report.suspicious.append(s)
    report.suspicious.sort(key=lambda s: (-len(s.wrong), s.kind, s.label))

    yes_t = [t for t in tenders if t.verdict == VERDICT_YES]
    no_t = [t for t in tenders if t.verdict == VERDICT_NO]
    report.minus_candidates = _candidate_words(
        [t for t in no_t if t.relevance != Relevance.REJECTED], yes_t, ("customer", "title"), min_count=2
    )
    report.marker_candidates = _candidate_words(
        [t for t in yes_t if not _active(t.matches, "marker")], no_t, ("customer",), min_count=2
    )
    return report


def tender_fixes(tender: Tender, ks: KeywordSet) -> list[tuple[Edit, str]]:
    """Готовые правки для одного тендера, где оценка расходится с автоматикой."""
    if not is_error(tender.verdict, tender.relevance):
        return []
    entries = _entry_masks(ks)
    fixes: list[tuple[Edit, str]] = []
    if tender.verdict == VERDICT_NO:
        for m in {m["mask"]: m for m in _active(tender.matches, "marker")}.values():
            if m["mask"] in entries["marker"]:
                fixes.append(
                    (Edit(REMOVE, "marker", m["mask"], m["label"]), f"удалить маркер «{m['label']}»")
                )
        for m in {m["mask"]: m for m in _active(tender.matches, "product")}.values():
            p = entries["product"].get(m["mask"])
            if p is not None and p.type == KeyType.SELF:
                fixes.append(
                    (
                        Edit(PRODUCT_TYPE, "product", m["mask"], m["label"], "Нужен маркер"),
                        f"ключ «{m['label']}» — сделать «Нужен маркер»",
                    )
                )
    else:
        for m in {m["mask"]: m for m in _active(tender.matches, "minus")}.values():
            n = entries["minus"].get(m["mask"])
            if n is None:
                continue
            if n.strength == Strength.STRONG:
                fixes.append(
                    (
                        Edit(MINUS_STRENGTH, "minus", m["mask"], m["label"], "Средний"),
                        f"минус-слово «{m['label']}» — сделать средним",
                    )
                )
            fixes.append(
                (Edit(REMOVE, "minus", m["mask"], m["label"]), f"удалить минус-слово «{m['label']}»")
            )
    return fixes


def word_suggestions(tender: Tender) -> list[Candidate]:
    """Слова из заказчика и названия для новой записи (маркер или минус-слово)."""
    fields = ("customer",) if tender.verdict == VERDICT_YES else ("customer", "title")
    return _candidate_words([tender], [], fields)


# --------------------------------------------------------------------------- влияние правки


@dataclass
class Impact:
    total: int = 0
    transitions: Counter = field(default_factory=Counter)  # (было, стало) → число
    changed: list[tuple[Tender, str, str]] = field(default_factory=list)
    errors_before: int = 0
    errors_after: int = 0
    fixed: list[Tender] = field(default_factory=list)
    broken: list[Tender] = field(default_factory=list)


def simulate(session: Session, ks: KeywordSet, limit: int = 40) -> Impact:
    """Как раскладка сохранённых тендеров изменится с новым набором ключей (без записи в БД)."""
    impact = Impact()
    for t in session.scalars(select(Tender).where(Tender.deleted_at.is_(None)).order_by(Tender.id.desc())):
        new = str(classify(ks, tender_to_text(t)).relevance or Relevance.REJECTED)
        old = t.relevance or str(Relevance.REJECTED)
        impact.total += 1
        if new != old:
            impact.transitions[(old, new)] += 1
            if len(impact.changed) < limit:
                impact.changed.append((t, old, new))
        before, after = is_error(t.verdict, old), is_error(t.verdict, new)
        impact.errors_before += before
        impact.errors_after += after
        if before and not after:
            impact.fixed.append(t)
        elif after and not before:
            impact.broken.append(t)
    return impact


def edit_from_form(form: dict[str, str]) -> Edit:
    action = form.get("action", "")
    mask = (form.get("mask") or "").strip()
    phrase = (form.get("phrase") or "").strip()
    if action in (ADD_MINUS, ADD_MARKER) and not mask and phrase:
        mask = suggest_mask(phrase)
    return Edit(
        action=action,
        kind=form.get("kind", ""),
        mask=mask,
        phrase=phrase,
        value=(form.get("value") or "").strip(),
        comment=(form.get("comment") or "").strip(),
    )
