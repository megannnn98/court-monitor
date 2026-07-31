"""The list of work a full pass has to do, shared by the CLI and the web job.

Both used to iterate the two source flavours themselves, with the same
try/accumulate/except shape written twice. Only the rendering differs — colour
on the terminal, a summary dict in the job result — so only the rendering
should live at the call site.
"""

from __future__ import annotations

import pytest

from court_monitor.config.loader import MonitoringConfig, SourceConfig
from court_monitor.config.registry import SourceRegistryEntry
from court_monitor.domain.models import SourceBackend, SourceType
from court_monitor.services import work as work_module
from court_monitor.services.work import plan_all_work


@pytest.fixture()
def monitoring() -> MonitoringConfig:
    return MonitoringConfig(criminal_articles=["205"], keywords=[])


def _sudrf(name: str, *, enabled: bool = True) -> SourceConfig:
    return SourceConfig(
        name=name,
        type=SourceType.sudrf,
        backend=SourceBackend.fixture,
        enabled=enabled,
    )


def _channel(entry_id: str, *, enabled: bool = True) -> SourceRegistryEntry:
    return SourceRegistryEntry(
        id=entry_id,
        name=entry_id,
        url=f"https://t.me/{entry_id}",
        domain="t.me",
        source_type="telegram",
        username=entry_id,
        enabled=enabled,
    )


@pytest.fixture()
def stub_config(monkeypatch):
    """Drive the planner off in-memory config instead of the repo's YAML."""

    def _apply(sources, entries):
        monkeypatch.setattr(work_module, "load_sources", lambda: list(sources))
        monkeypatch.setattr(work_module, "load_registry", lambda: list(entries))

    return _apply


def test_both_flavours_are_planned_in_order(stub_config, monitoring):
    stub_config([_sudrf("Суд А")], [_channel("channel_a")])

    groups = plan_all_work(monitoring)

    assert [g.title for g in groups] == ["Sudrf-источники", "Telegram-каналы"]
    assert [w.label for g in groups for w in g.items] == ["Суд А", "channel_a"]


def test_disabled_entries_are_left_out_of_the_plan(stub_config, monitoring):
    stub_config(
        [_sudrf("Включён"), _sudrf("Выключен", enabled=False)],
        [_channel("on"), _channel("off", enabled=False)],
    )

    groups = plan_all_work(monitoring)

    assert [w.label for g in groups for w in g.items] == ["Включён", "on"]


def test_an_empty_group_still_appears_so_the_ui_can_say_so(stub_config, monitoring):
    """The CLI prints "(нет включённых источников)" — it needs the empty group,
    which is why the planner returns groups rather than a flat iterator."""
    stub_config([], [])

    groups = plan_all_work(monitoring)

    assert [g.title for g in groups] == ["Sudrf-источники", "Telegram-каналы"]
    assert all(g.items == [] for g in groups)
    assert all(g.empty_note for g in groups)


def test_a_channel_without_a_saved_fixture_is_flagged_not_dropped(stub_config, monitoring):
    """Fixture mode with nothing saved is a skip the operator should see, not
    an error and not silence."""
    stub_config([], [_channel("no_fixture_here")])

    (work,) = plan_all_work(monitoring, live=False)[1].items

    assert work.fixture_missing is True


def test_live_mode_never_reports_a_missing_fixture(stub_config, monitoring):
    stub_config([], [_channel("no_fixture_here")])

    (work,) = plan_all_work(monitoring, live=True)[1].items

    assert work.fixture_missing is False


def test_running_a_planned_item_calls_through_with_the_session(
    stub_config, monitoring, monkeypatch
):
    """The plan carries *what to run*; the caller owns the session, because the
    CLI opens one per source and the job shares a single one."""
    seen: dict[str, object] = {}

    def _fake_process_source(session, source_cfg, monitoring_cfg, **kwargs):
        seen["session"] = session
        seen["source"] = source_cfg.name
        return "sudrf-stats"

    monkeypatch.setattr(work_module, "process_source", _fake_process_source)
    stub_config([_sudrf("Суд А")], [])

    (work,) = plan_all_work(monitoring)[0].items
    result = work.run("the-session")

    assert result == "sudrf-stats"
    assert seen == {"session": "the-session", "source": "Суд А"}


def test_a_channel_is_run_with_live_and_its_fixture_path(stub_config, monitoring, monkeypatch):
    seen: dict[str, object] = {}

    def _fake_process_registry_source(session, entry, monitoring_cfg, **kwargs):
        seen.update(source=entry.id, **kwargs)
        return "registry-stats"

    monkeypatch.setattr(work_module, "process_registry_source", _fake_process_registry_source)
    stub_config([], [_channel("channel_a")])

    (work,) = plan_all_work(monitoring, live=True)[1].items
    result = work.run("the-session")

    assert result == "registry-stats"
    assert seen["source"] == "channel_a"
    assert seen["live"] is True
    assert str(seen["fixture_path"]).endswith("tg_preview_channel_a.html")


def test_the_job_and_the_cli_now_read_the_same_fixture(stub_config, monitoring, monkeypatch):
    """Regression: the background job used to call process_registry_source with
    no fixture_path at all, so with live=False the adapter had nothing to read
    and the web UI's "Собрать источники" collected zero Telegram posts — while
    the identical CLI command worked. Both go through the plan now, so both get
    the path."""
    seen: list[object] = []

    def _fake(session, entry, monitoring_cfg, **kwargs):
        seen.append(kwargs.get("fixture_path"))
        return "stats"

    monkeypatch.setattr(work_module, "process_registry_source", _fake)
    stub_config([], [_channel("channel_a")])

    for live in (False, True):
        (item,) = plan_all_work(monitoring, live=live)[1].items
        item.run("session")

    assert all(path and path.endswith("tg_preview_channel_a.html") for path in seen)
    assert len(seen) == 2
