"""Discogs dialogs: review doubtful matches, preview a fill of Traktor.

Review — the tracks a lookup found candidates for but was not sure enough
about. Pick the right release (it is fetched and kept as the match), or say
none of them is the track.

Fill — every field a fill would write, per track, before anything is
written. Untick a track to leave it out.
"""

from __future__ import annotations

import html
import logging
from collections.abc import Sequence

from PySide6.QtCore import Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from core import discogs_store, spotify_store
from core.discogs_api import DiscogsClient, DiscogsError
from core.discogs_lookup import accept, prune_reviews, tracklist
from core.discogs_store import Candidate, Lookup
from core.discogs_write import TrackFill
from core.reveal import reveal_in_file_manager
from core.spotify_api import TrackDetails
from ui.dialogs import install_escape_to_close, warn
from ui.np_constants import _REVEAL_LABEL
from ui.style import T, scroll_page

log = logging.getLogger(__name__)


class _Fetch(QThread):
    """One Discogs call off the GUI thread: Discogs may make it wait out its
    rate limit (up to a minute), and the window must not freeze meanwhile."""

    done = Signal(object, object, str)  # key, result or None, error

    def __init__(self, key: object, call) -> None:
        super().__init__()
        self._key = key
        self._call = call

    def run(self) -> None:
        try:
            self.done.emit(self._key, self._call(), "")
        except DiscogsError as exc:
            self.done.emit(self._key, None, str(exc))
        except Exception as exc:  # reported in the dialog, never swallowed
            log.exception("discogs: %s", self._key)
            self.done.emit(self._key, None, f"unexpected: {exc}")


def _candidate_text(c: Candidate) -> str:
    bits = [c.title]
    if c.year:
        bits.append(str(c.year))
    if c.label:
        bits.append(f"{c.label} {c.catno}".strip())
    if c.format:
        bits.append(c.format)
    if c.country:
        bits.append(c.country)
    return f"{' · '.join(bits)}   ({c.score:.0%})"


def _guess_text(g: TrackDetails) -> str:
    bits = [f"{g.artist} — {g.title}"]
    bits += [b for b in (g.album, g.label, g.release_date) if b]
    return f"Spotify, not sure · {' · '.join(bits)}"


def _minutes(seconds: float | None) -> str:
    if not seconds:
        return ""
    s = int(round(seconds))
    return f"{s // 60}:{s % 60:02d}"


