"""Structure tests for the tabbed Library screen.

Same failure mode as the Settings tabs: a group can be built and then never
placed on a tab, which makes it silently disappear rather than error. The
export controls are new, so they are pinned too.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6.QtWidgets")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QSettings, Qt
from PySide6.QtWidgets import QApplication, QGroupBox, QScrollArea


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def library_tab(qapp, tmp_path, monkeypatch):
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    QCoreApplication.setApplicationName("tk-library-tabs-test")
    QCoreApplication.setOrganizationDomain("library-tabs.invalid")
    import ui.library_export_selection as mod

    # Whatever is plugged into the machine running the tests is not a fixture.
    monkeypatch.setattr(mod, "_removable_volumes", list)
    from ui.library import LibraryTab

    w = LibraryTab()
    qapp.processEvents()  # the playlists load one event-loop turn later
    yield w
    w.deleteLater()


def _groups(tab) -> set[str]:
    tw = tab._tab_widget
    found: set[str] = set()
    for i in range(tw.count()):
        found |= {g.title() for g in tw.widget(i).findChildren(QGroupBox)}
    return found


def test_every_group_is_on_a_tab(library_tab) -> None:
    expected = {
        "TRACK SOURCE",
        "PROGRESS",
        "LIBRARY",
        "HOTCUES",
        "AUTOMATIC PLAYLISTS",
        "EXPORT TO USB",
    }
    missing = expected - _groups(library_tab)
    assert not missing, f"groups built but not placed on any tab: {missing}"


def test_tab_names(library_tab) -> None:
    tw = library_tab._tab_widget
    assert [tw.tabText(i) for i in range(tw.count())] == [
        "Scan",
        "Hotcues",
        "Tagging",
        "Playlists",
        "Export",
    ]


def test_no_empty_tabs(library_tab) -> None:
    tw = library_tab._tab_widget
    for i in range(tw.count()):
        assert tw.widget(i).findChildren(QGroupBox), f"tab {tw.tabText(i)!r} is empty"


@pytest.mark.parametrize(
    "name",
    [
        # Widgets the scan/hotcue logic reaches for by name.
        "nml_label",
        "scan_btn",
        "stop_btn",
        "skip_cb",
        "check_lib_btn",
        "progress_bar",
        "status_lbl",
        "stats_lbl",
        "timing_text",
        "hc_btn",
        "hc_stop_btn",
        "hc_force_cb",
        "hc_cleanup_btn",
        "hc_progress",
        # New export controls.
        "rb_offset",
        "rb_status",
        "rb_cdj_btn",
        "rb_playlists",
        "rb_all_btn",
        "rb_none_btn",
        "rb_reload_btn",
        "rb_source_note",
        "rb_progress",
        "rb_mode",
        "rb_reanalyse",
    ],
)
def test_widget_survives_the_tab_split(library_tab, name: str) -> None:
    assert hasattr(library_tab, name)


def test_check_library_signal_still_exists(library_tab) -> None:
    assert hasattr(library_tab, "check_library_requested")


def test_offset_is_bounded_and_defaults_to_no_shift(library_tab) -> None:
    assert library_tab.rb_offset.value() == 0
    assert library_tab.rb_offset.minimum() < 0 < library_tab.rb_offset.maximum()


def test_last_tab_is_remembered(library_tab) -> None:
    tw = library_tab._tab_widget
    tw.setCurrentIndex(tw.count() - 1)
    name = tw.tabText(tw.count() - 1)

    from ui.library import LibraryTab

    reopened = LibraryTab()
    try:
        rw = reopened._tab_widget
        assert rw.tabText(rw.currentIndex()) == name
    finally:
        reopened.deleteLater()


def test_a_saved_rekordbox_tab_reopens_as_export(library_tab) -> None:
    """The tab was called Rekordbox; a remembered name must still land."""
    library_tab._settings.setValue(library_tab._LAST_TAB_KEY, "Rekordbox")

    from ui.library import LibraryTab

    reopened = LibraryTab()
    try:
        rw = reopened._tab_widget
        assert rw.tabText(rw.currentIndex()) == "Export"
    finally:
        reopened.deleteLater()


# ── Multi-playlist selection ────────────────────────────────────────────────
# The tests that look at the playlists use the fixture collection, not
# the one on this machine: what comes first there is the owner's business,
# and a smart playlist that cannot be exported is greyed out, not checkable.


def test_playlists_are_individually_checkable(fixture_library_tab) -> None:
    from PySide6.QtCore import Qt

    lst = fixture_library_tab.rb_playlists
    item = lst.item(0)
    assert item.flags() & Qt.ItemFlag.ItemIsUserCheckable
    assert item.checkState() == Qt.CheckState.Unchecked, "nothing ticked by default"


def test_all_and_none_buttons(fixture_library_tab) -> None:
    tab = fixture_library_tab
    lst = tab.rb_playlists
    assert lst.count() == 2
    tab.rb_all_btn.click()
    assert len(tab._selected_playlists()) == lst.count()
    tab.rb_none_btn.click()
    assert tab._selected_playlists() == []


def test_nothing_ticked_means_whole_collection(fixture_library_tab) -> None:
    fixture_library_tab._check_all_playlists(False)
    fixture_library_tab._on_rb_source_changed(0)
    assert "whole collection" in fixture_library_tab.rb_status.text().lower()


def test_several_playlists_can_be_selected_at_once(fixture_library_tab) -> None:
    from PySide6.QtCore import Qt

    lst = fixture_library_tab.rb_playlists
    assert lst.count() == 2
    for i in range(2):
        lst.item(i).setCheckState(Qt.CheckState.Checked)
    assert len(fixture_library_tab._selected_playlists()) == 2


def test_clicking_the_label_toggles_the_checkbox(fixture_library_tab, qapp) -> None:
    """Qt only toggles on the indicator itself; the whole row should work."""
    from PySide6.QtCore import QEvent, QPoint, Qt
    from PySide6.QtGui import QMouseEvent

    lst = fixture_library_tab.rb_playlists
    item = lst.item(0)
    assert item.checkState() == Qt.CheckState.Unchecked

    rect = lst.visualItemRect(item)
    # Well clear of the indicator — on the text.
    pos = rect.center() + QPoint(40, 0)
    for state in (Qt.CheckState.Checked, Qt.CheckState.Unchecked):
        lst.mousePressEvent(
            QMouseEvent(
                QEvent.Type.MouseButtonPress,
                pos,
                lst.viewport().mapToGlobal(pos),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
        )
        assert item.checkState() == state


def test_clicking_the_indicator_toggles_once_not_twice(fixture_library_tab) -> None:
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QMouseEvent

    lst = fixture_library_tab.rb_playlists
    item = lst.item(0)
    item.setCheckState(Qt.CheckState.Unchecked)
    rect = lst.visualItemRect(item)
    pos = rect.topLeft() + type(rect.topLeft())(8, rect.height() // 2)
    lst.mousePressEvent(
        QMouseEvent(
            QEvent.Type.MouseButtonPress,
            pos,
            lst.viewport().mapToGlobal(pos),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )
    assert item.checkState() == Qt.CheckState.Checked


def test_reanalyse_is_off_by_default(library_tab) -> None:
    """It decodes the whole selection and rewrites every file's tags, so it
    has to be something the user asks for."""
    assert library_tab.rb_reanalyse.isChecked() is False


# ── Export controls are wired to the worker ─────────────────────────────────


def test_the_export_has_a_stop_button_that_starts_disabled(library_tab) -> None:
    """The export runs on a thread now; Stop is the only way to end it early,
    and it must not be pressable before one is running."""
    assert library_tab.rb_stop_btn.text() == "Stop"
    assert library_tab.rb_stop_btn.isEnabled() is False


def test_stop_is_safe_with_no_export_running(library_tab) -> None:
    """Nothing to stop must not raise — the button outlives any one run."""
    library_tab._stop_export()
    assert library_tab.rb_stop_btn.isEnabled() is False


def test_progress_drives_the_bar_and_the_label(library_tab) -> None:
    library_tab._on_export_progress(3, 8, "Copying 1/4 — a.mp3")
    assert library_tab.rb_progress.maximum() == 8
    assert library_tab.rb_progress.value() == 3
    assert "Copying" in library_tab.rb_status.text()


def test_finishing_re_enables_the_export_button(library_tab) -> None:
    library_tab.rb_cdj_btn.setEnabled(False)
    library_tab.rb_stop_btn.setEnabled(True)
    library_tab._finish_export()
    assert library_tab.rb_cdj_btn.isEnabled()
    assert library_tab.rb_stop_btn.isEnabled() is False
    assert library_tab.rb_progress.isVisible() is False


def test_the_key_is_added_to_unless_replace_is_chosen(library_tab) -> None:
    """Replace deletes audio off the key, so it is never where the box starts."""
    assert library_tab.rb_mode.count() == 2
    assert library_tab.rb_mode.currentData() == "add"


def test_the_two_modes_are_the_only_ones(library_tab) -> None:
    """One decision, two answers — the sweep is what Replace means, not a
    separate switch that can be left in a state nobody wants."""
    modes = [library_tab.rb_mode.itemData(i) for i in range(library_tab.rb_mode.count())]
    assert modes == ["add", "replace"]
    for gone in ("rb_merge", "rb_keep", "rb_sweep"):
        assert not hasattr(library_tab, gone)


# ── Remembered playlist selection ───────────────────────────────────────────

_NML = """<?xml version="1.0" encoding="UTF-8"?>
<NML VERSION="19">
 <COLLECTION ENTRIES="0"></COLLECTION>
 <PLAYLISTS>
  <NODE TYPE="FOLDER" NAME="$ROOT">
   <SUBNODES COUNT="2">
    <NODE TYPE="PLAYLIST" NAME="Set A">
     <PLAYLIST ENTRIES="1" TYPE="LIST">
      <ENTRY><PRIMARYKEY TYPE="TRACK" KEY="Macintosh HD/:Music/:a.mp3"/></ENTRY>
     </PLAYLIST>
    </NODE>
    <NODE TYPE="PLAYLIST" NAME="Set B">
     <PLAYLIST ENTRIES="1" TYPE="LIST">
      <ENTRY><PRIMARYKEY TYPE="TRACK" KEY="Macintosh HD/:Music/:b.mp3"/></ENTRY>
     </PLAYLIST>
    </NODE>
   </SUBNODES>
  </NODE>
 </PLAYLISTS>
