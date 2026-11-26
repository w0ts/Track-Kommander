"""Library tab, export: which playlists go out and where they are written.

Holds the playlist list read from ``collection.nml``, the ticks remembered
between runs, the turning of a selection into tracks, and the destination
drive. Mixed into :class:`ui.library.LibraryTab`, which owns the widgets and
settings these methods read through ``self``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import QFileDialog, QListWidgetItem

from ui.library_helpers import _read_export_collection, _removable_volumes, _tick

log = logging.getLogger(__name__)


class _ExportSelectionMixin:
    # ── Source selection ────────────────────────────────────────────────────

    def reload_playlists(self) -> None:
        """Fill the source combo from the NML's playlist tree."""
        self._reload_playlists_from(self._nml())

    def _reload_playlists_from(self, nml: str) -> None:
        # Signals stay blocked while the list is refilled (see
        # _on_rb_playlist_toggled), and are unblocked however this ends — a
        # list left blocked would stop remembering ticks for the session.
        with QSignalBlocker(self.rb_playlists):
            note = self._fill_playlists(nml)
        self.rb_source_note.setText(note)
        self._on_rb_source_changed(0)

    def _fill_playlists(self, nml: str) -> str:
        """Refill the playlist list from *nml*; the note to show under it."""
        from core import playlists
        from core.runtime import in_traktor
        from core.smartlist import describe

        remembered = set(self._remembered_playlists())
        self.rb_playlists.clear()
        self._playlists = []
        note = ""
        # Inside Traktor the tree comes from its memory, whatever the file says.
        if in_traktor() or (nml and Path(nml).is_file()):
            try:
                # Cached per collection version and shared: read-only here.
                self._playlists = playlists.parse_playlists(Path(nml or "."))
            except RuntimeError as exc:
                note = str(exc)
            unsupported = 0
            for pl in self._playlists:
                if pl.smart:
                    # A saved search stores no entries, so it is resolved
                    # against the collection when the export runs. One whose
                    # query we cannot evaluate exactly is shown greyed out
                    # with the reason rather than silently exported empty.
                    why = describe(pl.query)
                    item = QListWidgetItem(f"{pl.display}   (smart)")
                    if why:
                        unsupported += 1
                        item.setFlags(Qt.ItemFlag.NoItemFlags)
                        item.setToolTip(f"Cannot be exported: {why}")
                    else:
                        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                        item.setCheckState(_tick(pl.display in remembered))
                        item.setToolTip(pl.query)
                    item.setData(Qt.ItemDataRole.UserRole, pl.display)
                    self.rb_playlists.addItem(item)
                    continue
                if not len(pl):
                    continue  # empty playlist — nothing to export
                item = QListWidgetItem(f"{pl.display}   ({len(pl)} tracks)")
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(_tick(pl.display in remembered))
                item.setData(Qt.ItemDataRole.UserRole, pl.display)
                self.rb_playlists.addItem(item)
            if unsupported and not note:
                note = (
                    f"{unsupported} smart playlist(s) cannot be exported — "
                    "hover them for the reason. The rest are resolved "
                    "against the collection when you export."
                )
        return note

    def _on_rb_source_changed(self, _idx: int) -> None:
        chosen = self._selected_playlists()
        total = sum(len(p) for p in chosen)
        self.rb_status.setText(
            f"{len(chosen)} playlist(s) · {total} entries"
            if chosen
            else "Nothing ticked — the whole collection will be exported."
        )

    def _on_rb_playlist_toggled(self, _item) -> None:
        """A tick changed by hand: remember it, then refresh the summary.

        Only user changes are saved. ``reload_playlists`` keeps its signals
        blocked so that re-reading a missing or empty collection does not
        overwrite the remembered selection with nothing.
        """
        self._settings.setValue(self._PLAYLISTS_KEY, self.rb_playlists.checked_data())
        self._on_rb_source_changed(0)

    def _remembered_playlists(self) -> list[str]:
        """The playlists ticked last time, by display name."""
        value = self._settings.value(self._PLAYLISTS_KEY, [])
        # QSettings hands a one-element list back as a bare string.
        if isinstance(value, str):
            return [value] if value else []
        return [str(v) for v in value or []]

    def _check_all_playlists(self, checked: bool) -> None:
        self.rb_playlists.set_all_checked(checked)

    def _selected_playlists(self) -> list:
        """The ticked playlists, in the order shown."""
        by_key = {p.display: p for p in self._playlists}
        return [by_key[k] for k in self.rb_playlists.checked_data() if k in by_key]

    # ── Building the library ────────────────────────────────────────────────

    def _collect_rb_tracks(self, nml: str):
        """(tracks, playlists, skipped) for the current selection of *nml*.

        *playlists* maps a name to indices into *tracks*. A track appearing in
        several playlists is exported once and referenced from each.
        """
        from core.playlists import cached_by_file
        from core.runtime import in_traktor
        from core.smartlist import UnsupportedQuery, evaluate

        if in_traktor():
            # Traktor's memory, never collection.nml (User, 2026-11-24): always current.
            from core.collection_source import collection_cues, collection_metadata
            from core.native_export import export_collection

            by_path, order, skipped = export_collection(
                collection_metadata(None), collection_cues(None)
            )
        else:
            # Parsed once per collection version: a second export of an unchanged
            # collection does not read it again. Shared, so read-only here.
            by_path, order, skipped = cached_by_file(
                "export_collection", nml, lambda: _read_export_collection(nml)
            )

        chosen = self._selected_playlists()
        if not chosen:
            return [by_path[p] for p in order], {}, skipped

        collection = [by_path[p] for p in order]
        tracks, index_of, playlists, missing = [], {}, {}, 0
        for pl in chosen:
            if pl.smart:
                # No stored entries — run the saved search. Some queries
                # cannot be evaluated outside Traktor at all: ``$PLAYED``
                # means "played in this session", which is runtime state we
                # do not have. Such a playlist is skipped, not exported
                # wrong and not allowed to take the whole export down with
                # it — one unexportable playlist must not cost the user the
                # other twenty.
                try:
                    member_paths = [t.path for t in evaluate(pl.query, collection)]
                except UnsupportedQuery as exc:
                    log.warning("export: skipping smart playlist %r — %s", pl.name, exc)
                    continue
            else:
                member_paths = pl.paths
            members = []
            for path in member_paths:
                track = by_path.get(path)
                if track is None:
                    missing += 1
                    continue
                if path not in index_of:
                    index_of[path] = len(tracks)
                    tracks.append(track)
                members.append(index_of[path])
            if members:
                playlists[pl.key] = members
        return tracks, playlists, skipped + missing

    # ── Export destination ──────────────────────────────────────────────────

    _DEST_KEY = "usb_export_path"

    def refresh_export_destinations(self) -> None:
        """Rebuild the destination list: mounted drives plus the remembered
        folder, which is preselected when it still exists.

        Not while an export runs: the destination cannot change under it,
        and touching the stick from the main thread is what stalls the UI.
        """
        if getattr(self, "_export_worker", None) is not None:
            return
        want = self._settings.value(self._DEST_KEY, "", type=str)
        entries = list(_removable_volumes())
        if want and Path(want).is_dir() and want not in {r for _n, r in entries}:
            entries.append((Path(want).name or want, want))
        roots = [r for _n, r in entries]
        # Never guess: a drive that is merely plugged in is not a choice the
        # user made, and "Replace" deletes. Without the remembered one, the
        # selection is a placeholder and the export asks.
        if want not in roots:
            entries.insert(0, ("Choose a drive…", ""))
            roots.insert(0, "")
            want = ""
        current = [self.rb_dest.itemData(i) for i in range(self.rb_dest.count())]
        if current == roots and self.rb_dest.currentData() == want:
            return
        # Unblocked however this ends, or the combo would stop remembering
        # the user's choice.
        with QSignalBlocker(self.rb_dest):
            self.rb_dest.clear()
            for name, root in entries:
                label = name if name == root or not root else f"{name}   ({root})"
                self.rb_dest.addItem(label, root)
            self.rb_dest.setCurrentIndex(max(0, self.rb_dest.findData(want)))

    def export_destination(self) -> Path | None:
        """The chosen destination, or None when there is no usable one."""
        root = self.rb_dest.currentData()
        if not root or not Path(root).is_dir():
            return None
        return Path(root)

    def set_export_destination(self, path: Path) -> None:
        self._settings.setValue(self._DEST_KEY, str(path))
        self._settings.sync()
        self.refresh_export_destinations()

    def _on_rb_dest_changed(self, _idx: int) -> None:
        root = self.rb_dest.currentData()
        if root:
            self._settings.setValue(self._DEST_KEY, root)
            self._settings.sync()

    def _choose_export_destination(self) -> None:
        start = self.export_destination()
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose the USB drive", str(start or Path.home())
        )
        if chosen:
            self.set_export_destination(Path(chosen))

    def _pick_export_destination(self) -> Path | None:
        """What the export writes to: the selection, else ask."""
        self.refresh_export_destinations()
        dest = self.export_destination()
        if dest is not None:
            return dest
        chosen = QFileDialog.getExistingDirectory(self, "Choose the USB drive", str(Path.home()))
        if not chosen:
            return None
        self.set_export_destination(Path(chosen))
        return Path(chosen)
