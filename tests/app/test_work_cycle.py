"""The next operator task follows one explicit priority order."""

import pytest

from web.ui.workload import Workload, next_operator_task


@pytest.mark.parametrize(
    ("work", "key", "title", "href"),
    [
        (
            Workload(pairs=3, unclear_roles=2, unclear_verdicts=1, unnamed=4, junk_holds=5),
            "junk_holds",
            "Публикации на проверке",
            "/ui/junk-holds",
        ),
        (
            Workload(pairs=3, unclear_roles=2, unclear_verdicts=1, unnamed=4),
            "pairs",
            "Совпадения людей",
            "/ui/pairs",
        ),
        (
            Workload(pairs=0, unclear_roles=2, unclear_verdicts=1, unnamed=4),
            "roles",
            "Неясные роли",
            "/ui/roles",
        ),
        (
            Workload(pairs=0, unclear_roles=0, unclear_verdicts=1, unnamed=4),
            "politics",
            "Проверка политичности",
            "/ui/politics-review",
        ),
        (
            Workload(pairs=0, unclear_roles=0, unclear_verdicts=0, unnamed=4),
            "unnamed",
            "Безымянные фигуранты",
            "/ui/unnamed",
        ),
    ],
)
def test_next_operator_task_uses_the_work_cycle_priority(
    work: Workload, key: str, title: str, href: str
) -> None:
    task = next_operator_task(work)

    assert task is not None
    assert (task.key, task.title, task.href) == (key, title, href)


def test_next_operator_task_is_none_when_every_queue_is_empty() -> None:
    assert next_operator_task(Workload(pairs=0, unclear_roles=0, unclear_verdicts=0)) is None
