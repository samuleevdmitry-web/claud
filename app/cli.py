"""Командная строка.

python -m app.cli check-keywords [файл.xlsx]   — разобрать файл ключей и показать ошибки
python -m app.cli classify "Название" --customer "Заказчик" [--position ...] [--code ...]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.classifier import RELEVANCE_LABELS, PositionText, TenderText, classify
from app.config import get_settings
from app.services.keywords import parse_keyword_file


def _load(path: str | None):
    file = Path(path) if path else get_settings().keywords_file
    return file, parse_keyword_file(file.read_bytes())


def check_keywords(args: argparse.Namespace) -> int:
    file, ks = _load(args.file)
    print(f"Файл: {file}")
    for key, value in ks.summary().items():
        print(f"  {key}: {value}")
    for issue in ks.issues:
        print(f"{'ОШИБКА' if issue.level == 'error' else 'внимание'}: {issue}  [{issue.mask}]")
    if args.notes:
        for key in [*ks.products, *ks.markers, *ks.minus_words]:
            for note in key.mask.notes:
                print(f"пояснение: {key.mask.source}: {note}")
    return 1 if ks.errors or not ks.is_usable else 0


def classify_cmd(args: argparse.Namespace) -> int:
    _, ks = _load(args.file)
    tender = TenderText(
        title=args.title,
        customer_name=args.customer,
        delivery_place=args.place,
        positions=[PositionText(p) for p in args.position],
        codes=args.code,
        notice_text=args.notice,
    )
    result = classify(ks, tender)
    label = RELEVANCE_LABELS[result.relevance] if result.relevance else "не сохраняется (нет товарных ключей)"
    print(f"{label}, score {result.score}")
    for reason in result.reasons:
        print(f"  — {reason}")
    for m in result.matches:
        flag = " [не учитывается]" if m.suppressed else ""
        print(f"  {m.kind:<13} {m.field:<14} «{m.fragment}» ← {m.label} ({m.mask}){flag}")
    return 0


def run_cmd(args: argparse.Namespace) -> int:
    import asyncio
    import logging

    from sqlalchemy import select

    from app.db import get_session_factory, session_scope
    from app.main import run_migrations
    from app.models import Run
    from app.services.keywords import bootstrap_keywords
    from app.services.runner import run_all

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run_migrations()
    from app.sources.tls import ensure_russian_ca

    ensure_russian_ca()
    with session_scope() as session:
        bootstrap_keywords(session)
    factory = get_session_factory()
    run_id = asyncio.run(
        run_all(
            factory,
            trigger="manual",
            only=args.source or None,
            max_queries=args.max_queries,
            queries=args.query or None,
        )
    )
    with factory() as session:
        run = session.get(Run, run_id)
        print(
            f"Прогон №{run.id}: {run.status}; новых {run.new_count} (релевантных {run.new_relevant}, "
            f"на проверку {run.new_review}), обновлено {run.updated_count}"
        )
        for st in session.scalars(select(Run).where(Run.id == run_id)).one().source_stats:
            print(
                f"  {st.source_code}: {st.status}, запросов {st.queries_done}/{st.queries_total}, "
                f"найдено {st.found}, новых {st.new}, обновлено {st.updated}, {st.duration_sec} с"
            )
            for err in st.errors[-5:]:
                print(f"    ! {err}")
    return 0 if run.status == "success" else 1


def probe_cmd(args: argparse.Namespace) -> int:
    import asyncio

    from app.probe import probe

    asyncio.run(probe(args.source or None, round_=args.round))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("check-keywords", help="разобрать файл ключевых слов")
    p.add_argument("file", nargs="?")
    p.add_argument("--notes", action="store_true", help="показать пояснения (беглые гласные)")
    p.set_defaults(func=check_keywords)

    p = sub.add_parser("classify", help="классифицировать тендер по тексту")
    p.add_argument("title")
    p.add_argument("--customer", default="")
    p.add_argument("--place", default="")
    p.add_argument("--position", action="append", default=[])
    p.add_argument("--code", action="append", default=[])
    p.add_argument("--notice", default="")
    p.add_argument("--file")
    p.set_defaults(func=classify_cmd)

    p = sub.add_parser("run", help="прогон по площадкам сейчас")
    p.add_argument("--source", action="append", help="код площадки (можно несколько)")
    p.add_argument("--max-queries", type=int, help="ограничить число запросов (для проверки)")
    p.add_argument("--query", action="append", help="свой поисковый запрос вместо запросов из файла")
    p.set_defaults(func=run_cmd)

    p = sub.add_parser("probe", help="снять ответы площадок для разработки адаптеров (с вашего компьютера)")
    p.add_argument("--source", action="append", help="код площадки (можно несколько)")
    p.add_argument("--round", type=int, default=1, choices=(1, 2), help="заход разведки (2 — уточняющий)")
    p.set_defaults(func=probe_cmd)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
