import io

from cli_progress import ProgressBar


class FakeTerminal(io.StringIO):
    def __init__(self, *, tty: bool = True) -> None:
        super().__init__()
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


class FakeClock:
    def __init__(self) -> None:
        self.seconds = 0.0

    def __call__(self) -> float:
        return self.seconds


def _bar(stream: FakeTerminal, clock: FakeClock, total: int = 10, **kwargs: object) -> ProgressBar:
    return ProgressBar(
        "resolve-people",
        total,
        stream=stream,
        now=clock,
        width=10,
        min_redraw_seconds=0.0,
        **kwargs,  # type: ignore[arg-type]
    )


def test_bar_fills_in_proportion_to_the_work_done() -> None:
    stream, clock = FakeTerminal(), FakeClock()
    bar = _bar(stream, clock)

    bar.advance(3)

    assert "resolve-people [###.......]  30%  3/10" in stream.getvalue()


def test_bar_redraws_in_place_instead_of_scrolling() -> None:
    stream, clock = FakeTerminal(), FakeClock()
    bar = _bar(stream, clock)

    bar.advance(1)
    clock.seconds = 1.0
    bar.advance(1)

    assert stream.getvalue().count("\r") == 2
    assert "\n" not in stream.getvalue()


def test_close_ends_the_line_so_result_output_starts_below_the_bar() -> None:
    stream, clock = FakeTerminal(), FakeClock()
    bar = _bar(stream, clock)

    bar.advance(1)
    bar.close()

    assert stream.getvalue().endswith("\n")


def test_nothing_is_written_when_the_stream_is_not_a_terminal() -> None:
    """Redirected output and CI logs must keep only the commands' own result lines."""
    stream, clock = FakeTerminal(tty=False), FakeClock()
    bar = _bar(stream, clock)

    bar.advance(5)
    bar.close()

    assert stream.getvalue() == ""


def test_nothing_is_written_when_there_is_no_work() -> None:
    stream, clock = FakeTerminal(), FakeClock()
    bar = _bar(stream, clock, total=0)

    bar.advance(1)
    bar.close()

    assert stream.getvalue() == ""


def test_rate_and_eta_are_reported_once_the_stage_is_under_way() -> None:
    stream, clock = FakeTerminal(), FakeClock()
    bar = _bar(stream, clock)

    clock.seconds = 60.0
    bar.advance(2)

    assert "2/min, ~4m left" in stream.getvalue()


def test_eta_is_reported_in_hours_for_a_stage_of_hours() -> None:
    stream, clock = FakeTerminal(), FakeClock()
    bar = _bar(stream, clock, total=1000)

    clock.seconds = 600.0
    bar.advance(10)

    assert "~16h30m left" in stream.getvalue()


def test_no_rate_is_guessed_before_the_first_step_completes() -> None:
    stream, clock = FakeTerminal(), FakeClock()
    bar = _bar(stream, clock)

    bar.advance(0)

    assert "left" not in stream.getvalue()


def test_redraws_are_throttled_but_the_final_step_always_lands() -> None:
    stream, clock = FakeTerminal(), FakeClock()
    bar = ProgressBar("extract", 3, stream=stream, now=clock, width=10, min_redraw_seconds=10.0)

    bar.advance(1)
    clock.seconds = 1.0
    bar.advance(1)  # throttled away
    clock.seconds = 2.0
    bar.advance(1)  # the last step is never throttled

    frames = [frame for frame in stream.getvalue().split("\r") if frame]

    assert len(frames) == 2
    assert "3/3" in frames[-1]


def test_advance_never_reports_more_than_the_total() -> None:
    stream, clock = FakeTerminal(), FakeClock()
    bar = _bar(stream, clock, total=2)

    bar.advance(5)

    assert "2/2" in stream.getvalue()
    assert "100%" in stream.getvalue()


def test_the_bar_closes_itself_when_used_as_a_context_manager() -> None:
    stream, clock = FakeTerminal(), FakeClock()

    with _bar(stream, clock) as bar:
        bar.advance(1)

    assert stream.getvalue().endswith("\n")