class DiscogsReviewDialog(QDialog):
    def __init__(self, token: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Review Discogs matches")
        self.resize(1000, 680)
        self._client_token = token
        self._client = DiscogsClient(token)
        # Reviews stored before the plausibility rule: drop what is not.
        prune_reviews()
        # Surest first, so the easy ones can be cleared before the hard ones.
        # Spotify found these, but was not sure it is the same recording.
        self._guesses: dict[str, TrackDetails] = spotify_store.unsure_guesses()
        items = discogs_store.by_status("review")
        listed = {lk.path for lk in items}
        for path in self._guesses:
            if path in listed:
                continue
            stored = discogs_store.get(path)
            if stored is not None and stored.status == "matched":
                continue  # Discogs knows it
            items.append(Lookup(path, "review", None, None, ()))
        self._items: list[Lookup] = sorted(items, key=lambda lk: -(lk.confidence or 0.0))
        self._fetch: _Fetch | None = None  # a tracklist for the pane
        self._accept_fetch: _Fetch | None = None  # the release the user picked
        self._accepting = False
        self._details_wanted: int | None = None  # release id the pane should show
        self._releases: dict[int, dict] = {}  # fetched for the pane, by id

        lay = QVBoxLayout(self)
        guesses = (
            " Where Spotify found a track but was not sure of it, its guess is listed "
            "too: use it only if it is the same recording."
            if self._guesses
            else ""
        )
        note = QLabel(
            "These tracks have no sure match. Pick the release the track is on, or say "
            "it is none of them. The tracklist below shows where the track would be on "
            "the selected release." + guesses
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 11px;")
        lay.addWidget(note)

        lists = QHBoxLayout()
        self._tracks = QListWidget()
        self._tracks.currentRowChanged.connect(self._show_track)
        lists.addWidget(self._tracks, stretch=2)
        right = QVBoxLayout()
        track_row = QHBoxLayout()
        self._track_info = QLabel("")
        self._track_info.setWordWrap(True)
        self._track_info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        track_row.addWidget(self._track_info, stretch=1)
        self._reveal_btn = QPushButton(_REVEAL_LABEL)
        self._reveal_btn.clicked.connect(self._reveal_track)
        track_row.addWidget(self._reveal_btn, alignment=Qt.AlignmentFlag.AlignTop)
        right.addLayout(track_row)
        self._candidates = QListWidget()
        self._candidates.itemDoubleClicked.connect(lambda _i: self._use_release())
        self._candidates.currentRowChanged.connect(lambda _r: self._show_candidate())
        right.addWidget(self._candidates, stretch=2)
        self._details = QLabel("")
        self._details.setWordWrap(True)
        self._details.setTextFormat(Qt.TextFormat.RichText)
        self._details.setOpenExternalLinks(True)
        self._details.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._details.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        right.addWidget(scroll_page(self._details), stretch=3)
        lists.addLayout(right, stretch=3)
        lay.addLayout(lists, stretch=1)

        buttons = QHBoxLayout()
        self._use_btn = QPushButton("Use This Release")
        self._use_btn.clicked.connect(self._use_release)
        buttons.addWidget(self._use_btn)
        self._skip_btn = QPushButton("None of These")
        self._skip_btn.clicked.connect(self._skip_track)
        buttons.addWidget(self._skip_btn)
        buttons.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        lay.addLayout(buttons)
        install_escape_to_close(self)

        self._fill_tracks()

    # ── the track ────────────────────────────────────────────────────────────

    def _query(self, path: str):
        return discogs_store.query_for(path)

    def _fill_tracks(self) -> None:
        self._tracks.clear()
        for lookup in self._items:
            q = self._query(lookup.path)
            name = f"{q.artist} — {q.title}" if q else lookup.path
            score = f"{lookup.confidence:.0%}" if lookup.confidence else "—"
            item = QListWidgetItem(f"{score:>4}  {name}")
            item.setToolTip(lookup.path)
            self._tracks.addItem(item)
        if self._items:
            self._tracks.setCurrentRow(0)
        self._update_buttons()

    def _current(self) -> Lookup | None:
        row = self._tracks.currentRow()
        return self._items[row] if 0 <= row < len(self._items) else None

    def _show_track(self, _row: int) -> None:
        lookup = self._current()
        q = self._query(lookup.path) if lookup else None
        if lookup is None or q is None:
            self._track_info.setText("")
        else:
            bits = [b for b in (q.album, _minutes(q.seconds)) if b]
            self._track_info.setText(
                f"<b>{html.escape(q.artist)} — {html.escape(q.title)}</b><br>"
                f"{html.escape(' · '.join(bits))}<br>"
                f"<span style='color:{T.TEXT_DIM}; font-size:11px'>{html.escape(lookup.path)}</span>"
            )
        self._candidates.clear()
        for c in lookup.candidates if lookup else ():
            item = QListWidgetItem(_candidate_text(c))
            item.setData(Qt.ItemDataRole.UserRole, c)
            self._candidates.addItem(item)
        guess = self._guesses.get(lookup.path) if lookup else None
        if guess is not None:
            item = QListWidgetItem(_guess_text(guess))
            item.setData(Qt.ItemDataRole.UserRole, guess)
            self._candidates.addItem(item)
        if self._candidates.count():
            self._candidates.setCurrentRow(0)
        else:
            self._details.setText("No candidates were kept for this track.")
        self._update_buttons()

    def _reveal_track(self) -> None:
        lookup = self._current()
        if lookup is not None:
            reveal_in_file_manager(lookup.path)

    # ── the candidate ────────────────────────────────────────────────────────

    def _chosen(self) -> Candidate | TrackDetails | None:
        item = self._candidates.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _show_candidate(self) -> None:
        self._update_buttons()
        c = self._chosen()
        if c is None:
            self._details.setText("")
            return
        if isinstance(c, TrackDetails):
            self._render_guess(c)
            return
        self._details_wanted = c.release_id
        if c.release_id in self._releases:
            self._render_details(c)
            return
        self._details.setText(self._links(c) + "<p>Fetching the tracklist…</p>")
        self._start_details_fetch()

    def _render_guess(self, g: TrackDetails) -> None:
        lookup = self._current()
        query = self._query(lookup.path) if lookup else None
        ours = _minutes(query.seconds) if query else ""
        theirs = _minutes(g.duration_s)
        rows = [
            ("Track", f"{g.artist} — {g.title}"),
            ("Album", g.album),
            ("Label", g.label),
            ("Released", g.release_date),
            ("Length", f"{theirs} (yours: {ours})" if ours else theirs),
        ]
        body = "<br>".join(f"<b>{k}</b> &nbsp; {html.escape(v)}" for k, v in rows if v)
        link = f'<a href="https://open.spotify.com/track/{g.spotify_id}">Track on Spotify ↗</a>'
        self._details.setText(
            f"<p>{link}</p><p>{body}</p>"
            "<p><i>Spotify found this, but the length or the names did not agree "
            "closely enough to be sure. Use it only if it is the same recording.</i></p>"
        )

    def _start_details_fetch(self) -> None:
        wanted = self._details_wanted
        if self._fetch is not None or wanted is None or wanted in self._releases:
            return  # one call at a time; the next starts when this one ends
        self._fetch = _Fetch(("details", wanted), lambda: self._client.release(wanted))
        self._fetch.done.connect(self._on_fetched)
        self._fetch.start()

    def _links(self, c: Candidate) -> str:
        links = [
            f'<a href="https://www.discogs.com/release/{c.release_id}">Release on Discogs ↗</a>'
        ]
        if c.master_id:
            links.append(
                f'<a href="https://www.discogs.com/master/{c.master_id}">'
                "All versions of this record ↗</a>"
            )
        return "<p>" + " &nbsp;·&nbsp; ".join(links) + "</p>"

    def _render_details(self, c: Candidate) -> None:
        lookup = self._current()
        query = self._query(lookup.path) if lookup else None
        release = self._releases.get(c.release_id)
        if release is None or query is None:
            return
        rows = tracklist(query, release)
        ours = _minutes(query.seconds)
        lines = []
        for r in rows:
            who = f"{html.escape(r.artists)} — " if r.artists else ""
            text = f"{html.escape(r.position)} &nbsp; {who}{html.escape(r.title)}"
            if r.duration:
                text += f" &nbsp; <span style='color:{T.TEXT_DIM}'>{html.escape(r.duration)}</span>"
            if r.is_match:
                text = f"<b>▶ {text}</b>" + (f" &nbsp; (yours: {ours})" if ours else "")
            lines.append(text)
        found = any(r.is_match for r in rows)
        verdict = "" if found else "<p><i>The track was not recognised on this release.</i></p>"
        genres = ", ".join([*release.get("styles", []), *release.get("genres", [])][:4])
        head = (
            f"<p><b>{html.escape(str(release.get('title', '')))}</b> — "
            f"{html.escape(str(release.get('released') or release.get('year') or ''))} · "
            f"{html.escape(genres)}</p>"
        )
        self._details.setText(self._links(c) + head + verdict + "<br>".join(lines))

    # ── decisions ────────────────────────────────────────────────────────────

    def _update_buttons(self) -> None:
        has_track = self._current() is not None
        self._use_btn.setEnabled(has_track and self._chosen() is not None and not self._accepting)
        if not self._accepting:
            guess = isinstance(self._chosen(), TrackDetails)
            self._use_btn.setText("Use Spotify's Track" if guess else "Use This Release")
        self._skip_btn.setEnabled(has_track and not self._accepting)
        self._reveal_btn.setEnabled(has_track)

    def _done_with_current(self) -> None:
        """Drop the reviewed track and show the one that takes its place.

        Removing the current row makes Qt announce a row number that is
        only right *before* the removal, then shift the list up without a
        word: the pane showed the track after the one selected. So the
        removal is silent, and the next track is selected and shown here.
        """
        row = self._tracks.currentRow()
        if row < 0:
            return
        del self._items[row]
        self._tracks.blockSignals(True)
        try:
            self._tracks.takeItem(row)
            if self._items:
                self._tracks.setCurrentRow(min(row, len(self._items) - 1))
        finally:
            self._tracks.blockSignals(False)
        if self._items:
            self._show_track(self._tracks.currentRow())
        else:
            self._candidates.clear()
            self._details.setText("All reviewed.")
            self._track_info.setText("")
        self._update_buttons()

    def _use_release(self) -> None:
        lookup, candidate = self._current(), self._chosen()
        if lookup is None or candidate is None or self._accept_fetch is not None:
            return
        if isinstance(candidate, TrackDetails):
            self._use_guess(lookup)
            return
        query = self._query(lookup.path)
        if query is None:
            warn(self, "Track gone", "This track is no longer in the library.")
            return
        path = lookup.path
        # Its own client: the pane's may be mid-call, and a picked release
        # must not wait behind a tracklist the user no longer looks at.
        client = DiscogsClient(self._client_token)
        self._accept_fetch = _Fetch(
            ("accept", path), lambda: accept(client, path, query, candidate)
        )
        self._accept_fetch.done.connect(self._on_fetched)
        self._set_busy(True)
        self._accept_fetch.start()

    def _use_guess(self, lookup: Lookup) -> None:
        """Spotify's guess is the right track: keep it, and drop the track from
        the review (Discogs' candidates, if any, were not it)."""
        spotify_store.accept_guess(lookup.path)
        self._guesses.pop(lookup.path, None)
        if lookup.status == "review" and lookup.candidates:
            discogs_store.skip(lookup.path)
        self._done_with_current()

    def _on_fetched(self, key: object, result: object, error: str) -> None:
        from ui.worker_shutdown import keep_until_finished

        kind, what = key if isinstance(key, tuple) else ("", None)
        if kind == "details":
            keep_until_finished(self._fetch)
            self._fetch = None
        else:
            keep_until_finished(self._accept_fetch)
            self._accept_fetch = None
        if kind == "details":
            if isinstance(result, dict) and isinstance(what, int):
                self._releases[what] = result
                c = self._chosen()
                if isinstance(c, Candidate) and c.release_id == what:
                    self._render_details(c)
            elif error:
                chosen = self._chosen()
                self._details.setText(
                    (self._links(chosen) if isinstance(chosen, Candidate) else "")
                    + f"<p>Could not fetch the tracklist: {html.escape(error)}</p>"
                )
            self._start_details_fetch()  # the user may have moved on meanwhile
            return
        self._set_busy(False)
        if not isinstance(result, Lookup):
            warn(self, "Discogs", f"Could not fetch that release: {error}")
            return
        discogs_store.save(result)
        current = self._current()
        if current is not None and current.path == what:
            self._done_with_current()
        self._start_details_fetch()

    def _set_busy(self, busy: bool) -> None:
        self._accepting = busy
        self._tracks.setEnabled(not busy)
        self._candidates.setEnabled(not busy)
        self._use_btn.setText("Fetching…" if busy else "Use This Release")
        self._update_buttons()

    def done(self, result: int) -> None:
        """Closing while Discogs is being asked lets the call finish."""
        from ui.worker_shutdown import keep_until_finished

        keep_until_finished(self._fetch)
        keep_until_finished(self._accept_fetch)
        super().done(result)

    def _skip_track(self) -> None:
        lookup = self._current()
        if lookup is not None:
            if lookup.path in self._guesses:
                spotify_store.reject_guess(lookup.path)
                del self._guesses[lookup.path]
            if lookup.candidates:
                discogs_store.skip(lookup.path)
            self._done_with_current()


class DiscogsFillDialog(QDialog):
    """Every change a fill would make; ticked tracks are written."""

    def __init__(self, plan: Sequence[TrackFill], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Fill Traktor")
        self.resize(820, 540)
        self._plan = list(plan)

        lay = QVBoxLayout(self)
        n_fields = sum(len(t.changes) for t in plan)
        note = QLabel(
            f"{len(plan)} tracks, {n_fields} empty fields to fill. Untick a track to "
            "leave it as it is. collection.nml is backed up before it is written."
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 11px;")
        lay.addWidget(note)

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Track / field", "Now", "New", "From"])
        self._tree.setColumnWidth(0, 340)
        self._tree.setColumnWidth(1, 160)
        for fill in self._plan:
            top = QTreeWidgetItem([f"{fill.artist} — {fill.title}", "", "", ""])
            top.setFlags(top.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            top.setCheckState(0, Qt.CheckState.Checked)
            top.setToolTip(0, fill.path)
            for change in fill.changes:
                QTreeWidgetItem(
                    top, [change.field.label, change.old or "—", change.new, change.source]
                )
            self._tree.addTopLevelItem(top)
        self._tree.expandAll()
        self._tree.itemDoubleClicked.connect(self._open_release)
        lay.addWidget(self._tree, stretch=1)

        row = QHBoxLayout()
        hint = QLabel("Double-click a track to open its release on Discogs.")
        hint.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 11px;")
        row.addWidget(hint)
        row.addStretch()
        self._reveal_btn = QPushButton(_REVEAL_LABEL)
        self._reveal_btn.clicked.connect(self._reveal)
        row.addWidget(self._reveal_btn)
        lay.addLayout(row)

        box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok = box.button(QDialogButtonBox.StandardButton.Ok)
        ok.setText("Write to Traktor")
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        install_escape_to_close(self)

    def _fill_of(self, item: QTreeWidgetItem | None) -> TrackFill | None:
        while item is not None and item.parent() is not None:
            item = item.parent()  # a field row belongs to its track
        if item is None:
            return None
        row = self._tree.indexOfTopLevelItem(item)
        return self._plan[row] if 0 <= row < len(self._plan) else None

    def _open_release(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        fill = self._fill_of(item)
        if fill is not None and fill.release_id:
            QDesktopServices.openUrl(QUrl(f"https://www.discogs.com/release/{fill.release_id}"))

    def _reveal(self) -> None:
        fill = self._fill_of(self._tree.currentItem())
        if fill is not None:
            reveal_in_file_manager(fill.path)

    def chosen(self) -> list[TrackFill]:
        chosen = []
        for i, fill in enumerate(self._plan):
            item = self._tree.topLevelItem(i)
            if item is not None and item.checkState(0) == Qt.CheckState.Checked:
                chosen.append(fill)
        return chosen
