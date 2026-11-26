"""Matching feature registry — the single source of truth for match axes.

Every dimension the matcher can score on is declared here once. Three layers
read from this registry:

  * compute  — a library scan computes a feature only if its compute *group*
               is enabled (see :data:`COMPUTE_GROUPS`).
  * axis     — the UI builds one control row per enabled feature.
  * score    — :mod:`core.matcher` loops the active axes instead of
               hard-coding party/mood/melodic.

This module is deliberately Qt-free so the matcher (and tests) can import it
without pulling in PySide6. Settings *keys* live here as plain strings; the UI
is responsible for reading/writing them via ``QSettings``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Literal

# ── axis kinds ──────────────────────────────────────────────────────────────
# scalar      — has an absolute target value in [0, 1]; scored by closeness.
# bpm         — relational: closeness of tempo to the source (octave-aware).
# key         — relational: Camelot harmonic distance to the source.
# similarity  — relational: cosine distance of the CLAP embedding to the source.
# genre       — relational: IDF-weighted token overlap of the Traktor genre tag.
# style       — relational: cosine of the CLAP-detected top-k genre vector.
AxisKind = Literal["scalar", "bpm", "key", "similarity", "genre", "style", "label"]

# ── compute groups (what a scan actually pays for) ──────────────────────────
# Toggling individual features never changes scan cost — only toggling a group
# does. "ml" is one CLAP forward pass that produces every mood/voice feature
# *and* the embedding, so similarity rides along with it.
ComputeGroup = Literal["core", "signal", "melodic", "shape", "ml", "embed"]

COMPUTE_GROUPS: dict[str, dict] = {
    "core": {"label": "Core (BPM · Key)", "cost": "free", "locked": True},
    "signal": {"label": "Signal features (librosa)", "cost": "cheap", "locked": False},
    "melodic": {"label": "Melodic (HPSS)", "cost": "heavy", "locked": False},
    # Bar-aligned structure and bassline. Needs the tempo, so it is skipped
    # for a track Traktor has no BPM for.
    "shape": {"label": "Structure · bassline", "cost": "cheap", "locked": False},
    "ml": {"label": "Mood · voice · similarity (CLAP)", "cost": "heavy", "locked": False},
    # Extra similarity embeddings (MERT / PANNs / OpenL3 / Discogs-EffNet). Each
    # is computed when its ONNX artefact is present; see core.embeddings.
    "embed": {"label": "Extra embeddings (ONNX)", "cost": "heavy", "locked": False},
}


def clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else v


def _identity(v: float) -> float:
    return clamp01(v)


def _from_deam(v: float) -> float:
    """DEAM 1–9 scale → [0, 1]."""
    return clamp01((v - 1.0) / 8.0)


def _gain(factor: float) -> Callable[[float], float]:
    """Scale a loosely-bounded signal feature into [0, 1] then clamp."""
    return lambda v: clamp01(v * factor)


#: The release-year and import-date scales: wide enough for any library.
YEAR_SCALE = (1960, 2030)
IMPORT_SCALE = (date(2000, 1, 1).toordinal(), date(2035, 1, 1).toordinal())


def _from_year(v: float) -> float:
    lo, hi = YEAR_SCALE
    return clamp01((v - lo) / (hi - lo))


def _from_import_day(v: float) -> float:
    lo, hi = IMPORT_SCALE
    return clamp01((v - lo) / (hi - lo))


def _year_text(v: float) -> str:
    return f"{v:.0f}"


def _years_text(w: float) -> str:
    return f"{w:.1f} y"


def _day_text(v: float) -> str:
    """A day number (``date.toordinal``) as a date."""
    try:
        return date.fromordinal(int(round(v))).isoformat()
    except (ValueError, OverflowError):
        return f"{v:.0f}"


def _days_text(w: float) -> str:
    if w < 60:
        return f"{w:.0f} d"
    if w < 730:
        return f"{w / 30.44:.0f} mo"
    return f"{w / 365.25:.1f} y"


def _from_rating(v: float) -> float:
    """Star rating 0–5 → [0, 1]."""
    return clamp01(v / 5.0)


@dataclass(frozen=True)
class FeatureSpec:
    """One matchable dimension, declared once."""

    key: str  # stable id — used in settings keys and as axis id
    label: str  # UI label
    kind: AxisKind
    group: str  # compute group id (key of COMPUTE_GROUPS)
    hue: int  # 0–360, drives the UI saturation feedback
    poles: tuple[str, str]  # (low end, high end) descriptions
    cost: str  # "free" | "cheap" | "heavy"
    default_enabled: bool  # shown as an axis out of the box
    desc: str = ""  # extra tooltip line: what the feature is / where it comes from
    column: str | None = None  # tracks-table column (None for bpm/key — derived)
    # Maps the raw stored value into [0, 1] for scalar scoring. None for
    # relational kinds (bpm/key/similarity), which compare against the source
    # directly rather than against an absolute target.
    normalize: Callable[[float], float] | None = None
    locked: bool = False  # core axes can't be disabled
    default_sync: bool = True  # scalar axes: start locked to the current track
    default_value: int = 50  # initial target (0–100) when not synced
    # True when normalize() maps onto a *semantically* meaningful [0,1] (a CLAP
    # probability, a DEAM scale, a rating) — so "all tracks at 0.9" is genuinely
    # low spread. False for features on an arbitrary unit (loosely-bounded signal
    # features behind a hand-tuned gain): there the library stats panel measures
    # spread on the observed range instead, so a real spread isn't hidden by a
    # mis-calibrated gain that saturates every track to one end.
    absolute_scale: bool = True
    short: str = ""  # explicit compact label; falls back to first word of label if empty
    # The column is worked out from other data (core.library_snapshot.DERIVED:
    # the release year, the import day), not stored by a scan — so it owns no
    # computed-feature bit and is never "missing" for the scanner.
    derived: bool = False
    # Scalar axes: the Gaussian width of "close to the target", in normalised
    # units. None: the shared VIBE_SIGMA. A wide-range scale (decades, years
    # of imports) needs a narrow one, or everything scores alike.
    sigma: float | None = None
    # How a raw value, and a width of values (σ), read for a person — the
    # stats page's axes and tooltips. None: a plain number.
    fmt_value: Callable[[float], str] | None = None
    fmt_width: Callable[[float], str] | None = None
    # For kind="similarity" axes: which EmbeddingModel (see core.embeddings) this
    # axis scores against. None for every other kind.
    embedding: str | None = None
    # False hides the feature from the whole app: it's dropped from FEATURES (so
    # it never appears as an axis, never shows in the stats "discriminative"
    # list, and is absent from BY_KEY, hence never scored) while its declaration,
    # backend, and export script stay in the tree. Used to park in-progress axes
    # out of a release without deleting the work — flip back to True to re-expose.
    released: bool = True

    @property
    def relational(self) -> bool:
        """Strength-only axes that compare to the current track (no target value)."""
        return self.kind in (
            "bpm",
            "key",
            "similarity",
            "genre",
            "style",
            "label",
            "year",
            "imported",
        )

    @property
    def short_label(self) -> str:
        """Compact label for rank list, histogram card title, and heatmap axis."""
        if self.short:
            return self.short
        return self.label.replace("/", " ").split()[0]


# ── the registry ────────────────────────────────────────────────────────────
# Order here is the order axes appear in the UI. ``_ALL_FEATURES`` is the full
# declaration; the public ``FEATURES`` below is the released subset. Axes marked
# ``released=False`` are parked (see the field docstring) — code kept, hidden.
_ALL_FEATURES: tuple[FeatureSpec, ...] = (
    # Core — always on, relational, computed from Traktor metadata (free).
    FeatureSpec(
        "bpm",
        "BPM / Tempo",
        "bpm",
        "core",
        150,
        ("slower", "faster"),
        "free",
        True,
        column="bpm",
        locked=True,
        desc="Traktor's BPM, compared with the current track. Full score within a few percent, then falling off. Half and double tempo count too — a 70 BPM track can follow a 140 one.",
    ),
    FeatureSpec(
        "key",
        "Key (harmonic)",
        "key",
        "core",
        150,
        ("distant", "in key"),
        "free",
        True,
        column="camelot",
        locked=True,
        desc="Camelot closeness to the current track. Click the card "
        "to cycle Key → Key-T → off: Key-T also accepts tracks "
        "that land in key once Traktor's Key knob moves, up to "
        "three semitones. A semitone is seven places round the "
        "wheel, because the wheel is the circle of fifths. Each "
        "semitone costs a little, so a track that already fits "
        "still wins, and the column shows the shift.",
    ),
    # Star rating — Traktor metadata (free); a real weighted dimension. Defaults
    # to "prefer top-rated" (target 5★) rather than syncing to the source.
    FeatureSpec(
        "rating",
        "Rating",
        "scalar",
        "core",
        48,
        ("any", "5★"),
        "free",
        True,
        column="rating",
        normalize=_from_rating,
        default_sync=False,
        default_value=100,
        desc="Your star rating in Traktor. Starts set to 5★, so better-rated tracks rank higher; move it to aim elsewhere.",
    ),
    # Traktor genre tag — free-text metadata (free); IDF-weighted token overlap.
    FeatureSpec(
        "genre",
        "Genre",
        "genre",
        "core",
        218,
        ("any genre", "same genre"),
        "free",
        True,
        column="genre",
        desc="The track's genre: Discogs' genres and styles when it has a "
        "Discogs match, else Traktor's genre tag (web addresses and 'Other' "
        "count as no genre). Matched on shared words (rarer ones count for more).",
    ),
    # Label (relational), release year and import date (numbers), from
    # Traktor's metadata and the Discogs match (core.track_meta). Not features
    # a scan computes: the label has no column, the two dates derived ones.
    FeatureSpec(
        "label",
        "Label",
        "label",
        "core",
        300,
        ("other label", "same label"),
        "free",
        False,
        desc="The record label: Traktor's, or the Discogs match's when Traktor "
        "has none. The same label scores full, another none; names are "
        "tidied first ('Planet Rhythm Records' is 'Planet Rhythm'), and "
        "distributors and web addresses are not labels.",
    ),
    FeatureSpec(
        "year",
        "Release Year",
        "scalar",
        "core",
        45,
        ("older", "newer"),
        "free",
        False,
        short="Year",
        column="year_effective",
        derived=True,
        normalize=_from_year,
        fmt_value=_year_text,
        fmt_width=_years_text,
        absolute_scale=False,
        sigma=3.0 / (YEAR_SCALE[1] - YEAR_SCALE[0]),
        desc="When the record came out — Discogs' original year when the "
        "track has a match (not a reissue's), else Traktor's release date. "
        "Follows the current track by default (full score in the same year, "
        "falling off over a few years); set a target for an era.",
    ),
    FeatureSpec(
        "imported",
        "Import Date",
        "scalar",
        "core",
        160,
        ("added long ago", "added recently"),
        "free",
        False,
        short="Imported",
        column="import_day",
        derived=True,
        normalize=_from_import_day,
        fmt_value=_day_text,
        fmt_width=_days_text,
        absolute_scale=False,
        sigma=30.0 / (IMPORT_SCALE[1] - IMPORT_SCALE[0]),
        desc="When the track entered your Traktor collection. Follows the "
        "current track by default — tracks added in the same weeks tend to "
        "belong together, falling off over a month or two; set a target to "
        "favour recent or old additions.",
    ),
    # ML mood/energy — one CLAP pass (heavy).
    FeatureSpec(
        "party",
        "Party / Energy",
        "scalar",
        "ml",
        28,
        ("chill", "peak"),
        "heavy",
        True,
        column="mood_party",
        normalize=_identity,
        desc="How much the CLAP audio model hears 'energetic party music, club banger, festival' rather than its opposite. A probability, 0 to 1.",
    ),
    FeatureSpec(
        "valence",
        "Mood / Valence",
        "scalar",
        "ml",
        322,
        ("dark", "uplifting"),
        "heavy",
        True,
        column="valence",
        normalize=_from_deam,
        desc="How positive the track feels, from dark and sad to euphoric. The CLAP model places it on a 1–9 scale between described anchor moods.",
    ),
    FeatureSpec(
        "similarity",
        "Similarity",
        "similarity",
        "ml",
        265,
        ("different", "sounds like current"),
        "heavy",
        True,
        column="genres_vector",
        embedding="clap",
        desc="Overall 'sounds like' from the CLAP audio embedding "
        "(cosine distance). Bundles timbre, genre and mood into "
        "one learned vector.",
    ),
    # Detected style — CLAP top-k genres (heavy); cosine of the label vector.
    # Distinct from the Traktor "genre" axis: this is audio-derived, that is
    # the file's metadata tag.
    FeatureSpec(
        "style",
        "Style",
        "style",
        "ml",
        292,
        ("any style", "same style"),
        "heavy",
        False,
        column="genres",
        desc="Genre detected from the audio by the CLAP model (not the "
        "file's tag). Matched on the detected genre mix.",
    ),
    FeatureSpec(
        "happy",
        "Happy",
        "scalar",
        "ml",
        48,
        ("somber", "joyful"),
        "heavy",
        False,
        column="mood_happy",
        normalize=_identity,
        desc="Probability the CLAP model hears happy, cheerful, uplifting music. Close to Valence, as a yes/no question.",
    ),
    FeatureSpec(
        "relaxed",
        "Relaxed",
        "scalar",
        "ml",
        170,
        ("tense", "calm"),
        "heavy",
        False,
        column="mood_relaxed",
        normalize=_identity,
        desc="Probability the CLAP model hears calm, peaceful, chill music.",
    ),
    FeatureSpec(
        "aggressive",
        "Aggressive",
        "scalar",
        "ml",
        8,
        ("smooth", "intense"),
        "heavy",
        False,
        column="mood_aggressive",
        normalize=_identity,
        desc="Probability the CLAP model hears hard, heavy, intense music — industrial and hard techno end.",
    ),
    FeatureSpec(
        "arousal",
        "Arousal / Drive",
        "scalar",
        "ml",
        340,
        ("low", "high energy"),
        "heavy",
        False,
        column="arousal",
        normalize=_from_deam,
        desc="How much drive the track has, from sleepy to pounding. The CLAP model places it on a 1–9 scale between described energy levels. Close to Party, without the club framing.",
    ),
    FeatureSpec(
        "vocal",
        "Vocal presence",
        "scalar",
        "ml",
        300,
        ("instrumental", "vocal"),
        "heavy",
        False,
        column="is_vocal",
        normalize=_identity,
        desc="Probability the CLAP model hears a lead voice or singing. Low for instrumentals.",
    ),
    FeatureSpec(
        "electronic",
        "Electronic",
        "scalar",
        "ml",
        210,
        ("acoustic", "synth"),
        "heavy",
        False,
        column="is_electronic",
        normalize=_identity,
        desc="Probability the CLAP model hears synths and drum machines rather than acoustic instruments.",
    ),
    # Melodic — HPSS (heavy), separable from the CLAP pass.
    FeatureSpec(
        "melodic",
        "Melodic / Rhythmic",
        "scalar",
        "melodic",
        188,
        ("percussive", "melodic"),
        "heavy",
        True,
        column="melodic",
        normalize=_identity,
        desc="Share of the sound that is tonal (chords, leads, pads) rather than drums, measured by splitting the audio into its harmonic and percussive parts over 30 s from the middle of the track.",
    ),
    # Signal features — librosa (cheap). Loosely-bounded ones default off until
    # calibrated against a real library; gains below are first-cut estimates.
    FeatureSpec(
        "brightness",
        "Brightness",
        "scalar",
        "signal",
        55,
        ("warm", "bright"),
        "cheap",
        False,
        column="spectral_centroid",
        normalize=_gain(3.0),
        absolute_scale=False,
        desc="Where the sound's weight sits in the spectrum — its centre of mass. Low for warm, dark mixes; high for bright, trebly ones.",
    ),
    # HFC — mean energy above 5 kHz.  Complements Brightness (spectral centroid):
    # Brightness shows *where* spectral weight sits; HFC measures raw presence in
    # the crisp/air band (hi-hats, transient attacks, sibilance).
    FeatureSpec(
        "hfc",
        "HF Content",
        "scalar",
        "signal",
        70,
        ("dull", "crisp"),
        "cheap",
        False,
        column="hfc",
        normalize=_gain(1.0),
        absolute_scale=False,
        desc="Mean spectral energy above 5 kHz. "
        "Complements Brightness: Brightness = spectral centre of mass; "
        "HFC = raw high-band energy (hi-hats, attacks, air).",
    ),
    # Low energy — mean spectral energy below 200 Hz.
    FeatureSpec(
        "low_energy",
        "Bass Energy",
        "scalar",
        "signal",
        15,
        ("thin", "bass-heavy"),
        "cheap",
        False,
        column="low_energy",
        normalize=_gain(1.0),
        absolute_scale=False,
        desc="Mean spectral energy below 200 Hz — absolute bass level.",
    ),
    # Mid energy — mean spectral energy 200 Hz – 5 kHz (body/presence band).
    FeatureSpec(
        "mid_energy",
        "Mid Energy",
        "scalar",
        "signal",
        50,
        ("hollow", "full-bodied"),
        "cheap",
        False,
        column="mid_energy",
        normalize=_gain(1.0),
        absolute_scale=False,
        desc="Mean spectral energy 200 Hz – 5 kHz. "
        "Covers the body, presence, and harmonic content of most instruments.",
    ),
    # Bassiness — low-band fraction of total energy; loudness-normalised.
    # Unlike Bass Energy this is not affected by overall track level, making it
    # a purer "spectral shape" measure: 1.0 = all energy in the bass band.
    FeatureSpec(
        "bassiness",
        "Bassiness",
        "scalar",
        "signal",
        10,
        ("top-heavy", "bass-heavy"),
        "cheap",
        False,
        column="bassiness",
        normalize=_identity,
        absolute_scale=True,
        desc="Fraction of total spectral energy below 200 Hz. "
        "Loudness-normalised — two tracks with different levels but "
        "the same tonal balance score equally.",
    ),
    # Band dynamics — RMS variation in each frequency band.
    # Complements full-band dynamic_complexity with per-band granularity:
    # e.g. high low_dynamics = pumping kick; high high_dynamics = busy hi-hats.
    FeatureSpec(
        "low_dynamics",
        "Bass Dynamics",
        "scalar",
        "signal",
        20,
        ("steady bass", "pumping"),
        "cheap",
        False,
        column="low_dynamics",
        normalize=_gain(1.0),
        absolute_scale=False,
        short="Bass±",
        desc="RMS variation in the bass band (< 200 Hz). "
        "High = pulsing/pumping kick; low = sustained or absent bass.",
    ),
    FeatureSpec(
        "mid_dynamics",
        "Mid Dynamics",
        "scalar",
        "signal",
        55,
        ("sustained", "expressive"),
        "cheap",
        False,
        column="mid_dynamics",
        normalize=_gain(1.0),
        absolute_scale=False,
        short="Mid±",
        desc="RMS variation in the body band (200 Hz – 5 kHz). "
        "High = large builds/drops or call-response arrangement.",
    ),
    FeatureSpec(
        "high_dynamics",
        "Hi Dynamics",
        "scalar",
        "signal",
        75,
        ("smooth highs", "percussive"),
        "cheap",
        False,
        column="high_dynamics",
        normalize=_gain(1.0),
        absolute_scale=False,
        short="Hi±",
        desc="RMS variation in the air band (> 5 kHz). "
        "High = busy hi-hats or prominent transients; low = filtered/smooth top.",
    ),
    FeatureSpec(
        "danceability",
        "Danceability",
        "scalar",
        "signal",
        130,
        ("loose", "driving"),
        "cheap",
        False,
        column="danceability",
        normalize=_identity,
        desc="How regular the pulse is — the peak of the onset pattern's self-similarity. Nearly every four-to-the-floor track scores high, so it separates little within one genre; Onset density says more.",
    ),
    FeatureSpec(
        "dynamics",
        "Dynamics",
        "scalar",
        "signal",
        90,
        ("flat", "dynamic"),
        "cheap",
        False,
        column="dynamic_complexity",
        normalize=_gain(10.0),
        absolute_scale=False,
        desc="How much the overall level moves over the track (spread of its RMS energy). High for big breakdowns and drops, low for a track at one level throughout.",
    ),
    # Loudness — mean RMS energy.  Distinct from Dynamics (= RMS std-dev, which
    # measures variation): loudness is the track's overall level.  Computed for
    # free alongside Dynamics from the same RMS frame sequence.
    FeatureSpec(
        "loudness",
        "Loudness",
        "scalar",
        "signal",
        240,
        ("quiet", "loud"),
        "cheap",
        False,
        column="loudness",
        normalize=_gain(5.0),
        absolute_scale=False,
        desc="Mean RMS energy — the track's average perceived level. "
        "Distinct from Dynamics (RMS variation). "
        "Useful for gain-matching awareness.",
    ),
    # ── Shape — bar-aligned structure and what the bassline does ──────────
    # Every window here is a number of bars, not seconds: a bar at 138 BPM is
    # 1.74 s, and windows in round seconds cut across the phrasing. Measured
    # in a "shape" compute group off the same decode as the signal features.
    FeatureSpec(
        "loop",
        "Loop",
        "scalar",
        "shape",
        20,
        ("through-composed", "tight loop"),
        "cheap",
        False,
        column="loop_4bar",
        normalize=_identity,
        desc="How closely a four-bar phrase resembles the next one. "
        "High for a groove that repeats, low for a track that "
        "keeps moving.",
    ),
    FeatureSpec(
        "sameness",
        "Sameness",
        "scalar",
        "shape",
        100,
        ("goes somewhere", "stays put"),
        "cheap",
        False,
        column="sameness",
        normalize=_identity,
        desc="How much the track sounds the same 32 bars later. High "
        "for a tool that runs unchanged, low for one built of "
        "distinct sections.",
    ),
    FeatureSpec(
        "evolution",
        "Evolution",
        "scalar",
        "shape",
        155,
        ("static", "travels"),
        "cheap",
        False,
        column="evolution",
        normalize=_gain(6.0),
        absolute_scale=False,
        desc="How far the texture moves from one 16-bar block to the next.",
    ),
    FeatureSpec(
        "onset_density",
        "Onset density",
        "scalar",
        "shape",
        15,
        ("sparse", "busy"),
        "cheap",
        False,
        short="Onsets",
        column="onset_density",
        normalize=_gain(0.09),
        absolute_scale=False,
        desc="Note and drum onsets per bar — how busy the track is. "
        "Distinct from Danceability, which is the peak of the "
        "onset autocorrelation: that says how regular the pulse "
        "is and reads 0.83–0.95 for every techno record, while "
        "this spans a factor of two across the same crate.",
    ),
    FeatureSpec(
        "sections",
        "Breaks",
        "scalar",
        "shape",
        320,
        ("none", "many"),
        "cheap",
        False,
        column="sections_per_min",
        normalize=_gain(0.6),
        absolute_scale=False,
        desc="Section boundaries per minute — breaks, drops and build-ups, found on the bar grid.",
    ),
    FeatureSpec(
        "bass_density",
        "Bass density",
        "scalar",
        "shape",
        285,
        ("sustained", "rolling"),
        "cheap",
        False,
        short="Bass notes",
        column="bass_notes_per_bar",
        normalize=_gain(0.08),
        absolute_scale=False,
        desc="Bass notes per bar. Low for a held sub, high for a rolling sixteenth line.",
    ),
    FeatureSpec(
        "bass_range",
        "Bass range",
        "scalar",
        "shape",
        125,
        ("one note", "travels"),
        "cheap",
        False,
        short="Bass range",
        column="bass_range",
        normalize=_gain(0.06),
        absolute_scale=False,
        desc="Semitones the bassline covers. Near zero for the "
        "single-note bass under cold stab techno.",
    ),
    FeatureSpec(
        "bounce",
        "Bounce",
        "scalar",
        "shape",
        65,
        ("flat", "bouncy"),
        "cheap",
        False,
        column="bounce",
        normalize=_gain(0.01),
        absolute_scale=False,
        desc="A bassline that is busy and travelling at once — "
        "density times range. Either on its own is a rolling "
        "one-note bass or a sparse melodic one; together they "
        "are what a DJ hears as bounce. First cut: it ranks two "
        "known-bouncy tracks top, but also puts a melodic "
        "wide-ranging bassline above them, so treat it as a "
        "candidate rather than a settled measure.",
    ),
    # Timbral fingerprint — a similarity axis built for free from the librosa
    # signal features above (z-scored across the library, cosine-compared). No
    # extra analysis: any track with signal features already has it. Distinct
    # from the CLAP "Similarity" axis — fully interpretable and genre-agnostic,
    # it matches sonic texture rather than learned genre/mood.
    FeatureSpec(
        "fingerprint",
        "Fingerprint",
        "similarity",
        "signal",
        195,
        ("different texture", "same texture"),
        "cheap",
        False,
        embedding="fingerprint",
        released=False,
        desc="Timbral 'sounds alike' from the librosa signal features "
        "(brightness, band energies, dynamics), z-scored and "
        "cosine-compared. GPU-free and independent of CLAP's "
        "genre bias.",
    ),
    # Extra ONNX embeddings — each scored against its own learned vector in the
    # track_embeddings side table. Off by default; available once the model's
    # artefact has been exported (scripts/export_<model>_onnx.py) and a scan has
    # backfilled it. Each is a different notion of "sounds alike".
    FeatureSpec(
        "mert",
        "MERT",
        "similarity",
        "embed",
        275,
        ("different", "same record"),
        "heavy",
        False,
        embedding="mert",
        released=False,
        desc="Music-specialised transformer (MERT). Matches sonic and "
        "musical identity below the level of a genre label — "
        "'sounds like the same kind of record'.",
    ),
    FeatureSpec(
        "discogs",
        "Discogs",
        "similarity",
        "embed",
        35,
        ("different", "same style"),
        "heavy",
        False,
        embedding="discogs",
        released=False,
        desc="EffNet trained on the Discogs genre/style tree — matches "
        "the subgenre a record store would file it under.",
    ),
    FeatureSpec(
        "panns",
        "PANNs",
        "similarity",
        "embed",
        110,
        ("different", "same palette"),
        "heavy",
        False,
        embedding="panns",
        released=False,
        desc="AudioSet sound-event embedding (PANNs CNN14). Matches the "
        "sonic palette / instrumentation present in the track.",
    ),
    FeatureSpec(
        "openl3",
        "OpenL3",
        "similarity",
        "embed",
        250,
        ("different", "same texture"),
        "heavy",
        False,
        embedding="openl3",
        released=False,
        desc="OpenL3 music embedding — a learned timbral/texture match, "
        "lighter than MERT and with no harmonic awareness.",
    ),
)

# Public registry: only released axes. Parked ones stay in _ALL_FEATURES above.
FEATURES: tuple[FeatureSpec, ...] = tuple(f for f in _ALL_FEATURES if f.released)

BY_KEY: dict[str, FeatureSpec] = {f.key: f for f in FEATURES}


def features_in_group(group: str) -> list[FeatureSpec]:
    return [f for f in FEATURES if f.group == group]


# ── settings key conventions (shared by UI + any headless consumer) ─────────
def compute_group_key(group: str) -> str:
    """Bool — is this compute group computed at scan time?"""
    return f"compute/{group}"


#: The furthest the Key-T axis will look, whatever the setting says. Past a
#: few semitones a track audibly changes character, harmony or no harmony.
MAX_TRANSPOSE_RANGE = 6


def key_range_key() -> str:
    """How many semitones Key-T may move, either way."""
    return "axis/key/semitones"


def transpose_range() -> int:
    """The configured Key-T range in semitones, clamped to something sane."""
    from PySide6.QtCore import QSettings

    raw = QSettings().value(key_range_key(), 3)
    if not isinstance(raw, (int, float, str)):
        return 3
    try:
        value = int(raw)
    except ValueError:
        return 3
    return max(1, min(MAX_TRANSPOSE_RANGE, value))


def axis_key_mode_key() -> str:
    """Which of Key / Key-T / off the Key axis is in."""
    return "axis/key/mode"


def axis_compute_key(key: str) -> str:
    """Bool — is this feature computed by a scan at all?

    The first of two switches. This one decides what analysis costs; the
    second (:func:`axis_enabled_key`) decides what ranks tracks. A feature
    can be computed and not used, but not used and not computed means its
    column is stale until it is switched back on and backfilled.
    """
    return f"axis/{key}/compute"


def axis_enabled_key(key: str) -> str:
    """Bool — is this axis shown in the matching UI and scored?"""
    return f"axis/{key}/enabled"


def axis_value_key(key: str) -> str:
    """Int 0–100 — the target value for a scalar axis."""
    return f"axis/{key}/value"


def axis_strength_key(key: str) -> str:
    """Int 0–100 — the weight of this axis in the composite score."""
    return f"axis/{key}/strength"


def axis_active_key(key: str) -> str:
    """Bool — is the axis on? Toggled by clicking its label. Off = strength
    treated as 0 (slider value preserved)."""
    return f"axis/{key}/active"


def axis_sync_key(key: str) -> str:
    """Bool — keep a scalar axis's value locked to the current track. Toggled by
    the sync icon beside the label (scalar axes only)."""
    return f"axis/{key}/sync"


# ── runtime axis state passed into the matcher ──────────────────────────────
class KeyMode(Enum):
    """What the Key axis is doing: strict, transposing, or dropped.

    One axis with three states rather than two axes — Key and Key-T ask the
    same question and only one answer belongs in a column.
    """

    STRICT = ("Key", 0, True)
    TRANSPOSE = ("Key-T", 3, True)
    OFF = ("Key", 0, False)

    def __init__(self, label: str, max_semitones: int, active: bool) -> None:
        self.label = label
        self.max_semitones = max_semitones
        self.active = active

    @classmethod
    def from_name(cls, name: str | None) -> KeyMode:
        """A stored name back to a mode; anything unrecognised is the default."""
        try:
            return cls[str(name)]
        except KeyError:
            return DEFAULT_KEY_MODE


#: What the Key card does until told otherwise. Transposing is a superset of
#: strict — a track that already fits still wins, because a shift costs — so
#: the strict mode only ever hides matches that were there.
DEFAULT_KEY_MODE = KeyMode.TRANSPOSE


def next_key_mode(mode: KeyMode) -> KeyMode:
    """Key → Key-T → off → Key."""
    order = (KeyMode.STRICT, KeyMode.TRANSPOSE, KeyMode.OFF)
    return order[(order.index(mode) + 1) % len(order)]


@dataclass(frozen=True)
class ActiveAxis:
    """A user-configured axis handed to :func:`core.matcher.rank_matches_axes`.

    ``value`` and ``strength`` are normalised to [0, 1]. ``value`` is ignored
    for relational axes (bpm/key/similarity).

    ``sync`` mirrors the UI's sync lock (see :func:`axis_sync_key`): the value
    is a copy of the current track's rather than something the user dialled in.
    It only matters when there is no current track — see
    :func:`core.matcher.rank_matches_absolute`, which ranks on the axes that
    carry a target of their own and must ignore the ones that don't.
    """

    key: str
    value: float = 0.0
    strength: float = 0.0
    sync: bool = False
    #: Key axis only: how far Traktor's Key knob may move, in semitones.
    #: 0 is strict harmonic matching — see :class:`KeyMode`.
    max_semitones: int = 0

    @property
    def spec(self) -> FeatureSpec:
        return BY_KEY[self.key]
