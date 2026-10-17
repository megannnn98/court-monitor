"""Question history exposes independent values, ordered by id as the console expects."""

import json
from dataclasses import FrozenInstanceError

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from results.read import question_answer, recent_answers
from results.storage.models import ChatQuestionRecord


def test_answers_keep_history_order_and_do_not_expose_persistent_json() -> None:
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE chat_questions (id INTEGER PRIMARY KEY, asked_at DATETIME, question TEXT, calls JSON, answer TEXT, outcome TEXT, model TEXT, cost_usd FLOAT)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO chat_questions VALUES (:id, :asked_at, 'question', :calls, 'answer', 'answered', 'model', 0.1)"
                ),
                [
                    {
                        "id": 1,
                        "asked_at": "2026-10-17 12:00:00",
                        "calls": json.dumps([{"result": {"total": 3}}]),
                    },
                    {"id": 2, "asked_at": "2026-10-16 12:00:00", "calls": "[]"},
                ],
            )
        with Session(engine) as session:
            assert [answer.id for answer in recent_answers(session, limit=2)] == [2, 1]
            assert [answer.id for answer in recent_answers(session, limit=1)] == [2]
            assert question_answer(session, 999) is None
            persistent = session.get(ChatQuestionRecord, 1)
            answer = question_answer(session, 1)
            assert answer is not None and persistent is not None
            assert answer.calls == [{"result": {"total": 3}}]
            answer.calls[0]["result"]["total"] = 99
            assert persistent.calls[0]["result"]["total"] == 3
            with pytest.raises(FrozenInstanceError):
                answer.question = "changed"  # type: ignore[misc]  # Frozen at runtime as well.
    finally:
        engine.dispose()
