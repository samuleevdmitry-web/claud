from __future__ import annotations

from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.classifier import FIELD_LABELS, RELEVANCE_LABELS, Field
from app.db import get_db
from app.models import Notification, Run, RunSourceStat, Tender, User, UserStatus, utcnow
from app.services.export import export_xlsx
from app.services.feedback import is_error, tender_fixes, word_suggestions
from app.services.jobs import active_run_id, launch_run
from app.services.keywords import get_active_keyword_set
from app.services.notify import NEW_TENDERS
from app.services.runner import get_source_setting
from app.services.scheduler import Schedule, apply_schedule, get_schedule, next_run_time, save_schedule
from app.services.tenders import (
    SORTS,
    TABS,
    VERDICT_LABELS,
    TenderFilters,
    delete_tenders,
    filter_options,
    filtered_ids,
    is_expired,
    is_urgent,
    list_tenders,
    mark_seen,
    restore_tenders,
    set_verdict,
    source_states,
    tab_counts,
)
from app.sources.registry import BY_CODE, get_source
from app.sources.util import aware
from app.web.auth import require_auth
from app.web.templating import templates

router = APIRouter(dependencies=[Depends(require_auth)])


def _filters(request: Request, default_tab: str = "relevant") -> TenderFilters:
    q = request.query_params
    try:
        page = max(1, int(q.get("page", "1")))
    except ValueError:
        page = 1
    tab = q.get("tab", default_tab)
    sort = q.get("sort", "deadline")
    return TenderFilters(
        tab=tab if tab in TABS else default_tab,
        source=q.get("source", ""),
        law=q.get("law", ""),
        region=q.get("region", ""),
        nmck_from=q.get("nmck_from", ""),
        nmck_to=q.get("nmck_to", ""),
        deadline_from=q.get("deadline_from", ""),
        deadline_to=q.get("deadline_to", ""),
        category=q.get("category", ""),
        user_status=q.get("user_status", ""),
        verdict=q.get("verdict", ""),
        q=q.get("q", "").strip(),
        sort=sort if sort in SORTS else "deadline",
        page=page,
    )


@router.get("/", response_class=HTMLResponse)
def tender_list(request: Request, db: Session = Depends(get_db)):
    counts = tab_counts(db)
    default_tab = "new" if counts["new"] else "relevant"
    f = _filters(request, default_tab)
    if f.tab == "trash" and not request.state.user.is_admin:
        f.tab = default_tab
    page = list_tenders(db, f)
    return templates.TemplateResponse(
        request,
        "tenders.html",
        {
            "f": f,
            "page": page,
            "tabs": TABS,
            "counts": counts,
            "sorts": SORTS,
            "options": filter_options(db),
            "relevance_labels": RELEVANCE_LABELS,
            "verdict_labels": VERDICT_LABELS,
            "is_mismatch": lambda t: is_error(t.verdict, t.relevance),
            "is_urgent": is_urgent,
            "is_expired": is_expired,
            "source_titles": BY_CODE,
        },
    )


@router.get("/export.xlsx")
def export(request: Request, db: Session = Depends(get_db)):
    f = _filters(request, "all")
    data = export_xlsx(db, f)
    name = f"tenders_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    return Response(
        data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.post("/tenders/seen-all")
def seen_all(request: Request, db: Session = Depends(get_db)):
    mark_seen(db, _filters(request, "new"))
    return RedirectResponse("/?" + request.url.query, status_code=303)


@router.get("/tenders/{tender_id}", response_class=HTMLResponse)
def tender_card(tender_id: int, request: Request, db: Session = Depends(get_db)):
    tender = db.get(Tender, tender_id)
    if tender is None or (tender.deleted_at is not None and not request.state.user.is_admin):
        raise HTTPException(404, "Тендер не найден")
    matches = tender.matches or []
    active = get_active_keyword_set(db)
    mismatch = is_error(tender.verdict, tender.relevance)
    return templates.TemplateResponse(
        request,
        "tender.html",
        {
            "t": tender,
            "matches": matches,
            "verdict_labels": VERDICT_LABELS,
            "mismatch": mismatch,
            "fixes": tender_fixes(tender, active[1]) if active and mismatch else [],
            "words": word_suggestions(tender) if mismatch else [],
            "relevance_labels": RELEVANCE_LABELS,
            "field_labels": FIELD_LABELS,
            "Field": Field,
            "user_statuses": UserStatus.ALL,
            "source_titles": BY_CODE,
            "is_urgent": is_urgent(tender),
            "is_expired": is_expired(tender),
            "back": request.headers.get("referer") or "/",
        },
    )


