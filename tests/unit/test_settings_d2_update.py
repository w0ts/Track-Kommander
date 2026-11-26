"""What Settings does once it notices Traktor was updated.

The detection itself is tested in ``test_d2_update.py``. What is pinned here
is the part that reaches the user: the D2 row has to say *why* it broke rather
than "Not installed", and the offer to reinstall has to be made once — a
dialog that comes back every time Settings is opened until you agree is not a
question, and one that never comes back is not an offer.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QSettings
from PySide6.QtWidgets import QApplication

from core.d2_patch import back_up, patch_qml
from ui.settings import SettingsTab

NI_STYLE = """
import CSI 1.0

Mapping
{
  Thing {}
}
"""

OLD = "Traktor Pro 4 (4.5.1)"
NEW = "Traktor Pro 4 (4.6.0)"


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def updated_traktor(qapp, tmp_path, monkeypatch):
    """A Settings tab looking at a Traktor that was updated under the patch."""
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(
        QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path / "qsettings")
    )
    QCoreApplication.setApplicationName("tk-d2-update-test")
    QCoreApplication.setOrganizationDomain("d2-update.invalid")

    d2 = tmp_path / "Traktor Pro 4.app" / "qml" / "CSI" / "D2"
    d2.mkdir(parents=True)
    (d2 / "D2.qml").write_text(NI_STYLE)

    backups = tmp_path / "backup"
    old_src = tmp_path / "src"
    old_src.mkdir()
    (old_src / "D2.qml").write_text(NI_STYLE)
    back_up(old_src, backups, OLD)

    api = tmp_path / "Api"
    api.mkdir()
    (api / "ApiModule.qml").write_text("// ours\n")

    w = SettingsTab()
    monkeypatch.setattr(w, "_d2_paths", lambda: (d2, api, backups, NEW))
    w.show()  # the offer is only ever made to a visible panel
    yield w
    w.hide()
    w.deleteLater()


def _ask(tab, monkeypatch, answer: bool) -> list[str]:
    """Record every confirm() the tab raises, answering *answer*."""
    seen: list[str] = []

    def fake_confirm(_parent, title, text, **_kw):
        seen.append(title)
        return answer

    monkeypatch.setattr("ui.dialogs.confirm", fake_confirm)
    monkeypatch.setattr(tab, "_patch_d2", lambda: None)
    return seen


def test_the_row_blames_the_update_not_the_user(updated_traktor) -> None:
    updated_traktor._refresh_d2_device()
    assert "updated" in updated_traktor.d2_msg.text().lower()
    assert OLD in updated_traktor.d2_msg.toolTip()
    assert NEW in updated_traktor.d2_msg.toolTip()


def test_the_offer_is_made(updated_traktor, monkeypatch) -> None:
    seen = _ask(updated_traktor, monkeypatch, answer=False)
    updated_traktor._refresh_d2_device()
    updated_traktor._offer_repatch()
    assert seen == ["Traktor was updated"]


def test_declining_is_an_answer(updated_traktor, monkeypatch) -> None:
    seen = _ask(updated_traktor, monkeypatch, answer=False)
    updated_traktor._refresh_d2_device()
    updated_traktor._offer_repatch()
    updated_traktor._offer_repatch()
    assert len(seen) == 1, "asked again after the user said no"


def test_accepting_runs_the_install(updated_traktor, monkeypatch) -> None:
    ran: list[bool] = []
    _ask(updated_traktor, monkeypatch, answer=True)
    monkeypatch.setattr(updated_traktor, "_patch_d2", lambda: ran.append(True))
    updated_traktor._refresh_d2_device()
    updated_traktor._offer_repatch()
    assert ran == [True]


def test_a_patched_install_is_never_asked_about(updated_traktor, monkeypatch) -> None:
    """The offer follows the detection; it does not have its own opinion."""
    d2 = updated_traktor._d2_paths()[0]
    Path(d2 / "D2.qml").write_text(patch_qml(NI_STYLE))
    seen = _ask(updated_traktor, monkeypatch, answer=True)
    updated_traktor._refresh_d2_device()
    updated_traktor._offer_repatch()
    assert seen == []


def test_a_panel_nobody_is_looking_at_never_asks(updated_traktor, monkeypatch) -> None:
    """There is no one to answer. It is also what keeps a modal dialog out of
    a headless test run on a machine whose Traktor really was updated — the
    check reads that machine's own backup directory."""
    seen = _ask(updated_traktor, monkeypatch, answer=True)
    updated_traktor._refresh_d2_device()
    updated_traktor.hide()
    updated_traktor._offer_repatch()
    assert seen == []


def test_switching_to_the_traktor_tab_makes_the_offer(updated_traktor, monkeypatch) -> None:
    """Driven through the real entry point, not by calling the handler. The
    offer is only worth anything if it is still wired to something."""
    seen = _ask(updated_traktor, monkeypatch, answer=False)
    tabs = updated_traktor._tab_widget
    other = next(i for i in range(tabs.count()) if tabs.tabText(i) != "Traktor")
    traktor = next(i for i in range(tabs.count()) if tabs.tabText(i) == "Traktor")
    tabs.setCurrentIndex(other)
    assert seen == [], "asked from a tab that is not about Traktor"
    tabs.setCurrentIndex(traktor)
    assert seen == ["Traktor was updated"]


def test_opening_settings_on_the_traktor_tab_makes_the_offer(updated_traktor, monkeypatch) -> None:
    """Restoring the last-used tab does not emit currentChanged, so without
    the showEvent path the person most likely to see this — whoever was last
    on that tab — would never be asked."""
    seen = _ask(updated_traktor, monkeypatch, answer=False)
    tabs = updated_traktor._tab_widget
    # Silently, the way _assemble_tabs restores the last-used tab: before the
    # currentChanged connection exists, so nothing is asked on the way in.
    tabs.blockSignals(True)
    tabs.setCurrentIndex(next(i for i in range(tabs.count()) if tabs.tabText(i) == "Traktor"))
    tabs.blockSignals(False)
    assert seen == []

    updated_traktor.hide()
    updated_traktor.show()
    QCoreApplication.processEvents()  # the deferred singleShot(0, …)
    assert seen == ["Traktor was updated"]


def test_a_new_traktor_version_is_asked_about_again(updated_traktor, monkeypatch) -> None:
    """Saying no to 4.6.0 is not saying no to 4.7.0."""
    d2, api, backups, _version = updated_traktor._d2_paths()
    seen = _ask(updated_traktor, monkeypatch, answer=False)

    updated_traktor._refresh_d2_device()
    updated_traktor._offer_repatch()

    monkeypatch.setattr(
        updated_traktor, "_d2_paths", lambda: (d2, api, backups, "Traktor Pro 4 (4.7.0)")
    )
    updated_traktor._refresh_d2_device()
    updated_traktor._offer_repatch()
    assert len(seen) == 2
