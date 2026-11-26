"""The matching-axis card panel of the Now Playing tab.

One card per axis (value slider, strength slider, sync and active toggles),
laid out in a flow layout above the results. Building and styling the cards
is the bulk of the tab's widget code and only touches the axis widgets and the
settings, so it is a mixin the tab inherits: the cards read and write state
the tab owns (``_axis_widgets``, ``_settings``, the source row), which is why
they are not a separate widget. ``NowPlayingTab`` in ``ui.now_playing``
provides those attributes.
"""

from __future__ import annotations

import textwrap

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
)

from core.features import (
    BY_KEY,
    FEATURES,
    axis_active_key,
    axis_enabled_key,
    axis_strength_key,
    axis_sync_key,
    axis_value_key,
)
from ui.np_format import _set_qss, _set_tip
from ui.style import T


class _AxisCardsMixin:
    """Builds, styles and refreshes the matching-axis cards."""

    # ── Value+strength axis panel ───────────────────────────────────────────

    def set_metric_enabled(self, key: str, enabled: bool) -> None:
        """Enable/disable a dimension as a matching axis. Driven by the stats
        dashboard's "discriminative features" list (see ui.stats). Persists the
        choice, rebuilds the axis panel, and re-ranks."""
        self._settings.setValue(axis_enabled_key(key), enabled)
        self._persist_timer.start()
        self._rebuild_axis_panel()
        self._refresh()

    def _active_axis_specs(self) -> list:
        """Specs currently enabled as matching axes (toggled from the stats
        dashboard's discriminative list). All declared features are computed."""
        return [
            spec
            for spec in FEATURES
            if self._settings.value(axis_enabled_key(spec.key), spec.default_enabled, type=bool)
        ]

    def axis_card_states(self) -> list[tuple[str, str, bool, int, int, int, int, str]]:
        """The cards as shown, for mirroring elsewhere (Traktor's band):
        (key, label, active, strength 0–100, value 0–100 or -1, hue 0–360, src):
        src is the background fill, where the source track sits on the axis
        (0–100), -1 with no source or the axis off — as this card fills its own;
        desc is the axis's description, for the band's tooltips."""
        out = []
        for key, refs in self._axis_widgets.items():
            spec = refs["spec"]
            val = refs.get("val")
            active = bool(refs.get("active", True))
            frac = self._source_axis_value(key)
            src = round(max(0.0, min(1.0, frac)) * 100) if frac is not None and active else -1
            out.append(
                (
                    key,
                    refs["label"].text(),
                    active,
                    int(refs["strength"].value()),
                    int(val.value()) if val is not None else -1,
                    int(spec.hue),
                    src,
                    spec.desc or "",
                )
            )
        return out

    def _rebuild_axis_panel(self) -> None:
        while self._axis_flow.count():
            item = self._axis_flow.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._axis_widgets = {}
        specs = self._active_axis_specs()
        if not specs:
            lbl = QLabel("No matching algorithms enabled — see Settings ▸ Matching Algorithms.")
            lbl.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 11px;")
            lbl.setWordWrap(True)
            self._axis_flow.addWidget(lbl)
            return
        for spec in specs:
            self._axis_flow.addWidget(self._build_axis_card(spec))

    _CARD_MIN_W = 130
    # Compact card metrics: the header label and each slider get a pinned
    # height. 16 px still fits the 12 px bold label and both slider handles
    # (value handle: 15 px tall; strength handle: 8 px groove ± 3 px margins)
    # while roughly halving the card's natural height vs the native hints.
    _CARD_HEADER_H = 16
    _CARD_SLIDER_H = 16

    def _axis_active(self, key: str) -> bool:
        refs = self._axis_widgets.get(key)
        return bool(refs.get("active", True)) if refs else False

    def _axis_sync(self, key: str) -> bool:
        refs = self._axis_widgets.get(key)
        return bool(refs.get("sync", False)) if refs else False

    def _build_axis_card(self, spec) -> QFrame:
        """One framed dimension: a one-word on/off label + sync icon, over a
        value slider (a position marker) and a strength slider (a weight bar).
        The card's background fills to show the current track's value on this axis.
        """
        card = QFrame()
        card.setObjectName("axisCard")
        # Width is driven by the flow layout (stretches to fill the row, no
        # upper cap); only pin the minimum here.
        card.setMinimumWidth(self._CARD_MIN_W)
        v = QVBoxLayout(card)
        v.setContentsMargins(8, 4, 8, 5)
        v.setSpacing(2)

        # Header: one-word label (on/off) + sync icon (scalar axes only).
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(4)
        label = QPushButton(spec.short_label)
        label.setCursor(Qt.CursorShape.PointingHandCursor)
        label.setFlat(True)
        # A flat QPushButton's native sizeHint is ~28 px on macOS — the single
        # biggest contributor to card height. The 12 px label needs far less.
        label.setFixedHeight(self._CARD_HEADER_H)
        # Tooltips are set in _style_axis_row, where the state they describe
        # is known; the cards are the main control surface and nothing else
        # documents them.
        if spec.key == "key":
            label.setText(self._key_mode().label)
            label.clicked.connect(lambda _: self._cycle_key_mode())
        else:
            label.clicked.connect(lambda _, k=spec.key: self._toggle_axis_active(k))
        head.addWidget(label, stretch=1)

        refs: dict = {"spec": spec, "label": label, "card": card}
        refs["active"] = self._settings.value(axis_active_key(spec.key), True, type=bool)
        refs["sync"] = (
            False
            if spec.relational
            else self._settings.value(axis_sync_key(spec.key), spec.default_sync, type=bool)
        )

        if not spec.relational:
            sync_btn = QPushButton("⟲")
            sync_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            sync_btn.setFlat(True)
            sync_btn.setFixedWidth(20)
            # Pin to the same header height as the label. (Its *sizeHint* is
            # the native ~24 px button hint even after setFixedHeight, so using
            # that grew the header — and pushed the sliders of every card with
            # a sync button below those without one.)
            sync_btn.setFixedHeight(self._CARD_HEADER_H)
            sync_btn.clicked.connect(lambda _, k=spec.key: self._toggle_axis_sync(k))
            refs["sync_btn"] = sync_btn
            head.addWidget(sync_btn)
        v.addLayout(head)

        # Restore the saved value/strength BEFORE wiring valueChanged, so
        # rebuilding the panel doesn't fire the change handlers (which would
        # re-save, drop the sync lock, and trigger premature re-ranks).
        if not spec.relational:
            val = QSlider(Qt.Orientation.Horizontal)
            val.setRange(0, 100)
            val.setFixedHeight(self._CARD_SLIDER_H)
            val.setValue(int(self._settings.value(axis_value_key(spec.key), spec.default_value)))
            val.valueChanged.connect(lambda x, k=spec.key: self._on_axis_value_changed(k, x))
            refs["val"] = val
            v.addWidget(val)
        else:
            # Relational axes have no value slider, but the row is reserved so
            # their strength slider still lands on the lower line with everyone
            # else. The placeholder is a *hidden slider* rather than a plain
            # spacer: styles give a slider a layout rect smaller than the
            # widget (9 px inside 16 on macOS), so only an identical widget
            # reserves an identical row on every platform.
            gap = QSlider(Qt.Orientation.Horizontal)
            gap.setFixedHeight(self._CARD_SLIDER_H)
            gap.setStyleSheet(self._value_slider_qss("transparent"))
            policy = gap.sizePolicy()
            policy.setRetainSizeWhenHidden(True)
            gap.setSizePolicy(policy)
            gap.hide()
            v.addWidget(gap)

        strength = QSlider(Qt.Orientation.Horizontal)
        strength.setRange(0, 100)
        strength.setFixedHeight(self._CARD_SLIDER_H)
        strength.setValue(
            int(self._settings.value(axis_strength_key(spec.key), self._default_strength(spec)))
        )
        strength.valueChanged.connect(lambda x, k=spec.key: self._on_axis_strength_changed(k, x))
        refs["strength"] = strength
        v.addWidget(strength)
        # Keep the label + sliders pinned to the top; when the card is stretched
        # taller (to fill the panel height) the slack falls below as clean
        # coloured fill rather than spreading the controls apart.
        v.addStretch(1)

        # Strength 0 always reads as off, regardless of the saved active flag.
        refs["active"] = refs["active"] and strength.value() > 0

        self._axis_widgets[spec.key] = refs
        if refs["sync"]:
            self._sync_axis_value_from_source(spec.key)
        self._style_axis_row(spec.key)  # also paints the card background
        return card

    @staticmethod
    def _strength_slider_qss(accent: str) -> str:
        """Weight bar: a thick rounded groove (transparent track) + round handle."""
        return (
            "QSlider{background:transparent;}"
            "QSlider::groove:horizontal{height:8px;background:transparent;border-radius:4px;}"
            f"QSlider::sub-page:horizontal{{background:{accent};border-radius:4px;}}"
            "QSlider::add-page:horizontal{background:transparent;}"
            f"QSlider::handle:horizontal{{background:#eef2f8;border:2px solid {accent};"
            "width:10px;margin:-3px 0;border-radius:7px;}"
        )

    @staticmethod
    def _value_slider_qss(line: str) -> str:
        """Position marker: a thin line (transparent track) + tall narrow tick handle."""
        return (
            "QSlider{background:transparent;}"
            "QSlider::groove:horizontal{height:3px;background:transparent;border-radius:1px;}"
            f"QSlider::sub-page:horizontal{{background:{line};border-radius:1px;}}"
            "QSlider::add-page:horizontal{background:transparent;}"
            f"QSlider::handle:horizontal{{background:{line};border:none;"
            "width:3px;height:15px;margin:-6px 0;border-radius:1px;}"
        )

    def _label_qss(self, active: bool) -> str:
        color = T.TEXT if active else "#5d6678"
        return (
            "QPushButton{border:none;background:transparent;text-align:left;padding:0;"
            f"font-size:12px;font-weight:700;color:{color};}}"
        )

    def _sync_btn_qss(self, synced: bool, active: bool) -> str:
        color = (T.ORANGE if synced else "#8893a5") if active else "#3a4150"
        # 14 px: the largest ⟲ that doesn't clip in the compact 16 px header.
        return (
            "QPushButton{border:none;background:transparent;padding:0;"
            f"font-size:14px;font-weight:700;color:{color};}}"
        )

    def _update_card_background(self, key: str) -> None:
        """Fill the card background up to the current track's value on this axis."""
        refs = self._axis_widgets.get(key)
        if not refs:
            return
        spec = refs["spec"]
        base, border = "#171b24", "#2a2f3a"
        frac = self._source_axis_value(key)
        if frac is None or not refs.get("active", True):
            # No source value, or the dimension is off → no coloured fill. An
            # off axis must read as neutral; only its (greyed) sliders show.
            bg = base
        else:
            c = QColor.fromHslF(spec.hue / 360.0, 0.55, 0.34)
            fill = f"rgba({c.red()},{c.green()},{c.blue()},0.38)"
            a = max(0.0, min(1.0, frac))
            b = min(1.0, a + 0.004)
            bg = (
                f"qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                f"stop:0 {fill}, stop:{a:.3f} {fill}, stop:{b:.3f} {base}, stop:1 {base})"
            )
        # Runs for every card on every re-rank, slider-drag ticks included;
        # the fill only moves when the source track does.
        _set_qss(
            refs["card"],
            f"QFrame#axisCard{{border:1px solid {border};border-radius:6px;background:{bg};}}",
        )

    def _style_axis_row(self, key: str) -> None:
        refs = self._axis_widgets.get(key)
        if not refs:
            return
        spec = refs["spec"]
        active = refs.get("active", True)
        # Strength bar saturation tracks the weight; greyed out when the axis is off.
        # Called per slider tick while dragging: restyle only what changed.
        sat = int(refs["strength"].value()) if active else 0
        _set_qss(refs["strength"], self._strength_slider_qss(f"hsl({spec.hue}, {sat}%, 58%)"))
        if "val" in refs:
            line = f"hsl({spec.hue}, 45%, 60%)" if active else "#444c5c"
            _set_qss(refs["val"], self._value_slider_qss(line))
        _set_qss(refs["label"], self._label_qss(active))
        if "sync_btn" in refs:
            _set_qss(refs["sync_btn"], self._sync_btn_qss(refs.get("sync", False), active))
        self._set_axis_tooltips(refs)
        # Keep the card's coloured fill in sync with the on/off state so an
        # axis toggled off loses its bright background gradient too.
        self._update_card_background(key)

    def _set_axis_tooltips(self, refs: dict) -> None:
        """Every control on the card says what it does and what state it is in."""
        spec = refs["spec"]
        active = refs.get("active", True)
        strength = int(refs["strength"].value())
        low, high = spec.poles

        head = f"{spec.label} — {low} ←→ {high}"
        if spec.relational:
            head += "\nCompared against the current track."
        if spec.desc:
            head += "\n" + textwrap.fill(spec.desc, 72)
        state = "on" if active else "off"
        _set_tip(
            refs["label"],
            f"{head}\n\nThis dimension is {state}. Click to turn {'off' if active else 'on'}.",
        )

        _set_tip(
            refs["strength"],
            f"Weight: {strength}% — how much {spec.label} counts in the score.\n"
            "Drag to change; 0 turns the dimension off. The colour in the "
            "results follows it.",
        )

        if "val" in refs:
            synced = refs.get("sync", False)
            _set_tip(
                refs["val"],
                f"Target {spec.label}: tracks close to this score higher.\n"
                + (
                    "Following the current track — drag to hold a value of your own instead."
                    if synced
                    else "Held where you put it; the card's fill shows the current "
                    "track's value. Click ⟲ to follow the track again."
                ),
            )
        if "sync_btn" in refs:
            _set_tip(
                refs["sync_btn"],
                "Following the current track: the target moves with whatever "
                "is loaded. Click to hold the value instead."
                if refs.get("sync", False)
                else "Held at your own value. Click to follow the current track.",
            )

    def _refresh_axis_widget_states(self, key: str) -> None:
        """Restyle a card. Sliders stay interactive in every state — touching
        them is how the user turns a dimension on / un-syncs it."""
        self._style_axis_row(key)

    def _source_axis_value(self, key: str) -> float | None:
        """Normalised [0,1] value of the current source track for a scalar axis."""
        source = getattr(self, "_source", None)
        if source is None:
            return None
        spec = BY_KEY[key]
        if spec.relational or spec.column is None or spec.normalize is None:
            return None
        try:
            raw = source[spec.column]
        except Exception:
            return None
        if raw is None:
            return None
        try:
            return spec.normalize(float(raw))
        except Exception:
            return None

    def _sync_axis_value_from_source(self, key: str) -> None:
        """Pull the current track's value into a synced scalar axis's slider."""
        refs = self._axis_widgets.get(key)
        if not refs or "val" not in refs:
            return
        v = self._source_axis_value(key)
        if v is None:
            return
        iv = int(round(v * 100))
        refs["val"].blockSignals(True)
        refs["val"].setValue(iv)
        refs["val"].blockSignals(False)
        self._settings.setValue(axis_value_key(key), iv)
