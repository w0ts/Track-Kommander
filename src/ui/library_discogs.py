"""Library tab, Discogs: look tracks up, review doubtful matches, fill Traktor.

The lookup runs on a worker thread and only writes our database; filling
``collection.nml`` is a separate step with a preview, behind the same
Traktor-must-be-closed guard as every other collection write. Mixed into
:class:`ui.library.LibraryTab`; the worker is kept as ``self._discogs_worker``
because the main window stops it by that name when the app quits.
"""

from __future__ import annotations

import logging
import sqlite3
import xml.etree.ElementTree as ET
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
)

from core import discogs_store
from core.discogs_fields import EXACT_DATES_KEY, PLACEHOLDER_KEY, REGION_KEY, read_mapping
from core.discogs_match import DEFAULT_REGION
from core.secrets import DISCOGS_TOKEN, get_secret
from workers.discogs_worker import DiscogsWorker, spotify_key_set

log = logging.getLogger(__name__)


def review_label(waiting: int) -> str:
    """The Review button's words. No "…": "Review 4…" read as a label cut short (User,
    2026-11-24, reported four times); the count says what waits."""
    if not waiting:
        return "No matches to review"
    return f"Review {waiting} match" if waiting == 1 else f"Review {waiting} matches"


class _DiscogsMixin:
    def _build_discogs_box(self) -> None:
        self._discogs_worker: DiscogsWorker | None = None
        box = QGroupBox("DISCOGS AND SPOTIFY")
        lay = QVBoxLayout(box)
        desc = QLabel(
            "Look your tracks up on Discogs — genre, style, label, catalog number, "
            "release date — and on Spotify — album, release date, label (no genres; "
            "only with your Spotify key in Settings ▸ Traktor) — and keep what is "
            "found in Track Kommander's library. Confident Discogs matches are kept; "
            "doubtful ones wait for you under Review. Then fill the empty fields in "
            "Traktor with them: which value goes where is set in Settings ▸ Traktor. "
            "The files' own tags and covers are left alone: Library ▸ Spotify's "
            "right-click Update tags writes those."
        )
        desc.setStyleSheet("font-size: 11px; color: #7777aa;")
        desc.setWordWrap(True)
        lay.addWidget(desc)

        ctrl = QHBoxLayout()
        self.discogs_btn = QPushButton("Look Up Tracks")
        self.discogs_btn.setToolTip(
            "Ask Discogs (and Spotify, when its key is set) about every track "
            "Discogs has no answer for yet."
        )
        self.discogs_btn.clicked.connect(self._start_discogs_lookup)
        ctrl.addWidget(self.discogs_btn)
        self.discogs_stop_btn = QPushButton("Stop")
        self.discogs_stop_btn.setEnabled(False)
        self.discogs_stop_btn.clicked.connect(self._stop_discogs_lookup)
        ctrl.addWidget(self.discogs_stop_btn)
        self.discogs_again_cb = QCheckBox("Look up again")
        self.discogs_again_cb.setToolTip(
            "Ask Discogs again about every track, not only the ones without an\n"
            "answer. Releases you picked by hand under Review are kept."
        )
        ctrl.addWidget(self.discogs_again_cb)
        ctrl.addStretch()
        self.discogs_review_btn = QPushButton(review_label(0))
        self.discogs_review_btn.clicked.connect(self._review_discogs)
        # Wide enough for its longest text: its count changes, the width must not move or cut it.
        widest = self.discogs_review_btn.fontMetrics().horizontalAdvance(review_label(999))
        self.discogs_review_btn.setMinimumWidth(widest + 32)
        ctrl.addWidget(self.discogs_review_btn)
        self.discogs_fill_btn = QPushButton("Fill Traktor…")
        self.discogs_fill_btn.setToolTip(
            "Write Discogs' and Spotify's values into the empty fields of collection.nml.\n"
            "Shows what will change first. Traktor must be closed."
        )
        self.discogs_fill_btn.clicked.connect(self._fill_traktor_from_discogs)
        ctrl.addWidget(self.discogs_fill_btn)
        lay.addLayout(ctrl)

        self.discogs_progress = QProgressBar()
        self.discogs_progress.setValue(0)
        lay.addWidget(self.discogs_progress)
        self.discogs_status = QLabel("")
        self.discogs_status.setStyleSheet("font-size: 11px; color: #7777aa;")
        self.discogs_status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        lay.addWidget(self.discogs_status)
        self._add_pinned("Tagging", box)
        self._refresh_discogs_counts()
        # After startup, not during it: the window paints first.
        QTimer.singleShot(8000, self, self.auto_lookup_playlist_tracks)

    # ── counts ───────────────────────────────────────────────────────────────

    def _refresh_discogs_counts(self) -> None:
        try:
            from core.discogs_lookup import prune_reviews

            prune_reviews()  # the count must be what Review will show (implausible ones out)
            counts = discogs_store.counts()
        except Exception as exc:  # the database is the app's; say it, don't crash the tab
            log.warning("discogs counts unavailable: %s", exc)
            return
        # Doubtful on Discogs, plus the tracks only Spotify found but was not sure of.
        try:
            review = len(discogs_store.review_paths())
        except sqlite3.OperationalError:
            review = counts.get("review", 0)
        counts = {**counts, "review": review}
        self.discogs_review_btn.setText(review_label(review))
        self.discogs_review_btn.setEnabled(bool(review))
        # Spotify's data can fill fields too (dates, labels), matched or not.
        self.discogs_fill_btn.setEnabled(True)
        if self._discogs_worker is None:
            self.discogs_status.setText(_summary(counts))

    # ── lookup ───────────────────────────────────────────────────────────────

    def _start_discogs_lookup(self) -> None:
        from ui.dialogs import info

        todo = discogs_store.pending(
            again=self.discogs_again_cb.isChecked(), spotify=spotify_key_set()
        )
        if not todo:
            info(
                self,
                "Nothing to look up",
                "Every track with an artist and a title already has an answer from "
                'Discogs. Tick "Look up again" to ask anyway, or scan the library '
                "first if tracks are missing.",
            )
            return
        self._run_discogs(todo, again=self.discogs_again_cb.isChecked())

    def auto_lookup_playlist_tracks(self) -> None:
        """Look up the Spotify-playlist tracks that have no Discogs answer yet.

        Runs on its own after startup and after each library scan — a track
        the playlist tool placed is in the library once scanned. Syncs what
        Spotify said about them first (core.spotify_store), so the search
        uses Spotify's artist and title. Quiet: a status line, no dialog;
        nothing when a lookup is already running.
        """
        from core import spotify_store

        if self._discogs_worker is not None:
            return
        try:
            spotify_store.sync()
            playlist = set(spotify_store.queries())
            todo = [(p, q) for p, q in discogs_store.pending() if p in playlist]
        except Exception as exc:  # a missing table or job folder: say it, carry on
            log.warning("playlist tracks: not looked up on Discogs (%s)", exc)
            return
        if not todo:
            return
        log.info("discogs: looking up %d new playlist track(s)", len(todo))
        self._run_discogs(todo)
        self.discogs_status.setText(f"Looking up {len(todo)} new playlist track(s) on Discogs…")

    def lookup_discogs_for(self, path: str) -> None:
        """One track, from the results' right-click menu — asked again even
        when it has an answer, a hand-picked one included."""
        from ui.dialogs import info

        if self._discogs_worker is not None:
            info(
                self,
                "Discogs is busy",
                "A Discogs lookup is already running. Try again when it has finished.",
            )
            return
        query = discogs_store.query_for(path)
        if query is None or not query.artist or not query.title:
            info(self, "Nothing to look up", "Discogs needs the track's artist and title.")
            return
        self._discogs_single = path
        self._run_discogs([(path, query)], again=True)

    def lookup_discogs_paths(self, paths: list) -> None:
        """Several tracks, from Library ▸ Spotify's right-click menu: asked
        again even when they have an answer; the review is in this page."""
        from ui.dialogs import info

        queries = ((p, discogs_store.query_for(p)) for p in dict.fromkeys(paths))
        todo = [(p, q) for p, q in queries if q is not None and q.artist and q.title]
        if not todo:
            info(self, "Nothing to look up", "Discogs needs the tracks' artist and title.")
            return
        if self._discogs_worker is not None:  # running: they join it
            self._discogs_worker.add(todo)
            return
        if len(todo) == 1:
            self.lookup_discogs_for(todo[0][0])
            return
        self._run_discogs(todo, again=True)

    def unqueue_discogs_paths(self, paths: list) -> None:
        """Take tracks off the running lookup before their turn."""
        if self._discogs_worker is not None:
            self._discogs_worker.skip(set(paths))

    def _run_discogs(self, todo, again: bool = False) -> None:
        """*again*: the user asked for these tracks once more, so the Spotify
        search tries the ones it missed before too."""
        token = get_secret(DISCOGS_TOKEN)
        exact = bool(self._settings.value(EXACT_DATES_KEY, False, type=bool))
        region = str(self._settings.value(REGION_KEY, DEFAULT_REGION) or "")
        self._discogs_worker = DiscogsWorker(todo, token, exact, region, spotify_again=again)
        self._discogs_worker.progress.connect(self._on_discogs_progress)
        self._discogs_worker.finished.connect(self._on_discogs_finished)
        self._discogs_worker.error.connect(self._on_discogs_error)
        self.discogs_btn.setEnabled(False)
        self.discogs_stop_btn.setEnabled(True)
        self.discogs_progress.setMaximum(len(todo))
        self.discogs_progress.setValue(0)
        rate = "60 a minute (token)" if token else "25 a minute — add a token in Settings for 60"
        self.discogs_status.setText(f"Looking up {len(todo)} tracks, {rate}…")
        self._discogs_worker.start()

    def _stop_discogs_lookup(self) -> None:
        if self._discogs_worker is not None:
            self._discogs_worker.stop()
            self.discogs_stop_btn.setEnabled(False)
            self.discogs_status.setText("Stopping after the current track…")

    def _on_discogs_progress(self, done: int, total: int, what: str) -> None:
        self.discogs_progress.setMaximum(max(total, 1))
        self.discogs_progress.setValue(done)
        if what:
            self.discogs_status.setText(f"{done} / {total}  {what}")

    def _on_discogs_finished(self, outcome: dict) -> None:
        from ui.worker_shutdown import keep_until_finished

        worker, self._discogs_worker = self._discogs_worker, None
        if worker is not None:
            keep_until_finished(worker)
            # Tracks added (from Library ▸ Spotify) as this run was ending.
            late = [] if getattr(worker, "_stop", False) else worker.leftover()
            if late:
                self._run_discogs(late)
                return
        self.discogs_btn.setEnabled(True)
        self.discogs_stop_btn.setEnabled(False)
        log.info("discogs lookup: %s", outcome)
        self._refresh_discogs_counts()
        left = getattr(worker, "spotify_left", 0)
        if left:
            self.discogs_status.setText(
                f"Done. Spotify was asked about its share for this run; {left} more track(s) "
                "wait. Press Look up again later (tomorrow is safest) to ask about them."
            )
        single, self._discogs_single = getattr(self, "_discogs_single", None), None
        if single:
            self._report_single_lookup(single)

    def _report_single_lookup(self, path: str) -> None:
        """Say what one right-click lookup found; offer the review if unsure."""
        from ui.dialogs import confirm, info

        result = discogs_store.get(path)
        query = discogs_store.query_for(path)
        name = f"{query.artist} — {query.title}" if query else Path(path).name
        if result is None:
            return
        if result.status == "matched" and result.record:
            r = result.record
            styles = ", ".join(r.get("styles") or r.get("genres") or [])
            year = r.get("original_year") or r.get("year") or ""
            info(
                self,
                "Found on Discogs",
                f"{name}\n\n{r.get('title', '')} — {r.get('label', '')} {r.get('catno', '')}"
                f"\n{styles}  {year}\n\nFill Traktor from Library ▸ Tagging.",
            )
        elif result.status == "review":
            if confirm(
                self,
                "Not sure which release",
                f"{name}\n\nDiscogs has releases for it, but none is a sure match. Pick one now?",
                yes_text="Review",
            ):
                self._review_discogs()
        elif result.status == "spotify":
            info(
                self,
                "Found on Spotify",
                f"{name}\n\nDiscogs was not sure of a release, but Spotify knows the track: "
                "its label and release date fill Traktor from Library ▸ Tagging.",
            )
        elif result.status == "none":
            info(
                self, "Not on Discogs", f"{name}\n\nDiscogs has nothing for this artist and title."
            )
        else:
            info(self, "Discogs lookup failed", f"{name}\n\n{result.error}")

    def _on_discogs_error(self, message: str) -> None:
        from ui.dialogs import warn

        warn(self, "Discogs", message)

    # ── review ───────────────────────────────────────────────────────────────

    def _review_discogs(self) -> None:
        from core.discogs_lookup import prune_reviews
        from ui.dialogs import info
        from ui.discogs_dialogs import DiscogsReviewDialog

        # The dialog used to prune on opening: a button saying "Review 4…" then opened an empty
        # window when all four were implausible (User, 2026-11-24). Pruned first, and said.
        moved = prune_reviews()
        if not discogs_store.review_paths():
            self._refresh_discogs_counts()
            info(
                self,
                "Nothing to review",
                f"The {moved} doubtful match(es) only had releases by other artists, so those "
                "tracks are marked as not on Discogs."
                if moved
                else "Nothing is waiting for a review.",
            )
            return
        DiscogsReviewDialog(get_secret(DISCOGS_TOKEN), self).exec()
        self._refresh_discogs_counts()

    # ── fill Traktor ─────────────────────────────────────────────────────────

    def _tag_records(self) -> dict:
        """Each track's values from Discogs and Spotify, merged by the rule in
        core.tag_sources (genre/style sources as set in Settings ▸ Traktor)."""
        from core import spotify_store
        from core.tag_sources import DEFAULT_GENRE_SOURCES, GENRE_SOURCES_KEY, merged_records

        try:
            spotify = spotify_store.infos()
        except sqlite3.OperationalError:  # a database from before the table
            spotify = {}
        genre_sources = str(self._settings.value(GENRE_SOURCES_KEY, DEFAULT_GENRE_SOURCES) or "")
        return merged_records(discogs_store.matched_records(), spotify, genre_sources)

    def _fill_traktor_from_discogs(self) -> None:
        from core.discogs_write import apply_fill, plan_fill
        from core.runtime import in_traktor
        from ui.dialogs import info, warn
        from ui.discogs_dialogs import DiscogsFillDialog

        if in_traktor():  # hidden there (hide_collection_fill); a stray call reads no NML either
            return
        nml = self._nml()
        if not nml or not Path(nml).exists():
            warn(self, "No collection", "No collection.nml for the Traktor selected in Settings.")
            return
        replace_placeholders = bool(self._settings.value(PLACEHOLDER_KEY, False, type=bool))
        try:
            plan = plan_fill(
                Path(nml),
                self._tag_records(),
                read_mapping(self._settings),
                replace_placeholders,
            )
        except (OSError, ET.ParseError) as exc:
            log.warning("discogs fill: cannot read %s: %s", nml, exc)
            warn(
                self,
                "Collection unreadable",
                f"collection.nml could not be read — is Traktor writing it?\n\n{exc}",
            )
            return
        if not plan:
            info(
                self,
                "Nothing to fill",
                "The matched tracks have nothing to add: their mapped fields are "
                "already filled in Traktor, or no field is mapped in Settings ▸ Traktor.",
            )
            return
        dialog = DiscogsFillDialog(plan, self)
        if not dialog.exec():
            return
        chosen = dialog.chosen()
        if not chosen or not self._confirm_traktor_closed("fill fields from Discogs"):
            return
        try:
            changed = apply_fill(Path(nml), chosen)
        except Exception as exc:  # reported, and the collection is as it was
            log.exception("discogs fill failed")
            warn(self, "Not written", f"The collection was not changed:\n{exc}")
            return
        finally:
            self._collection_written()
        info(
            self,
            "Filled from Discogs",
            f"{changed} track{'s' if changed != 1 else ''} updated in collection.nml "
            "(a dated backup was kept beside it). Track Kommander's library picks "
            "the new values up on the next scan.",
        )


def _summary(counts: dict[str, int]) -> str:
    if not counts:
        return "No track looked up yet."
    parts = [
        f"{counts[k]} {label}"
        for k, label in (
            ("matched", "matched"),
            ("review", "to review"),
            ("spotify", "found on Spotify"),
            ("none", "not on Discogs"),
            ("skipped", "skipped"),
            ("error", "failed (retried next time)"),
        )
        if counts.get(k)
    ]
    return " · ".join(parts)
