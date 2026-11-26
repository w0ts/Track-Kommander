"""The three things that have to be true for the bridge to work.

They form a chain, and each can be established independently:

1. **The D2 QML is patched** — our module is in Traktor's own mapping. Read
   off disk, no Traktor needed.
2. **A D2 controller is added** in Traktor's Controller Manager, which is what
   makes Traktor load that QML at all. Read from its settings file.
3. **The Web API is responding** — Traktor's bridge is actually posting to us.

Diagnostic order matters: if 1 is false nothing else can work, if 2 is false
the QML never loads, and only then is 3 worth looking at. The previous screen
showed only a version number reported *over* the working connection, which
told you nothing when it was broken — the one time you need it.

Each check is three-valued. "Unknown" is not "broken": if Traktor's settings
cannot be read, saying "not configured" would send the user to fix something
that is already right.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.d2_patch import patch_qml
from core.d2_status import api_check, controller_check, qml_check

QML = """
import CSI 1.0

Mapping
{
  Thing {}
}
"""


def _install(tmp_path: Path, *, patched: bool, api: bool) -> Path:
    d2 = tmp_path / "CSI" / "D2"
    d2.mkdir(parents=True)
    (d2 / "D2.qml").write_text(patch_qml(QML) if patched else QML)
    if api:
        (d2 / "Api").mkdir()
        (d2 / "Api" / "ApiModule.qml").write_text("// ours\n")
    return d2


# ── 1. Is the QML patched? ─────────────────────────────────────────────────


def test_a_patched_install_passes(tmp_path) -> None:
    got = qml_check(_install(tmp_path, patched=True, api=True))
    assert got.ok is True
    assert "install" in got.summary.lower() or "ready" in got.summary.lower()


def test_an_untouched_install_fails(tmp_path) -> None:
    got = qml_check(_install(tmp_path, patched=False, api=False))
    assert got.ok is False
    assert got.detail, "must say what to do about it"


def test_a_patch_without_the_module_is_not_ok(tmp_path) -> None:
    """The import would resolve to nothing and Traktor would fail to load."""
    got = qml_check(_install(tmp_path, patched=True, api=False))
    assert got.ok is False
    assert "Api" in got.detail


def test_the_module_without_the_patch_is_not_ok(tmp_path) -> None:
    """Files present but nothing references them — the old manual half-job."""
    got = qml_check(_install(tmp_path, patched=False, api=True))
    assert got.ok is False


def test_a_missing_install_is_unknown(tmp_path) -> None:
    got = qml_check(tmp_path / "nowhere")
    assert got.ok is None


def test_no_path_at_all_is_unknown() -> None:
    assert qml_check(None).ok is None


# ── 2. Is the controller added? ────────────────────────────────────────────


def test_controller_present(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("core.d2_status.has_d2", lambda _p: True)
    got = controller_check(tmp_path / "Traktor Settings.tsi")
    assert got.ok is True


def test_controller_absent_explains_how_to_add_it(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("core.d2_status.has_d2", lambda _p: False)
    got = controller_check(tmp_path / "Traktor Settings.tsi")
    assert got.ok is False
    assert "Controller Manager" in got.detail


def test_controller_unknown_when_settings_unreadable(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("core.d2_status.has_d2", lambda _p: None)
    assert controller_check(tmp_path / "x.tsi").ok is None


def test_controller_unknown_with_no_settings_file() -> None:
    assert controller_check(None).ok is None


# ── 3. Is the Web API responding? ──────────────────────────────────────────


def test_api_responding_recently(tmp_path) -> None:
    got = api_check(received=120, last_age_s=0.4)
    assert got.ok is True


def test_api_never_heard_from(tmp_path) -> None:
    got = api_check(received=0, last_age_s=None)
    assert got.ok is False
    assert "restarted" in got.detail, "the actionable step is a Traktor restart"


def test_api_went_quiet(tmp_path) -> None:
    """Messages arrived once and then stopped — a stalled bridge, which is a
    different problem from one that never started."""
    got = api_check(received=500, last_age_s=45.0)
    assert got.ok is False
    assert "stopped" in got.summary.lower() or "quiet" in got.summary.lower()


@pytest.mark.parametrize("age", [0.0, 1.0, 4.9])
def test_recent_enough_counts_as_responding(age) -> None:
    assert api_check(received=10, last_age_s=age).ok is True


@pytest.mark.parametrize("age", [5.1, 30.0, 600.0])
def test_too_long_ago_does_not(age) -> None:
    assert api_check(received=10, last_age_s=age).ok is False


# ── The chain ──────────────────────────────────────────────────────────────


def test_each_check_has_a_short_summary_and_a_reason(tmp_path) -> None:
    """The row shows the summary; the reason is what makes it actionable."""
    for check in (qml_check(_install(tmp_path, patched=False, api=False)), api_check(0, None)):
        assert check.summary and len(check.summary) < 60
        assert check.detail