</NML>
"""


@pytest.fixture
def fixture_library_tab(library_tab, tmp_path, monkeypatch):
    """A LibraryTab whose collection is a two-playlist fixture NML."""
    nml = tmp_path / "collection.nml"
    nml.write_text(_NML)
    import ui.library as mod

    monkeypatch.setattr(mod, "find_traktor_nml", lambda: nml)
    library_tab.reload_playlists()
    return library_tab


def _keys(tab) -> list[str]:
    return [p.display for p in tab._selected_playlists()]


def test_ticked_playlists_survive_a_reload(fixture_library_tab) -> None:
    from PySide6.QtCore import Qt

    tab = fixture_library_tab
    assert tab.rb_playlists.count() == 2
    tab.rb_playlists.item(1).setCheckState(Qt.CheckState.Checked)
    tab.reload_playlists()
    assert _keys(tab) == ["Set B"]


def test_ticked_playlists_survive_reopening_the_tab(fixture_library_tab) -> None:
    from PySide6.QtCore import Qt

    tab = fixture_library_tab
    tab.rb_playlists.item(0).setCheckState(Qt.CheckState.Checked)

    from ui.library import LibraryTab

    reopened = LibraryTab()
    try:
        # The collection is read after the window first paints, not in the
        # constructor.
        assert reopened.rb_playlists.count() == 0
        QApplication.processEvents()
        assert _keys(reopened) == ["Set A"]
    finally:
        reopened.deleteLater()


def test_the_nml_is_read_once_per_action(fixture_library_tab, monkeypatch) -> None:
    """Resolving the collection walks the Traktor installs; one reload needs
    it once, not once per helper."""
    import ui.library as mod

    calls: list[int] = []
    real = mod.find_traktor_nml
    monkeypatch.setattr(mod, "find_traktor_nml", lambda: calls.append(1) or real())
    fixture_library_tab.refresh_traktor_config()
    assert len(calls) == 1


def test_signals_are_unblocked_when_a_reload_fails(fixture_library_tab, monkeypatch) -> None:
    import core.playlists

    tab = fixture_library_tab

    def boom(_path):
        raise ValueError("unexpected")

    monkeypatch.setattr(core.playlists, "parse_playlists", boom)
    with pytest.raises(ValueError):
        tab.reload_playlists()
    assert not tab.rb_playlists.signalsBlocked()


def test_untick_all_is_remembered_too(fixture_library_tab) -> None:
    tab = fixture_library_tab
    tab.rb_all_btn.click()
    tab.rb_none_btn.click()
    tab.reload_playlists()
    assert _keys(tab) == []


def test_a_playlist_that_disappeared_is_ignored(fixture_library_tab) -> None:
    from PySide6.QtCore import QSettings

    tab = fixture_library_tab
    QSettings().setValue("usb_export_playlists", ["Gone", "Set A"])
    tab.reload_playlists()
    assert _keys(tab) == ["Set A"]


def test_a_missing_collection_does_not_forget_the_selection(
    fixture_library_tab, monkeypatch
) -> None:
    """A missing NML gives an empty list; that must not wipe the memory."""
    from PySide6.QtCore import QSettings

    tab = fixture_library_tab
    tab.rb_all_btn.click()
    import ui.library as mod

    monkeypatch.setattr(mod, "find_traktor_nml", lambda: None)
    tab.reload_playlists()
    assert tab.rb_playlists.count() == 0
    assert QSettings().value("usb_export_playlists", [], type=list) == ["Set A", "Set B"]


# ── Remembered export destination ───────────────────────────────────────────


def test_the_remembered_destination_is_preselected(library_tab, tmp_path) -> None:
    from PySide6.QtCore import QSettings

    stick = tmp_path / "Key"
    stick.mkdir()
    QSettings().setValue("usb_export_path", str(stick))

    from ui.library import LibraryTab

    tab = LibraryTab()
    try:
        assert tab.export_destination() == stick
        assert tab.rb_dest.currentData() == str(stick)
    finally:
        tab.deleteLater()


def test_choosing_a_destination_is_remembered(library_tab, tmp_path) -> None:
    from PySide6.QtCore import QSettings

    stick = tmp_path / "Other"
    stick.mkdir()
    library_tab.set_export_destination(stick)
    assert library_tab.export_destination() == stick
    assert QSettings().value("usb_export_path", "", type=str) == str(stick)


def test_an_unplugged_destination_is_not_offered(library_tab, tmp_path) -> None:
    from PySide6.QtCore import QSettings

    QSettings().setValue("usb_export_path", str(tmp_path / "gone"))
    from ui.library import LibraryTab

    tab = LibraryTab()
    try:
        assert tab.export_destination() is None
        assert str(tmp_path / "gone") not in [
            tab.rb_dest.itemData(i) for i in range(tab.rb_dest.count())
        ]
    finally:
        tab.deleteLater()


def test_mounted_volumes_are_listed_but_not_guessed(library_tab, monkeypatch, tmp_path) -> None:
    """Whatever is plugged in is offered — never picked on the user's behalf,
    since "Replace" deletes what is on it."""
    import ui.library_export_selection as mod

    stick = tmp_path / "Volumes" / "KEY"
    stick.mkdir(parents=True)
    monkeypatch.setattr(mod, "_removable_volumes", lambda: [("KEY", str(stick))])
    library_tab.refresh_export_destinations()
    data = [library_tab.rb_dest.itemData(i) for i in range(library_tab.rb_dest.count())]
    assert str(stick) in data
    assert library_tab.export_destination() is None
    assert "choose" in library_tab.rb_dest.currentText().lower()


def test_export_asks_for_a_folder_only_when_none_is_chosen(
    library_tab, monkeypatch, tmp_path
) -> None:
    """The dialog is the fallback, not a step on every export."""
    import ui.library_export_selection as mod

    asked: list[str] = []
    monkeypatch.setattr(
        mod.QFileDialog, "getExistingDirectory", lambda *a, **k: asked.append("dialog") or ""
    )
    stick = tmp_path / "Key"
    stick.mkdir()
    library_tab.set_export_destination(stick)
    assert library_tab._pick_export_destination() == stick
    assert asked == []

    stick.rmdir()
    library_tab.refresh_export_destinations()
    assert library_tab._pick_export_destination() is None
    assert asked == ["dialog"]


# ── "Traktor is running" is dismissed per use ───────────────────────────────


def _capture_confirms(monkeypatch):
    """Traktor 'running', and every dismissible confirm recorded, not shown."""
    import ui.dialogs as dialogs
    import ui.library as mod

    seen: list[dict] = []
    monkeypatch.setattr(mod, "is_traktor_running", lambda: True)

    def _fake(parent, title, text, *, key, yes_text="", **kw):
        if dialogs.is_warning_dismissed(key):
            return True
        seen.append({"key": key, "text": text, "yes": yes_text})
        return False

    monkeypatch.setattr(dialogs, "confirm_dismissible", _fake)
    return seen


def test_scan_and_export_warn_under_different_keys(library_tab, monkeypatch) -> None:
    seen = _capture_confirms(monkeypatch)
    library_tab._warn_traktor_open_for_scan()
    library_tab._warn_traktor_open_for_export()
    keys = [s["key"] for s in seen]
    assert len(set(keys)) == 2, keys


def test_dismissing_it_for_the_scan_keeps_it_for_the_export(library_tab, monkeypatch) -> None:
    from PySide6.QtCore import QSettings

    from ui.dialogs import DISMISSED_GROUP

    seen = _capture_confirms(monkeypatch)
    QSettings().setValue(f"{DISMISSED_GROUP}/scan_traktor_running", True)
    assert library_tab._warn_traktor_open_for_scan() is True
    library_tab._warn_traktor_open_for_export()
    assert [s["key"] for s in seen] == ["export_traktor_running"]


def test_the_export_warning_talks_about_exporting(library_tab, monkeypatch) -> None:
    seen = _capture_confirms(monkeypatch)
    library_tab._warn_traktor_open_for_export()
    assert "scan" not in seen[0]["text"].lower()
    assert "export" in seen[0]["yes"].lower()


# ── The time left sits on the right, not at the end of a moving line ────────


def test_the_eta_has_its_own_right_aligned_label(library_tab) -> None:
    from PySide6.QtCore import Qt

    library_tab._on_export_progress(3, 10, "Copying 3/10 — a.mp3  ·  about 8 min left")
    assert library_tab.rb_status.text() == "Copying 3/10 — a.mp3"
    assert library_tab.rb_eta.text() == "about 8 min left"
    assert library_tab.rb_eta.alignment() & Qt.AlignmentFlag.AlignRight


def test_no_eta_clears_the_label(library_tab) -> None:
    library_tab._on_export_progress(3, 10, "Copying 3/10 — a.mp3  ·  about 8 min left")
    library_tab._on_export_progress(10, 10, "Writing the database…")
    assert library_tab.rb_status.text() == "Writing the database…"
    assert library_tab.rb_eta.text() == ""


# ── Nothing on the main thread touches a stick while it is being written ────


def test_destinations_are_not_rescanned_during_an_export(library_tab, monkeypatch) -> None:
    import ui.library_export_selection as mod

    calls: list[int] = []
    monkeypatch.setattr(mod, "_removable_volumes", lambda: calls.append(1) or [])
    library_tab._export_worker = object()  # an export is running
    library_tab.refresh_export_destinations()
    assert calls == []
    library_tab._export_worker = None
    library_tab.refresh_export_destinations()
    assert calls == [1]


def test_mounts_are_watched_not_polled(library_tab) -> None:
    """A stat on a saturated FSKit stick can block for seconds; polling the
    volumes every 2 s from the main thread beachballs the app."""
    import sys

    if sys.platform != "darwin":
        pytest.skip("watching /Volumes is a macOS thing")
    assert "/Volumes" in library_tab._mount_watcher.directories()
    assert not library_tab._dest_timer.isActive()


# ── Recompute the hotcues of one track ──────────────────────────────────────


def test_recompute_hotcues_for_one_track_runs_a_forced_single_detection(
    library_tab, monkeypatch, tmp_path
) -> None:
    import ui.library as mod

    track = tmp_path / "a.mp3"
    track.write_bytes(b"x")
    nml = tmp_path / "collection.nml"
    nml.write_text("<NML/>")
    monkeypatch.setattr(mod, "find_traktor_nml", lambda: nml)
    monkeypatch.setattr(library_tab, "_confirm_traktor_closed", lambda action: True)
    monkeypatch.setattr(
        "ui.library_hotcues.hotcue_eligibility",
        lambda p, include_existing=False: (
            ([(str(track), 128.0, 10.0), ("/other.mp3", 120.0, 0.0)] if include_existing else []),
            {},
        ),
    )
    started = _stub_hotcue_worker(monkeypatch)
    monkeypatch.delenv("TK_EMBED", raising=False)
    assert library_tab.recompute_hotcues_for(str(track)) is True
    assert started == [
        {
            "nml": str(nml),
            "candidates": [(str(track), 128.0, 10.0)],
            "force": True,
            "nml_write": True,
        }
    ]


def _stub_hotcue_worker(monkeypatch) -> list[dict]:
    started: list[dict] = []

    class _Worker:
        def __init__(self, nml, candidates, force=False, parent=None, write_nml=True):
            started.append(
                {"nml": nml, "candidates": candidates, "force": force, "nml_write": write_nml}
            )
            from PySide6.QtCore import QObject, Signal

            class _S(QObject):
                progress = Signal(int, int, str)
                finished = Signal(int, str)
                error = Signal(str)
                detected = Signal(dict)

            self._s = _S()
            self.progress, self.finished, self.error, self.detected = (
                self._s.progress,
                self._s.finished,
                self._s.error,
                self._s.detected,
            )

        def start(self):
            pass

    monkeypatch.setattr("ui.library_hotcues.HotcueWorker", _Worker)
    return started


def test_inside_traktor_hotcues_are_detected_for_a_live_write(
    library_tab, monkeypatch, tmp_path
) -> None:
    """No 'quit Traktor' step and no collection.nml at all: the candidates come from Traktor's
    memory and the cues go into Traktor itself (User, 2026-11-24)."""
    import ui.library as mod

    track = tmp_path / "a.mp3"
    track.write_bytes(b"x")

    def no_nml():
        raise AssertionError("collection.nml looked up inside Traktor")

    monkeypatch.setattr(mod, "find_traktor_nml", no_nml)
    monkeypatch.setattr(
        "ui.library_hotcues.hotcue_eligibility",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("collection.nml parsed")),
    )
    monkeypatch.setenv("TK_EMBED", "/repo/traktor-native/re/embed_tk.py")
    asked: list[str] = []
    monkeypatch.setattr(library_tab, "_confirm_traktor_closed", lambda a: asked.append(a) or True)
    monkeypatch.setattr("core.collection_source.collection_cues", lambda nml: ["one track"])
    monkeypatch.setattr(
        "core.cue_points.eligibility",
        lambda tracks, include_existing=False: ([(str(track), 128.0, 10.0)], {}),
    )
    started = _stub_hotcue_worker(monkeypatch)
    assert library_tab.recompute_hotcues_for(str(track)) is True
    assert asked == [] and started[0]["nml_write"] is False and started[0]["nml"] == ""


def test_recompute_hotcues_needs_a_grid(library_tab, monkeypatch, tmp_path) -> None:
    """A track Traktor never analysed has no beat grid to detect against."""
    import ui.library as mod

    nml = tmp_path / "collection.nml"
    nml.write_text("<NML/>")
    monkeypatch.setattr(mod, "find_traktor_nml", lambda: nml)
    monkeypatch.setattr(library_tab, "_confirm_traktor_closed", lambda action: True)
    monkeypatch.delenv("TK_EMBED", raising=False)
    monkeypatch.setattr(
        "ui.library_hotcues.hotcue_eligibility", lambda p, include_existing=False: ([], {})
    )
    shown: list[str] = []
    monkeypatch.setattr("ui.dialogs.warn", lambda parent, title, text: shown.append(title))
    assert library_tab.recompute_hotcues_for("/nowhere.mp3") is False
    assert shown


def test_inside_traktor_a_collection_write_is_refused(library_tab, monkeypatch) -> None:
    """TK runs in Traktor's process: Traktor can't be closed for the write, and a write while it
    runs is lost when it quits (2026-11-24, hotcues run inside Traktor)."""
    monkeypatch.setenv("TK_EMBED", "/repo/traktor-native/re/embed_tk.py")
    asked: list[str] = []
    monkeypatch.setattr(
        "ui.traktor_guard.wait_for_traktor_closed",
        lambda *a, **k: asked.append("closed") or (True, False),
    )
    shown: list[str] = []
    monkeypatch.setattr("ui.dialogs.warn", lambda parent, title, text: shown.append(title))
    assert library_tab._confirm_traktor_closed("Writing hotcues") is False
    assert asked == []  # Traktor (and TK with it) is never asked to close
    assert shown == ["Writing hotcues: not inside Traktor"]


def test_outside_traktor_the_write_waits_for_traktor_to_close(library_tab, monkeypatch) -> None:
    monkeypatch.delenv("TK_EMBED", raising=False)
    monkeypatch.setattr("ui.traktor_guard.wait_for_traktor_closed", lambda *a, **k: (True, False))
    assert library_tab._confirm_traktor_closed("Writing hotcues") is True


# ── Beat-grid fixes queued during a mix ─────────────────────────────────────


def _queue(tmp_path, *phases):
    from core.grid_fix import GridFix, GridQueue

    q = GridQueue(tmp_path / "fixes.json")
    for i, ph in enumerate(phases):
        q.add(
            GridFix(
                path=f"/m/{i}.mp3",
                deck="A",
                phase=ph,
                bpm=130.0,
                title=f"T{i}",
                artist="Ar",
                captured_at="2026-09-22T14:00",
            )
        )
    return q


def test_the_grid_group_lists_what_is_queued(library_tab, monkeypatch, tmp_path) -> None:
    import ui.library_grid_fixes as mod

    monkeypatch.setattr(mod, "grid_queue_path", lambda: tmp_path / "fixes.json")
    _queue(tmp_path, 0.27, -0.1)
    library_tab.refresh_grid_fixes()
    assert library_tab.grid_list.count() == 2
    assert "+125" in library_tab.grid_list.item(0).text()
    assert "T0" in library_tab.grid_list.item(0).text()


def test_an_empty_queue_says_so(library_tab, monkeypatch, tmp_path) -> None:
    import ui.library_grid_fixes as mod

    monkeypatch.setattr(mod, "grid_queue_path", lambda: tmp_path / "fixes.json")
    library_tab.refresh_grid_fixes()
    assert library_tab.grid_list.count() == 0
    assert not library_tab.grid_apply_btn.isEnabled()


def test_only_what_was_written_leaves_the_queue(library_tab, monkeypatch, tmp_path) -> None:
    """A fix that could not be applied — the track is not in the collection
    yet, say — stays queued for the next try."""
    import ui.library as mod
    from core.grid_fix import GridQueue

    monkeypatch.setattr("ui.library_grid_fixes.grid_queue_path", lambda: tmp_path / "fixes.json")
    nml = tmp_path / "collection.nml"
    nml.write_text("<NML/>")
    monkeypatch.setattr(mod, "find_traktor_nml", lambda: nml)
    monkeypatch.setattr(library_tab, "_confirm_traktor_closed", lambda action: True)
    monkeypatch.setattr("ui.dialogs.confirm", lambda *a, **k: True)
    monkeypatch.setattr("ui.dialogs.info", lambda *a, **k: None)
    monkeypatch.setattr(
        "ui.library_grid_fixes.apply_grid_fixes",
        lambda path, fixes: (1, ["1.mp3: not in the collection"]),
    )
    _queue(tmp_path, 0.27, -0.1)
    library_tab.refresh_grid_fixes()
    library_tab._apply_grid_fixes()
    assert [f.path for f in GridQueue(tmp_path / "fixes.json").entries()] == ["/m/1.mp3"]
    assert library_tab.grid_list.count() == 1


def test_applying_one_leaves_the_others(library_tab, monkeypatch, tmp_path) -> None:
    import ui.library as mod
    from core.grid_fix import GridQueue

    monkeypatch.setattr("ui.library_grid_fixes.grid_queue_path", lambda: tmp_path / "fixes.json")
    nml = tmp_path / "collection.nml"
    nml.write_text("<NML/>")
    monkeypatch.setattr(mod, "find_traktor_nml", lambda: nml)
    monkeypatch.setattr(library_tab, "_confirm_traktor_closed", lambda action: True)
    monkeypatch.setattr("ui.dialogs.confirm", lambda *a, **k: True)
    monkeypatch.setattr("ui.dialogs.info", lambda *a, **k: None)
    sent = []

    def _apply(path, fixes):
        sent.append([f.path for f in fixes])
        return len(fixes), []

    monkeypatch.setattr("ui.library_grid_fixes.apply_grid_fixes", _apply)
    _queue(tmp_path, 0.27, -0.1, 0.05)
    library_tab.refresh_grid_fixes()
    library_tab.grid_list.setCurrentRow(1)
    library_tab._apply_selected_grid_fix()
    assert sent == [["/m/1.mp3"]]
    assert [f.path for f in GridQueue(tmp_path / "fixes.json").entries()] == [
        "/m/0.mp3",
        "/m/2.mp3",
    ]
    assert library_tab.grid_list.count() == 2


def test_applying_one_needs_a_selection(library_tab, monkeypatch, tmp_path) -> None:
    import ui.library_grid_fixes as mod

    monkeypatch.setattr(mod, "grid_queue_path", lambda: tmp_path / "fixes.json")
    _queue(tmp_path, 0.27)
    library_tab.refresh_grid_fixes()
    library_tab.grid_list.setCurrentRow(-1)
    assert not library_tab.grid_apply_one_btn.isEnabled()


def test_selecting_a_row_arms_the_single_apply(library_tab, monkeypatch, tmp_path) -> None:
    import ui.library_grid_fixes as mod

    monkeypatch.setattr(mod, "grid_queue_path", lambda: tmp_path / "fixes.json")
    _queue(tmp_path, 0.27, -0.1)
    library_tab.refresh_grid_fixes()
    library_tab.grid_list.setCurrentRow(0)
    assert library_tab.grid_apply_one_btn.isEnabled()
    assert library_tab.grid_drop_btn.isEnabled()


def test_applying_writes_the_nml_and_empties_the_queue(library_tab, monkeypatch, tmp_path) -> None:
    import ui.library as mod

    monkeypatch.setattr("ui.library_grid_fixes.grid_queue_path", lambda: tmp_path / "fixes.json")
    nml = tmp_path / "collection.nml"
    nml.write_text("<NML/>")
    monkeypatch.setattr(mod, "find_traktor_nml", lambda: nml)
    monkeypatch.setattr(library_tab, "_confirm_traktor_closed", lambda action: True)
    monkeypatch.setattr("ui.dialogs.confirm", lambda *a, **k: True)
    monkeypatch.setattr("ui.dialogs.info", lambda *a, **k: None)
    called = {}

    def _apply(path, fixes):
        called["n"] = len(fixes)
        return 2, []

    monkeypatch.setattr("ui.library_grid_fixes.apply_grid_fixes", _apply)
    _queue(tmp_path, 0.27, -0.1)
    library_tab.refresh_grid_fixes()
    library_tab._apply_grid_fixes()
    assert called["n"] == 2
    assert library_tab.grid_list.count() == 0


def test_applying_is_refused_while_traktor_runs(library_tab, monkeypatch, tmp_path) -> None:
    """Traktor rewrites collection.nml from memory when it quits."""
    import ui.library as mod

    monkeypatch.setattr("ui.library_grid_fixes.grid_queue_path", lambda: tmp_path / "fixes.json")
    nml = tmp_path / "collection.nml"
    nml.write_text("<NML/>")
    monkeypatch.setattr(mod, "find_traktor_nml", lambda: nml)
    monkeypatch.setattr(library_tab, "_confirm_traktor_closed", lambda action: False)
    # The confirmation comes first now; the Traktor check is the last step
    # before writing, so Traktor quit for it is started again after.
    monkeypatch.setattr("ui.dialogs.confirm", lambda *a, **k: True)
    touched = []
    monkeypatch.setattr(
        "ui.library_grid_fixes.apply_grid_fixes", lambda *a: touched.append(1) or (0, [])
    )
    _queue(tmp_path, 0.27)
    library_tab.refresh_grid_fixes()
    library_tab._apply_grid_fixes()
    assert touched == []
    assert library_tab.grid_list.count() == 1


def test_a_queued_fix_can_be_dropped(library_tab, monkeypatch, tmp_path) -> None:
    import ui.library_grid_fixes as mod

    monkeypatch.setattr(mod, "grid_queue_path", lambda: tmp_path / "fixes.json")
    _queue(tmp_path, 0.27, -0.1)
    library_tab.refresh_grid_fixes()
    library_tab.grid_list.setCurrentRow(0)
    library_tab._drop_grid_fix()
    assert library_tab.grid_list.count() == 1
    assert "T1" in library_tab.grid_list.item(0).text()


def test_the_grid_list_is_re_read_when_the_window_is_shown(
    library_tab, monkeypatch, tmp_path
) -> None:
    """The capture happens in the NOW PLAYING panel, often with the Library
    window closed — and it survives a restart. Either way the list has to be
    read again, not kept from construction."""
    import ui.library_grid_fixes as mod

    monkeypatch.setattr(mod, "grid_queue_path", lambda: tmp_path / "fixes.json")
    library_tab.refresh_grid_fixes()
    assert library_tab.grid_list.count() == 0

    _queue(tmp_path, 0.27)
    library_tab.showEvent(_show_event())
    assert library_tab.grid_list.count() == 1


def _show_event():
    from PySide6.QtGui import QShowEvent

    return QShowEvent()


def test_a_capture_reaches_an_open_window(library_tab, monkeypatch, tmp_path) -> None:
    import ui.library_grid_fixes as mod

    monkeypatch.setattr(mod, "grid_queue_path", lambda: tmp_path / "fixes.json")
    library_tab.refresh_grid_fixes()
    _queue(tmp_path, -0.5)
    library_tab.grid_fix_captured()  # what NowPlaying emits
    assert library_tab.grid_list.count() == 1


def test_every_tab_scrolls_vertically_when_needed(library_tab) -> None:
    """Opened inside the docked window, a tab can be taller than the space."""
    tw = library_tab._tab_widget
    for i in range(tw.count()):
        area = tw.widget(i)
        assert isinstance(area, QScrollArea), tw.tabText(i)
        assert area.widgetResizable()
        assert area.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
        assert area.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff


# ── Discogs Review: never an empty window ────────────────────────────────────


def test_review_with_only_implausible_matches_says_so_instead_of_an_empty_window(
    library_tab, monkeypatch
) -> None:
    """The button said "Review 4…" and the dialog opened empty: it pruned all four on opening
    (User, 2026-11-24). Pruned first now; nothing left = a note, no dialog."""
    monkeypatch.setattr("core.discogs_lookup.prune_reviews", lambda: 4)
    monkeypatch.setattr("core.discogs_store.counts", lambda: {"matched": 3, "none": 4})
    monkeypatch.setattr("core.discogs_store.review_paths", lambda: set())
    opened: list[bool] = []
    monkeypatch.setattr(
        "ui.discogs_dialogs.DiscogsReviewDialog", lambda *a, **k: opened.append(True)
    )
    said: list[str] = []
    monkeypatch.setattr("ui.dialogs.info", lambda parent, title, text: said.append(text))
    library_tab._review_discogs()
    assert opened == [] and "4 doubtful" in said[0]
    assert library_tab.discogs_review_btn.text() == "No matches to review"
    assert not library_tab.discogs_review_btn.isEnabled()


def test_review_opens_for_spotify_guesses_alone(library_tab, monkeypatch) -> None:
    """Only Spotify's unsure guesses wait (Discogs has no doubtful match): the button counts
    them, so the click must open the review instead of saying there is nothing."""
    monkeypatch.setattr("core.discogs_lookup.prune_reviews", lambda: 0)
    monkeypatch.setattr("core.discogs_store.counts", lambda: {"none": 2})
    monkeypatch.setattr("core.discogs_store.review_paths", lambda: {"/m/a.mp3", "/m/b.mp3"})
    opened: list[bool] = []

    class _Dialog:
        def __init__(self, *a, **k) -> None:
            opened.append(True)

        def exec(self) -> int:
            return 0

    monkeypatch.setattr("ui.discogs_dialogs.DiscogsReviewDialog", _Dialog)
    monkeypatch.setattr("ui.dialogs.info", lambda *a: None)
    library_tab._review_discogs()
    assert opened == [True]


def test_the_summary_tells_spotify_finds_from_discogs_misses() -> None:
    from ui.library_discogs import _summary

    text = _summary({"spotify": 2, "none": 3})
    assert "2 found on Spotify" in text and "3 not on Discogs" in text


def test_the_review_button_never_looks_cut_short() -> None:
    """No trailing ellipsis (it read as a truncated label), singular and plural (User, 2026-11-24)."""
    from ui.library_discogs import review_label

    assert review_label(0) == "No matches to review"
    assert review_label(1) == "Review 1 match"
    assert review_label(4) == "Review 4 matches"
    assert not any(review_label(n).endswith(("…", "...")) for n in (0, 1, 4, 999))