@router.post("/tenders/{tender_id}/seen")
def tender_seen(tender_id: int, request: Request, db: Session = Depends(get_db)):
    mark_seen(db, tender_id=tender_id)
    if request.headers.get("hx-request"):
        return HTMLResponse('<span class="muted">просмотрен</span>')
    return RedirectResponse(f"/tenders/{tender_id}", status_code=303)


@router.post("/tenders/{tender_id}/status", response_class=HTMLResponse)
def tender_status(tender_id: int, user_status: str = Form(...), db: Session = Depends(get_db)):
    tender = db.get(Tender, tender_id)
    if tender is None or user_status not in UserStatus.ALL:
        raise HTTPException(400, "Неверный статус")
    tender.user_status = user_status
    tender.is_new = False
    return HTMLResponse('<span class="saved">сохранено</span>')


@router.post("/tenders/{tender_id}/verdict", response_class=HTMLResponse)
def tender_verdict(tender_id: int, request: Request, verdict: str = Form(""), db: Session = Depends(get_db)):
    value = verdict if verdict in VERDICT_LABELS else None
    tender = set_verdict(db, tender_id, value)
    if tender is None:
        raise HTTPException(404, "Тендер не найден")
    if request.headers.get("hx-request"):
        return templates.TemplateResponse(
            request,
            "_verdict.html",
            {
                "t": tender,
                "verdict_labels": VERDICT_LABELS,
                "mismatch": is_error(tender.verdict, tender.relevance),
            },
        )
    return RedirectResponse(f"/tenders/{tender_id}", status_code=303)


@router.post("/tenders/{tender_id}/delete")
def tender_delete(tender_id: int, db: Session = Depends(get_db)):
    if not delete_tenders(db, [tender_id]) and db.get(Tender, tender_id) is None:
        raise HTTPException(404, "Тендер не найден")
    return RedirectResponse(f"/tenders/{tender_id}?deleted=1", status_code=303)


@router.post("/tenders/{tender_id}/restore")
def tender_restore(tender_id: int, db: Session = Depends(get_db)):
    restore_tenders(db, [tender_id])
    return RedirectResponse(f"/tenders/{tender_id}", status_code=303)


BULK_ACTIONS = ("delete", "restore", "yes", "no", "clear", "seen")


@router.post("/tenders/bulk")
async def tenders_bulk(request: Request, db: Session = Depends(get_db)):
    """Действие над отмеченными тендерами или (scope=filter) над всеми по текущим фильтрам."""
    form = await request.form()
    action = str(form.get("action", ""))
    if action not in BULK_ACTIONS:
        raise HTTPException(400, "Неизвестное действие")
    if action in ("delete", "restore") and not request.state.user.is_admin:
        raise HTTPException(403, "Удалять тендеры может только администратор")
    if form.get("scope") == "filter":
        ids = filtered_ids(db, _filters(request, "all"))
    else:
        ids = [int(x) for x in form.getlist("ids") if str(x).isdigit()]
    if action == "delete":
        done = delete_tenders(db, ids)
    elif action == "restore":
        done = restore_tenders(db, ids)
    elif action == "seen":
        done = sum(mark_seen(db, tender_id=i) for i in ids)
    else:
        done = sum(set_verdict(db, i, None if action == "clear" else action) is not None for i in ids)
    query = request.url.query
    sep = "&" if query else ""
    return RedirectResponse(f"/?{query}{sep}done={done}&did={action}", status_code=303)


@router.post("/tenders/{tender_id}/notes", response_class=HTMLResponse)
def tender_notes(tender_id: int, notes: str = Form(""), db: Session = Depends(get_db)):
    tender = db.get(Tender, tender_id)
    if tender is None:
        raise HTTPException(404)
    tender.notes = notes[:10000]
    return HTMLResponse('<span class="saved">заметки сохранены</span>')


