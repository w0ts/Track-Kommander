import CSI 1.0
import QtQuick 2.0
import "ApiClient.js" as ApiClient

// Tracks one Traktor deck and POSTs state changes to Track Kommander.
// deckId: 0=A, 1=B, 2=C, 3=D
Module {
    id: root
    property int deckId: 0

    readonly property string deckLetter: ["A", "B", "C", "D"][deckId]
    readonly property int    deckIdx:    deckId + 1  // Traktor uses 1-based indices

    // ── Content metadata ──────────────────────────────────────────────────────
    AppProperty { id: propTitle;       path: "app.traktor.decks." + root.deckIdx + ".content.title" }
    AppProperty { id: propArtist;      path: "app.traktor.decks." + root.deckIdx + ".content.artist" }
    AppProperty { id: propAlbum;       path: "app.traktor.decks." + root.deckIdx + ".content.album" }
    AppProperty { id: propGenre;       path: "app.traktor.decks." + root.deckIdx + ".content.genre" }
    AppProperty { id: propKey;         path: "app.traktor.decks." + root.deckIdx + ".content.musical_key" }
    AppProperty { id: propBaseBpm;     path: "app.traktor.decks." + root.deckIdx + ".tempo.base_bpm" }
    // The tempo the deck plays at — pitch fader and sync included. Carried
    // by the keep-alive, not sent on change: a changed-only post that is
    // dropped (one request per endpoint) would leave the app on a stale tempo.
    AppProperty { id: propTrueBpm;     path: "app.traktor.decks." + root.deckIdx + ".tempo.true_bpm" }
    AppProperty { id: propFilePath;     path: "app.traktor.decks." + root.deckIdx + ".track.content.file_path" }
    AppProperty { id: propTrackLength;  path: "app.traktor.decks." + root.deckIdx + ".track.content.track_length" }
    AppProperty { id: propPlayheadPos;  path: "app.traktor.decks." + root.deckIdx + ".track.player.playhead_position" }

    // ── Beat phase probes ─────────────────────────────────────────────────────
    // Candidates for "how far this deck sits off the beat", for capturing a
    // wrong beat grid: the offset left over once the DJ has nudged a track
    // into place by ear is the grid error. None is used by NI's own QML, so
    // all three are carried until one proves to report.
    AppProperty { id: propPhase;      path: "app.traktor.decks." + root.deckIdx + ".tempo.phase" }
    AppProperty { id: propBeatPhase;  path: "app.traktor.decks." + root.deckIdx + ".tempo.beatphase" }
    AppProperty { id: propSyncPhase;  path: "app.traktor.decks." + root.deckIdx + ".sync.phase" }

    // ── Key knob ──────────────────────────────────────────────────────────────
    // Normalised, not a semitone count: 1.0 is a full octave. We set it when
    // loading a transposed match; the DJ can also turn it by hand, and either
    // way the track's written key stops being the key it sounds in.
    AppProperty { id: propKeyAdjust; path: "app.traktor.decks." + root.deckIdx + ".track.key.adjust" }

    // ── Fires once when a new track is loaded ─────────────────────────────────
    AppProperty {
        id: propIsLoaded
        path: "app.traktor.decks." + root.deckIdx + ".is_loaded_signal"
        onValueChanged: {
            if (value) {
                ApiClient.send("deckLoaded/" + root.deckLetter, {
                    "title":        propTitle.value,
                    "artist":       propArtist.value,
                    "album":        propAlbum.value,
                    "genre":        propGenre.value,
                    "bpm":          propBaseBpm.value,
                    "key":          propKey.value,
                    "file_path":    propFilePath.value,
                    "track_length": propTrackLength.value
                });
            }
        }
    }

    // ── Play / pause state ────────────────────────────────────────────────────
    AppProperty {
        id: propPlay
        path: "app.traktor.decks." + root.deckIdx + ".play"
        onValueChanged: {
            ApiClient.send("updateDeck/" + root.deckLetter, { "play": value });
        }
    }


    // ── Snapshot of current state (used by keep-alive and resync) ────────────
    function currentState() {
        var len = propTrackLength.value;
        var pos = (len > 0) ? Math.max(0.0, Math.min(1.0, propPlayheadPos.value / len)) : 0.0;
        return {
            "deck":      root.deckLetter,
            "play":      propPlay.value,
            "filePath":  propFilePath.value,
            "title":     propTitle.value,
            "artist":    propArtist.value,
            "bpm":       propBaseBpm.value,
            "tempo":     propTrueBpm.value,
            "key":       propKey.value,
            "position":  pos,
            "keyAdjust":  propKeyAdjust.value,
            "phase":      propPhase.value,
            "beatphase":  propBeatPhase.value,
            "syncphase":  propSyncPhase.value
        };
    }

    // ── Re-send full deck state (called on resync after reconnect) ────────────
    function sendCurrentState() {
        if (propFilePath.value) {
            ApiClient.send("deckLoaded/" + root.deckLetter, {
                "title":        propTitle.value,
                "artist":       propArtist.value,
                "album":        propAlbum.value,
                "genre":        propGenre.value,
                "bpm":          propBaseBpm.value,
                "key":          propKey.value,
                "file_path":    propFilePath.value,
                "track_length": propTrackLength.value
            });
        }
        ApiClient.send("updateDeck/" + root.deckLetter, { "play": propPlay.value });
    }
}
