"""Страница «Проверка»: ошибки автоматики по оценкам пользователя и правки файла ключей."""

from __future__ import annotations

import hashlib

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.classifier import RELEVANCE_LABELS
from app.db import get_db
from app.keywords.editor import PRODUCT_TYPES, STRENGTHS, EditError, apply_edits
from app.services.feedback import KIND_LABELS, build_report, edit_from_form, simulate
from app.services.keywords import (
    KeywordFileRejected,
    activate_keyword_file,
    get_active_keyword_set,
    parse_keyword_file,
)
from app.services.tenders import VERDICT_LABELS
from app.web.auth import require_auth
from app.web.routes_keywords import UPLOAD_DIR
from app.web.templating import templates

router = APIRouter(prefix="/feedback", dependencies=[Depends(require_auth)])


def _context(db: Session) -> dict:
    active = get_active_keyword_set(db)
    version, ks = active if active else (None, None)
    return {
        "version": version,
        "report": build_report(db, ks) if ks else None,
        "relevance_labels": RELEVANCE_LABELS,
        "verdict_labels": VERDICT_LABELS,
        "kind_labels": KIND_LABELS,
        "strengths": STRENGTHS,
        "product_types": PRODUCT_TYPES,
    }


@router.get("", response_class=HTMLResponse)
def feedback_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "feedback.html", _context(db))


@router.post("/preview", response_class=HTMLResponse)
async def feedback_preview(request: Request, db: Session = Depends(get_db)):
    """Правка файла ключей → новая версия (не применена) и её влияние на сохранённые тендеры."""
    form = {k: str(v) for k, v in (await request.form()).items()}
    active = get_active_keyword_set(db)
    if not active:
        return RedirectResponse("/keywords", status_code=303)
    version, ks_old = active
    edit = edit_from_form(form)
    error = None
    try:
        content = apply_edits(version.content, [edit])
    except EditError as exc:
        error = str(exc)
    if error is None:
        ks_new = parse_keyword_file(content)
        new_errors = [i for i in ks_new.errors if i.message not in {e.message for e in ks_old.errors}]
        if not ks_new.is_usable or new_errors:
            error = "после правки файл разбирается с ошибками: " + "; ".join(str(i) for i in new_errors[:3])
    if error:
        ctx = _context(db) | {"edit_error": f"Правка не выполнена: {error}", "edit": edit}
        return templates.TemplateResponse(request, "feedback.html", ctx, status_code=400)
    token = hashlib.sha256(content).hexdigest()
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    (UPLOAD_DIR / f"{token}.xlsx").write_bytes(content)
    return templates.TemplateResponse(
        request,
        "feedback_preview.html",
        {
            "edit": edit,
            "token": token,
            "impact": simulate(db, ks_new),
            "relevance_labels": RELEVANCE_LABELS,
            "verdict_labels": VERDICT_LABELS,
            "back": form.get("back") or "/feedback",
        },
    )


@router.post("/apply")
def feedback_apply(
    request: Request,
    token: str = Form(...),
    description: str = Form(""),
    back: str = Form("/feedback"),
    db: Session = Depends(get_db),
    user: str = Depends(require_auth),
):
    if len(token) != 64 or not all(c in "0123456789abcdef" for c in token):
        return RedirectResponse("/feedback", status_code=303)
    path = UPLOAD_DIR / f"{token}.xlsx"
    active = get_active_keyword_set(db)
    if not path.exists() or not active:
        return RedirectResponse("/feedback", status_code=303)
    who = f"{user}: {description}" if user else description
    try:
        activate_keyword_file(db, path.read_bytes(), active[0].filename, uploaded_by=who[:100])
    except KeywordFileRejected:
        return RedirectResponse("/feedback", status_code=303)
    path.unlink(missing_ok=True)
    target = back if back.startswith("/") and not back.startswith("//") else "/feedback"
    sep = "&" if "?" in target else "?"
    return RedirectResponse(f"{target}{sep}applied=1", status_code=303)