# --------------------------------------------------------------------------- площадки и прогоны


@router.get("/sources", response_class=HTMLResponse)
def sources_page(request: Request, db: Session = Depends(get_db)):
    stats = db.scalars(select(RunSourceStat).order_by(RunSourceStat.id.desc()).limit(300)).all()
    by_source: dict[str, list[RunSourceStat]] = {}
    for st in stats:
        by_source.setdefault(st.source_code, [])
        if len(by_source[st.source_code]) < 10:
            by_source[st.source_code].append(st)
    return templates.TemplateResponse(
        request,
        "sources.html",
        {
            "states": source_states(db),
            "stats": by_source,
            "active_run": active_run_id(),
            "schedule": get_schedule(db),
            "next_run": next_run_time(),
            "schedule_error": request.query_params.get("schedule_error"),
        },
    )


@router.post("/schedule")
def update_schedule(
    interval_hours: int = Form(...),
    start_time: str = Form(...),
    enabled: bool = Form(False),
    db: Session = Depends(get_db),
):
    schedule = Schedule(
        interval_hours=max(1, min(interval_hours, 24 * 30)), start_time=start_time.strip(), enabled=enabled
    )
    try:
        save_schedule(db, schedule)
    except ValueError as exc:
        return RedirectResponse(f"/sources?schedule_error={quote(str(exc))}", status_code=303)
    db.commit()
    apply_schedule(schedule)
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/{code}/toggle")
def toggle_source(code: str, db: Session = Depends(get_db)):
    get_source(code)
    setting = get_source_setting(db, code)
    setting.enabled = not setting.enabled
    return RedirectResponse("/sources", status_code=303)


@router.post("/runs/start", response_class=HTMLResponse)
async def start_run_now(request: Request):
    started = launch_run("manual")
    if not started:
        return HTMLResponse(
            '<div class="flash flash-error">Прогон уже идёт</div>'
            '<div hx-get="/runs/progress" hx-trigger="load delay:1s" hx-swap="outerHTML"></div>'
        )
    return HTMLResponse('<div hx-get="/runs/progress" hx-trigger="load delay:1s" hx-swap="outerHTML"></div>')


@router.get("/runs/progress", response_class=HTMLResponse)
def run_progress(request: Request, db: Session = Depends(get_db)):
    run = db.scalar(select(Run).where(Run.trigger != "import").order_by(Run.id.desc()))
    return templates.TemplateResponse(request, "_run_progress.html", {"run": run, "titles": BY_CODE})


@router.get("/runs", response_class=HTMLResponse)
def runs_page(request: Request, db: Session = Depends(get_db)):
    runs = db.scalars(select(Run).order_by(Run.id.desc()).limit(50)).all()
    return templates.TemplateResponse(request, "runs.html", {"runs": runs, "titles": BY_CODE})


# --------------------------------------------------------------------------- уведомления


@router.get("/notifications", response_class=HTMLResponse)
def notifications_page(request: Request, db: Session = Depends(get_db)):
    me = request.state.user
    stmt = select(Notification).order_by(Notification.id.desc()).limit(100)
    if me.is_admin:
        items = db.scalars(stmt).all()
        unread = {n.id for n in items if not n.is_read}
    else:
        # Менеджеру — только «Новые тендеры»; непрочитанные — с момента его прошлого визита.
        user = db.scalar(select(User).where(User.login == me.login))
        items = db.scalars(stmt.where(Notification.kind == NEW_TENDERS)).all()
        seen = aware(user.notifications_seen_at) if user else None
        unread = {n.id for n in items if seen is None or aware(n.created_at) > seen}
        if user is not None:
            user.notifications_seen_at = utcnow()
    return templates.TemplateResponse(
        request, "notifications.html", {"items": items, "unread": unread, "new_kind": NEW_TENDERS}
    )


@router.post("/notifications/read-all")
def notifications_read_all(request: Request, db: Session = Depends(get_db)):
    if request.state.user.is_admin:
        db.execute(update(Notification).where(Notification.is_read.is_(False)).values(is_read=True))
    return RedirectResponse("/notifications", status_code=303)
