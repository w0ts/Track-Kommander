"""The result-row menu: recompute hotcues, show in Finder."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QSettings
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def tab(qapp, tmp_path, monkeypatch):
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    QCoreApplication.setApplicationName("tk-row-menu-test")
    QCoreApplication.setOrganizationDomain("rowmenu.invalid")
    import core.db as db

    monkeypatch.setattr(db, "_db_path", lambda: tmp_path / "t.db")
    db.init_db()
    import ui.now_playing as np_mod

    w = np_mod.NowPlayingTab()
    yield w
    w.deleteLater()


def test_the_row_menu_offers_recompute_hotcues(tab, monkeypatch) -> None:
    labels: list[str] = []
    asked: list[str] = []
    tab.recompute_hotcues_requested.connect(asked.append)

    class _Menu:
        def __init__(self, parent=None):
            pass

        def addMenu(self, label):
            return _Menu()

        def addSeparator(self):
            pass

        def addAction(self, label):
            labels.append(label)
            from PySide6.QtGui import QAction

            act = QAction(label)
            if label == "Recompute hotcues":
                _Menu.hotcues = act
            return act

        def exec(self, pos):
            _Menu.hotcues.trigger()

    monkeypatch.setattr("ui.np_actions.QMenu", _Menu)
    tab.table.setRowCount(1)
    tab.table._row_paths[0] = "/a.mp3"
    monkeypatch.setattr(tab.table, "rowAt", lambda y: 0)
    from PySide6.QtCore import QPoint

    tab._show_context_menu(QPoint(1, 1))

    assert "Recompute hotcues" in labels
    assert asked == ["/a.mp3"]


def test_the_row_menu_reveals_the_file(tab, monkeypatch) -> None:
    revealed: list[str] = []
    monkeypatch.setattr(
        "ui.np_actions.reveal_in_file_manager", lambda path: revealed.append(path) or True
    )

    class _Menu:
        def __init__(self, parent=None):
            pass

        def addMenu(self, label):
            return _Menu()

        def addSeparator(self):
            pass

        def addAction(self, label):
            from PySide6.QtGui import QAction

            act = QAction(label)
            if label.startswith("Show in "):
                _Menu.reveal = act
                _Menu.label = label
            return act

        def exec(self, pos):
            _Menu.reveal.trigger()

    monkeypatch.setattr("ui.np_actions.QMenu", _Menu)
    tab.table.setRowCount(1)
    tab.table._row_paths[0] = "/a.mp3"
    monkeypatch.setattr(tab.table, "rowAt", lambda y: 0)
    from PySide6.QtCore import QPoint

    tab._show_context_menu(QPoint(1, 1))

    assert revealed == ["/a.mp3"]
    import sys

    expected = {"darwin": "Show in Finder", "win32": "Show in Explorer"}.get(
        sys.platform, "Show in file manager"
    )
    assert _Menu.label == expected


def _menu_labels(tab, monkeypatch) -> list[str]:
    labels: list[str] = []

    class _Menu:
        def __init__(self, parent=None):
            pass

        def addMenu(self, label):
            labels.append("SUBMENU " + label)
            return _Menu()

        def addSeparator(self):
            labels.append("---")

        def addAction(self, label):
            from PySide6.QtGui import QAction

            labels.append(label)
            return QAction(label)

        def exec(self, pos):
            pass

    monkeypatch.setattr("ui.np_actions.QMenu", _Menu)
    tab.table.setRowCount(1)
    tab.table._row_paths[0] = "/a.mp3"
    monkeypatch.setattr(tab.table, "rowAt", lambda y: 0)
    from PySide6.QtCore import QPoint

    tab._show_context_menu(QPoint(1, 1))
    return labels


def test_load_into_deck_is_one_entry_per_deck_at_the_top_level(tab, monkeypatch) -> None:
    tab.show()
    tab.update_deck("C", {"file_path": "/c.mp3", "title": "C", "artist": "x"})
    labels = _menu_labels(tab, monkeypatch)
    assert not any(lb.startswith("SUBMENU") for lb in labels)
    assert labels[:4] == [
        "Load into deck A",
        "Load into deck B",
        "Load into deck C",
        "Load into deck D",
    ]


def test_only_the_decks_on_screen_are_offered(tab, monkeypatch) -> None:
    tab.show()
    labels = _menu_labels(tab, monkeypatch)
    decks = [lb for lb in labels if lb.startswith("Load into deck")]
    assert decks == ["Load into deck A", "Load into deck B"]
