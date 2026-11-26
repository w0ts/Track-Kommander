"""Shared constants — audio extensions, analysis params, Camelot wheel."""

APP_NAME = "Track Kommander"

# Port the embedded webhook server binds and the D2 QML bridge posts to.
# MUST match ``API_BASE_URL`` in ``Traktor/D2/Api/ApiClient.js`` — the QML side
# hardcodes it, so changing this alone silently breaks the connection.
WEBHOOK_PORT = 12345

# Port the native injector (libtk, DYLD-loaded into Traktor) listens on for the
# TK->dylib command channel, and that :mod:`workers.native_cmd` sends to. MUST
# match ``TK_CMD_PORT`` in ``traktor-native/src/tk_inject.mm`` (same default).
# Never 8766 — that is reserved for another local app.
NATIVE_CMD_PORT = 8767

AUDIO_EXTS = {".mp3", ".flac", ".wav", ".aiff", ".m4a", ".ogg"}
SR = 22_050

CAMELOT_MAP: dict[tuple[int, bool], str] = {
    (0, True): "8B",
    (1, True): "3B",
    (2, True): "10B",
    (3, True): "5B",
    (4, True): "12B",
    (5, True): "7B",
    (6, True): "2B",
    (7, True): "9B",
    (8, True): "4B",
    (9, True): "11B",
    (10, True): "6B",
    (11, True): "1B",
    (0, False): "5A",
    (1, False): "12A",
    (2, False): "7A",
    (3, False): "2A",
    (4, False): "9A",
    (5, False): "4A",
    (6, False): "11A",
    (7, False): "6A",
    (8, False): "1A",
    (9, False): "8A",
    (10, False): "3A",
    (11, False): "10A",
}

CAMELOT_TO_OPENKEY: dict[str, str] = {
    "8B": "1d",
    "9B": "2d",
    "10B": "3d",
    "11B": "4d",
    "12B": "5d",
    "1B": "6d",
    "2B": "7d",
    "3B": "8d",
    "4B": "9d",
    "5B": "10d",
    "6B": "11d",
    "7B": "12d",
    "8A": "1m",
    "9A": "2m",
    "10A": "3m",
    "11A": "4m",
    "12A": "5m",
    "1A": "6m",
    "2A": "7m",
    "3A": "8m",
    "4A": "9m",
    "5A": "10m",
    "6A": "11m",
    "7A": "12m",
}

BPM_PLATEAU = 5.0  # % diff before any BPM penalty
BPM_ZERO = 25.0  # % diff where BPM score reaches 0

# Musical-key colours, matched to Traktor's key colours so that a key reads
# the same in both applications — a track that is pink in Traktor's browser is
# pink here. Keyed by Camelot code (see CAMELOT_MAP above); relative
# major/minor pairs share a colour, which is what makes the wheel readable.
CAMELOT_COLORS: dict[str, str] = {
    # Major (B) keys
    "8B": "#FF0078",  # C  major
    "3B": "#90FF00",  # C# major
    "10B": "#0028FF",  # D  major
    "5B": "#FF7800",  # Eb major
    "12B": "#0088B8",  # E  major
    "7B": "#FF0000",  # F  major
    "2B": "#00FF00",  # F# major
    "9B": "#A000C8",  # G  major
    "4B": "#FFC800",  # G# major
    "11B": "#0064FF",  # A  major
    "6B": "#FF4000",  # Bb major
    "1B": "#00FF80",  # B  major
    # Minor (A) keys
    "5A": "#FF7800",  # C  minor
    "12A": "#0088B8",  # C# minor
    "7A": "#FF0000",  # D  minor
    "2A": "#00FF00",  # Eb minor
    "9A": "#A000C8",  # E  minor
    "4A": "#FFC800",  # F  minor
    "11A": "#0064FF",  # F# minor
    "6A": "#FF4000",  # G  minor
    "1A": "#00FF80",  # G# minor
    "8A": "#FF0078",  # A  minor
    "3A": "#90FF00",  # Bb minor
    "10A": "#0028FF",  # B  minor
}
