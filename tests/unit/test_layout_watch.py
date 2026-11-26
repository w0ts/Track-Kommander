"""When Traktor's layout changes, the snapped overlay follows the browser."""

from __future__ import annotations

from ui.layout_watch import LayoutWatch


class _Overlay:
    def __init__(self, snapped: bool = True) -> None:
        self.snapped = snapped
        self.resnaps = 0

    def is_snapped(self) -> bool:
        return self.snapped

    def resnap(self) -> None:
        self.resnaps += 1


def _watch(overlay, readings):
    it = iter(readings)
    return LayoutWatch(overlay, probe=lambda: next(it, None))


def test_a_moved_browser_line_resnaps(monkeypatch) -> None:
    ov = _Overlay()
    w = _watch(ov, [0.48, 0.48, 0.72])
    w.tick()
    w.tick()
    w.tick()
    assert ov.resnaps == 1


def test_the_first_reading_only_sets_the_baseline() -> None:
    ov = _Overlay()
    w = _watch(ov, [0.48])
    w.tick()
    assert ov.resnaps == 0


def test_jitter_does_not_resnap() -> None:
    ov = _Overlay()
    w = _watch(ov, [0.480, 0.482, 0.479])
    w.tick()
    w.tick()
    w.tick()
    assert ov.resnaps == 0


def test_not_snapped_means_nothing_to_do() -> None:
    ov = _Overlay(snapped=False)
    w = _watch(ov, [0.48, 0.72])
    w.tick()
    w.tick()
    assert ov.resnaps == 0


def test_a_failed_capture_keeps_the_baseline() -> None:
    ov = _Overlay()
    w = _watch(ov, [0.48, None, 0.48, None, 0.72])
    for _ in range(5):
        w.tick()
    assert ov.resnaps == 1


# ── "Still snapped" survives a minimum-size clamp ───────────────────────────


def test_still_snapped_when_only_the_height_grew() -> None:
    """A 4-deck snap can ask for less height than the window's minimum; macOS
    then makes it taller. That is not the user moving it."""
    from ui.now_playing import still_snapped

    assert still_snapped((100, 700, 1728, 370), (100, 700, 1728, 520))


def test_a_moved_or_narrowed_window_is_not_snapped() -> None:
    from ui.now_playing import still_snapped

    assert not still_snapped((100, 700, 1728, 370), (140, 700, 1728, 370))
    assert not still_snapped((100, 700, 1728, 370), (100, 700, 1500, 370))
    assert not still_snapped((100, 700, 1728, 520), (100, 700, 1728, 370))


def test_a_pixel_of_slop_is_fine() -> None:
    from ui.now_playing import still_snapped

    assert still_snapped((100, 700, 1728, 370), (101, 699, 1727, 370))


# ── The probe may run elsewhere (off the GUI thread) ────────────────────────


class _Deferred:
    """Stands in for a thread: holds each probe until release()."""

    def __init__(self) -> None:
        self.pending: list = []

    def __call__(self, probe, done) -> None:
        self.pending.append((probe, done))

    def release(self) -> None:
        probe, done = self.pending.pop(0)
        done(probe())


def test_a_deferred_probe_resnaps_when_its_reading_arrives() -> None:
    ov = _Overlay()
    it = iter([0.48, 0.72])
    runner = _Deferred()
    w = LayoutWatch(ov, probe=lambda: next(it), run_probe=runner)
    w.tick()
    runner.release()
    w.tick()
    assert ov.resnaps == 0
    runner.release()
    assert ov.resnaps == 1


def test_no_second_probe_while_one_is_in_flight() -> None:
    ov = _Overlay()
    runner = _Deferred()
    w = LayoutWatch(ov, probe=lambda: 0.48, run_probe=runner)
    w.tick()
    w.tick()
    w.tick()
    assert len(runner.pending) == 1
    runner.release()
    w.tick()
    assert len(runner.pending) == 1


def test_a_reading_that_lands_after_unsnapping_is_ignored() -> None:
    ov = _Overlay()
    it = iter([0.48, 0.72])
    runner = _Deferred()
    w = LayoutWatch(ov, probe=lambda: next(it), run_probe=runner)
    w.tick()
    runner.release()
    w.tick()
    ov.snapped = False  # the user moved the window meanwhile
    runner.release()
    assert ov.resnaps == 0
