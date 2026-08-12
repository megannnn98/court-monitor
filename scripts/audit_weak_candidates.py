"""Audit weak (court-only) CaseMatchCandidate entries in the existing database.

Usage:
  uv run python scripts/audit_weak_candidates.py [--dry-run]

Without --dry-run, just prints statistics.
With --dry-run, also lists each weak candidate for review.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import select
from sqlalchemy.orm import Session

from court_monitor.storage.db import make_engine, session_scope
from court_monitor.storage.orm import Case, CaseMatchCandidate

MEANINGFUL_SIGNALS = {"article", "date", "person_name"}


@dataclass
class AuditResult:
    total_candidates: int = 0
    weak_only: int = 0
    with_meaningful: int = 0
    weak_ids: list[int] = None  # type: ignore

    def __post_init__(self) -> None:
        if self.weak_ids is None:
            self.weak_ids = []

    def summary(self) -> str:
        return dedent(f"""
        Всего CaseMatchCandidate: {self.total_candidates}
        Со meaningful сигналом:  {self.with_meaningful}
        Только court (слабые):   {self.weak_only}
        """)


def audit_weak_candidates(session: Session) -> AuditResult:
    result = AuditResult()
    stmt = select(CaseMatchCandidate).order_by(CaseMatchCandidate.id)
    candidates = list(session.execute(stmt).scalars().all())
    result.total_candidates = len(candidates)

    for c in candidates:
        signals = json.loads(c.signals_json) if c.signals_json else []
        signal_types = {s.get("signal_type", "") for s in signals}
        if signal_types & MEANINGFUL_SIGNALS:
            result.with_meaningful += 1
        else:
            result.weak_only += 1
            result.weak_ids.append(c.id)

    return result


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    engine = make_engine()
    with session_scope(engine) as session:
        audit = audit_weak_candidates(session)
        print(audit.summary())

        if dry_run and audit.weak_ids:
            print("Слабые кандидаты (только court, без article/date/person):")
            for cid in audit.weak_ids:
                c = session.get(CaseMatchCandidate, cid)
                case = session.get(Case, c.case_id) if c else None
                case_ref = case.case_number if case else "?"
                print(f"  id={cid:>6}  score={c.score:.2f}  status={c.status}  case={case_ref}")

    if audit.weak_only > 0 and not dry_run:
        print("\nДля просмотра деталей: uv run python scripts/audit_weak_candidates.py --dry-run")
        print(
            "\nДля безопасного удаления (уже неподдерживаемого на новом коде):\n"
            "  court-monitor reject-case-match --comment 'court-only signal' <id>\n"
            "(выполнить для каждого id выше)"
        )


if __name__ == "__main__":
    main()
