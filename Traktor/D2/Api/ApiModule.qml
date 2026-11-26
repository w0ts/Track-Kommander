import CSI 1.0
import QtQuick 2.0
import "ApiClient.js" as ApiClient

// Top-level module: instantiates all deck/channel trackers and handles
// bidirectional communication with Track Kommander over HTTP.
Module {
    id: root

    // ── Deck state senders (A=0 … D=3) ───────────────────────────────────────
    ApiDeck { id: deckA; deckId: 0 }
    ApiDeck { id: deckB; deckId: 1 }
    ApiDeck { id: deckC; deckId: 2 }
    ApiDeck { id: deckD; deckId: 3 }

    // ── Channel on-air senders (1=A … 4=D) ───────────────────────────────────
    ApiChannel { id: channelA; index: 1 }
    ApiChannel { id: channelB; index: 2 }
    ApiChannel { id: channelC; index: 3 }
    ApiChannel { id: channelD; index: 4 }

    // ── Master clock ──────────────────────────────────────────────────────────
    ApiMasterClock {}

    // Master tempo read (for transition timing)
    AppProperty { id: propMasterTempo; path: "app.traktor.masterclock.tempo" }

    // ── GUI layout (so Python can re-detect decks when 2↔4 layout flips) ────
    AppProperty { id: probeGuiLayout; path: "app.traktor.gui.layout" }


    // ── Load-command receiver ─────────────────────────────────────────────────
    AppProperty { id: searchProp; path: "app.traktor.browser.search_string" }
    // Key knob per deck, in semitones — the same property NI's own key
    // mapping writes. A match found by transposing arrives transposed.
    AppProperty { id: keyAdj1; path: "app.traktor.decks.1.track.key.adjust" }
    AppProperty { id: keyAdj2; path: "app.traktor.decks.2.track.key.adjust" }
    AppProperty { id: keyAdj3; path: "app.traktor.decks.3.track.key.adjust" }
    AppProperty { id: keyAdj4; path: "app.traktor.decks.4.track.key.adjust" }

    AppProperty { id: loadDeck1;  path: "app.traktor.decks.1.load.selected" }
    AppProperty { id: loadDeck2;  path: "app.traktor.decks.2.load.selected" }
    AppProperty { id: loadDeck3;  path: "app.traktor.decks.3.load.selected" }
    AppProperty { id: loadDeck4;  path: "app.traktor.decks.4.load.selected" }

    // Channel fader volumes
    AppProperty { id: volCh1; path: "app.traktor.mixer.channels.1.volume" }
    AppProperty { id: volCh2; path: "app.traktor.mixer.channels.2.volume" }
    AppProperty { id: volCh3; path: "app.traktor.mixer.channels.3.volume" }
    AppProperty { id: volCh4; path: "app.traktor.mixer.channels.4.volume" }

    // Channel EQ low/bass (0.0 = full cut, 0.5 = centre/0 dB, 1.0 = full boost)
    AppProperty { id: eqBass1; path: "app.traktor.mixer.channels.1.eq.low" }
    AppProperty { id: eqBass2; path: "app.traktor.mixer.channels.2.eq.low" }
    AppProperty { id: eqBass3; path: "app.traktor.mixer.channels.3.eq.low" }
    AppProperty { id: eqBass4; path: "app.traktor.mixer.channels.4.eq.low" }

    // Headphone cue per channel
    AppProperty { id: pflCh1; path: "app.traktor.mixer.channels.1.cue" }
    AppProperty { id: pflCh2; path: "app.traktor.mixer.channels.2.cue" }
    AppProperty { id: pflCh3; path: "app.traktor.mixer.channels.3.cue" }
    AppProperty { id: pflCh4; path: "app.traktor.mixer.channels.4.cue" }

    // Deck sync
    AppProperty { id: syncDeck1; path: "app.traktor.decks.1.sync.enabled" }
    AppProperty { id: syncDeck2; path: "app.traktor.decks.2.sync.enabled" }
    AppProperty { id: syncDeck3; path: "app.traktor.decks.3.sync.enabled" }
    AppProperty { id: syncDeck4; path: "app.traktor.decks.4.sync.enabled" }

    // ── Hotcue triggers (experiment) ────────────────────────────────────────
    // Two write paths tried in fireHotcue():
    //   (1) MappingPropertyDescriptor → Wire → Hotcues element. The Hotcues
    //       CSI element accepts ``hotcues.<M>.trigger`` as a Wire target;
    //       NI uses ``%surface%.pads.X`` as the source. We try a JS-writable
    //       MappingPropertyDescriptor instead — if CSI resolves its path
    //       as a Wire source, writing true→false propagates to the trigger.
    //   (2) Fallback: write true to ``track.cue.hotcues.<M>.active``. NI
    //       reads this but never writes it; might or might not be a trigger.
    // Diagnostic readables still in place so we can see what changed when
    // the user presses a key and report findings to NI/the user.

    // (1) Virtual button per (deck, cue) — 32 descriptors.
    MappingPropertyDescriptor { id: hcBtn1_1; path: "mapping.kommander.hotcue.A1"; type: MappingPropertyDescriptor.Boolean; value: false }
    MappingPropertyDescriptor { id: hcBtn1_2; path: "mapping.kommander.hotcue.A2"; type: MappingPropertyDescriptor.Boolean; value: false }
    MappingPropertyDescriptor { id: hcBtn1_3; path: "mapping.kommander.hotcue.A3"; type: MappingPropertyDescriptor.Boolean; value: false }
    MappingPropertyDescriptor { id: hcBtn1_4; path: "mapping.kommander.hotcue.A4"; type: MappingPropertyDescriptor.Boolean; value: false }
    MappingPropertyDescriptor { id: hcBtn1_5; path: "mapping.kommander.hotcue.A5"; type: MappingPropertyDescriptor.Boolean; value: false }
    MappingPropertyDescriptor { id: hcBtn1_6; path: "mapping.kommander.hotcue.A6"; type: MappingPropertyDescriptor.Boolean; value: false }
    MappingPropertyDescriptor { id: hcBtn1_7; path: "mapping.kommander.hotcue.A7"; type: MappingPropertyDescriptor.Boolean; value: false }
    MappingPropertyDescriptor { id: hcBtn1_8; path: "mapping.kommander.hotcue.A8"; type: MappingPropertyDescriptor.Boolean; value: false }
    MappingPropertyDescriptor { id: hcBtn2_1; path: "mapping.kommander.hotcue.B1"; type: MappingPropertyDescriptor.Boolean; value: false }
    MappingPropertyDescriptor { id: hcBtn2_2; path: "mapping.kommander.hotcue.B2"; type: MappingPropertyDescriptor.Boolean; value: false }
    MappingPropertyDescriptor { id: hcBtn2_3; path: "mapping.kommander.hotcue.B3"; type: MappingPropertyDescriptor.Boolean; value: false }
    MappingPropertyDescriptor { id: hcBtn2_4; path: "mapping.kommander.hotcue.B4"; type: MappingPropertyDescriptor.Boolean; value: false }
    MappingPropertyDescriptor { id: hcBtn2_5; path: "mapping.kommander.hotcue.B5"; type: MappingPropertyDescriptor.Boolean; value: false }
    MappingPropertyDescriptor { id: hcBtn2_6; path: "mapping.kommander.hotcue.B6"; type: MappingPropertyDescriptor.Boolean; value: false }
    MappingPropertyDescriptor { id: hcBtn2_7; path: "mapping.kommander.hotcue.B7"; type: MappingPropertyDescriptor.Boolean; value: false }
    MappingPropertyDescriptor { id: hcBtn2_8; path: "mapping.kommander.hotcue.B8"; type: MappingPropertyDescriptor.Boolean; value: false }
    MappingPropertyDescriptor { id: hcBtn3_1; path: "mapping.kommander.hotcue.C1"; type: MappingPropertyDescriptor.Boolean; value: false }
    MappingPropertyDescriptor { id: hcBtn3_2; path: "mapping.kommander.hotcue.C2"; type: MappingPropertyDescriptor.Boolean; value: false }
    MappingPropertyDescriptor { id: hcBtn3_3; path: "mapping.kommander.hotcue.C3"; type: MappingPropertyDescriptor.Boolean; value: false }
    MappingPropertyDescriptor { id: hcBtn3_4; path: "mapping.kommander.hotcue.C4"; type: MappingPropertyDescriptor.Boolean; value: false }
    MappingPropertyDescriptor { id: hcBtn3_5; path: "mapping.kommander.hotcue.C5"; type: MappingPropertyDescriptor.Boolean; value: false }
    MappingPropertyDescriptor { id: hcBtn3_6; path: "mapping.kommander.hotcue.C6"; type: MappingPropertyDescriptor.Boolean; value: false }
    MappingPropertyDescriptor { id: hcBtn3_7; path: "mapping.kommander.hotcue.C7"; type: MappingPropertyDescriptor.Boolean; value: false }
    MappingPropertyDescriptor { id: hcBtn3_8; path: "mapping.kommander.hotcue.C8"; type: MappingPropertyDescriptor.Boolean; value: false }
    MappingPropertyDescriptor { id: hcBtn4_1; path: "mapping.kommander.hotcue.D1"; type: MappingPropertyDescriptor.Boolean; value: false }
    MappingPropertyDescriptor { id: hcBtn4_2; path: "mapping.kommander.hotcue.D2"; type: MappingPropertyDescriptor.Boolean; value: false }
    MappingPropertyDescriptor { id: hcBtn4_3; path: "mapping.kommander.hotcue.D3"; type: MappingPropertyDescriptor.Boolean; value: false }
    MappingPropertyDescriptor { id: hcBtn4_4; path: "mapping.kommander.hotcue.D4"; type: MappingPropertyDescriptor.Boolean; value: false }
    MappingPropertyDescriptor { id: hcBtn4_5; path: "mapping.kommander.hotcue.D5"; type: MappingPropertyDescriptor.Boolean; value: false }
    MappingPropertyDescriptor { id: hcBtn4_6; path: "mapping.kommander.hotcue.D6"; type: MappingPropertyDescriptor.Boolean; value: false }
    MappingPropertyDescriptor { id: hcBtn4_7; path: "mapping.kommander.hotcue.D7"; type: MappingPropertyDescriptor.Boolean; value: false }
    MappingPropertyDescriptor { id: hcBtn4_8; path: "mapping.kommander.hotcue.D8"; type: MappingPropertyDescriptor.Boolean; value: false }
    property var hotcueButtons: [
        [hcBtn1_1, hcBtn1_2, hcBtn1_3, hcBtn1_4, hcBtn1_5, hcBtn1_6, hcBtn1_7, hcBtn1_8],
        [hcBtn2_1, hcBtn2_2, hcBtn2_3, hcBtn2_4, hcBtn2_5, hcBtn2_6, hcBtn2_7, hcBtn2_8],
        [hcBtn3_1, hcBtn3_2, hcBtn3_3, hcBtn3_4, hcBtn3_5, hcBtn3_6, hcBtn3_7, hcBtn3_8],
        [hcBtn4_1, hcBtn4_2, hcBtn4_3, hcBtn4_4, hcBtn4_5, hcBtn4_6, hcBtn4_7, hcBtn4_8],
    ]

    // Hotcues elements + Wires — same pattern as NI's HotcuesModule.qml,
    // but sourced from our virtual descriptors instead of `%surface%.pads.X`.
    Hotcues { name: "kHcA"; channel: 1 }
    Hotcues { name: "kHcB"; channel: 2 }
    Hotcues { name: "kHcC"; channel: 3 }
    Hotcues { name: "kHcD"; channel: 4 }

    Wire { from: "mapping.kommander.hotcue.A1"; to: "kHcA.1.trigger" }
    Wire { from: "mapping.kommander.hotcue.A2"; to: "kHcA.2.trigger" }
    Wire { from: "mapping.kommander.hotcue.A3"; to: "kHcA.3.trigger" }
    Wire { from: "mapping.kommander.hotcue.A4"; to: "kHcA.4.trigger" }
    Wire { from: "mapping.kommander.hotcue.A5"; to: "kHcA.5.trigger" }
    Wire { from: "mapping.kommander.hotcue.A6"; to: "kHcA.6.trigger" }
    Wire { from: "mapping.kommander.hotcue.A7"; to: "kHcA.7.trigger" }
    Wire { from: "mapping.kommander.hotcue.A8"; to: "kHcA.8.trigger" }
    Wire { from: "mapping.kommander.hotcue.B1"; to: "kHcB.1.trigger" }
    Wire { from: "mapping.kommander.hotcue.B2"; to: "kHcB.2.trigger" }
    Wire { from: "mapping.kommander.hotcue.B3"; to: "kHcB.3.trigger" }
    Wire { from: "mapping.kommander.hotcue.B4"; to: "kHcB.4.trigger" }
    Wire { from: "mapping.kommander.hotcue.B5"; to: "kHcB.5.trigger" }
    Wire { from: "mapping.kommander.hotcue.B6"; to: "kHcB.6.trigger" }
    Wire { from: "mapping.kommander.hotcue.B7"; to: "kHcB.7.trigger" }
    Wire { from: "mapping.kommander.hotcue.B8"; to: "kHcB.8.trigger" }
    Wire { from: "mapping.kommander.hotcue.C1"; to: "kHcC.1.trigger" }
    Wire { from: "mapping.kommander.hotcue.C2"; to: "kHcC.2.trigger" }
    Wire { from: "mapping.kommander.hotcue.C3"; to: "kHcC.3.trigger" }
    Wire { from: "mapping.kommander.hotcue.C4"; to: "kHcC.4.trigger" }
    Wire { from: "mapping.kommander.hotcue.C5"; to: "kHcC.5.trigger" }
    Wire { from: "mapping.kommander.hotcue.C6"; to: "kHcC.6.trigger" }
    Wire { from: "mapping.kommander.hotcue.C7"; to: "kHcC.7.trigger" }
    Wire { from: "mapping.kommander.hotcue.C8"; to: "kHcC.8.trigger" }
    Wire { from: "mapping.kommander.hotcue.D1"; to: "kHcD.1.trigger" }
    Wire { from: "mapping.kommander.hotcue.D2"; to: "kHcD.2.trigger" }
    Wire { from: "mapping.kommander.hotcue.D3"; to: "kHcD.3.trigger" }
    Wire { from: "mapping.kommander.hotcue.D4"; to: "kHcD.4.trigger" }
    Wire { from: "mapping.kommander.hotcue.D5"; to: "kHcD.5.trigger" }
    Wire { from: "mapping.kommander.hotcue.D6"; to: "kHcD.6.trigger" }
    Wire { from: "mapping.kommander.hotcue.D7"; to: "kHcD.7.trigger" }
    Wire { from: "mapping.kommander.hotcue.D8"; to: "kHcD.8.trigger" }

    // (2) Fallback writable AppProperty (might or might not be a trigger).
    // Probe both ``active`` and ``exists`` for deck 1, hotcue 1 so we can
    // SEE the values on stdout when the user presses 1 — diagnostic.
    AppProperty { id: hcA1_1_active; path: "app.traktor.decks.1.track.cue.hotcues.1.active" }
    AppProperty { id: hcA1_2_active; path: "app.traktor.decks.1.track.cue.hotcues.2.active" }
    AppProperty { id: hcA1_3_active; path: "app.traktor.decks.1.track.cue.hotcues.3.active" }
    AppProperty { id: hcA1_4_active; path: "app.traktor.decks.1.track.cue.hotcues.4.active" }
    AppProperty { id: hcA1_5_active; path: "app.traktor.decks.1.track.cue.hotcues.5.active" }
    AppProperty { id: hcA1_6_active; path: "app.traktor.decks.1.track.cue.hotcues.6.active" }
    AppProperty { id: hcA1_7_active; path: "app.traktor.decks.1.track.cue.hotcues.7.active" }
    AppProperty { id: hcA1_8_active; path: "app.traktor.decks.1.track.cue.hotcues.8.active" }
    AppProperty { id: hcA2_1_active; path: "app.traktor.decks.2.track.cue.hotcues.1.active" }
    AppProperty { id: hcA2_2_active; path: "app.traktor.decks.2.track.cue.hotcues.2.active" }
    AppProperty { id: hcA2_3_active; path: "app.traktor.decks.2.track.cue.hotcues.3.active" }
    AppProperty { id: hcA2_4_active; path: "app.traktor.decks.2.track.cue.hotcues.4.active" }
    AppProperty { id: hcA2_5_active; path: "app.traktor.decks.2.track.cue.hotcues.5.active" }
    AppProperty { id: hcA2_6_active; path: "app.traktor.decks.2.track.cue.hotcues.6.active" }
    AppProperty { id: hcA2_7_active; path: "app.traktor.decks.2.track.cue.hotcues.7.active" }
    AppProperty { id: hcA2_8_active; path: "app.traktor.decks.2.track.cue.hotcues.8.active" }
    AppProperty { id: hcA3_1_active; path: "app.traktor.decks.3.track.cue.hotcues.1.active" }
    AppProperty { id: hcA3_2_active; path: "app.traktor.decks.3.track.cue.hotcues.2.active" }
    AppProperty { id: hcA3_3_active; path: "app.traktor.decks.3.track.cue.hotcues.3.active" }
    AppProperty { id: hcA3_4_active; path: "app.traktor.decks.3.track.cue.hotcues.4.active" }
    AppProperty { id: hcA3_5_active; path: "app.traktor.decks.3.track.cue.hotcues.5.active" }
    AppProperty { id: hcA3_6_active; path: "app.traktor.decks.3.track.cue.hotcues.6.active" }
    AppProperty { id: hcA3_7_active; path: "app.traktor.decks.3.track.cue.hotcues.7.active" }
    AppProperty { id: hcA3_8_active; path: "app.traktor.decks.3.track.cue.hotcues.8.active" }
    AppProperty { id: hcA4_1_active; path: "app.traktor.decks.4.track.cue.hotcues.1.active" }
    AppProperty { id: hcA4_2_active; path: "app.traktor.decks.4.track.cue.hotcues.2.active" }
    AppProperty { id: hcA4_3_active; path: "app.traktor.decks.4.track.cue.hotcues.3.active" }
    AppProperty { id: hcA4_4_active; path: "app.traktor.decks.4.track.cue.hotcues.4.active" }
    AppProperty { id: hcA4_5_active; path: "app.traktor.decks.4.track.cue.hotcues.5.active" }
    AppProperty { id: hcA4_6_active; path: "app.traktor.decks.4.track.cue.hotcues.6.active" }
    AppProperty { id: hcA4_7_active; path: "app.traktor.decks.4.track.cue.hotcues.7.active" }
    AppProperty { id: hcA4_8_active; path: "app.traktor.decks.4.track.cue.hotcues.8.active" }
    property var hotcueActive: [
        [hcA1_1_active, hcA1_2_active, hcA1_3_active, hcA1_4_active, hcA1_5_active, hcA1_6_active, hcA1_7_active, hcA1_8_active],
        [hcA2_1_active, hcA2_2_active, hcA2_3_active, hcA2_4_active, hcA2_5_active, hcA2_6_active, hcA2_7_active, hcA2_8_active],
        [hcA3_1_active, hcA3_2_active, hcA3_3_active, hcA3_4_active, hcA3_5_active, hcA3_6_active, hcA3_7_active, hcA3_8_active],
        [hcA4_1_active, hcA4_2_active, hcA4_3_active, hcA4_4_active, hcA4_5_active, hcA4_6_active, hcA4_7_active, hcA4_8_active],
    ]
    // Diagnostic readables — exists tells us whether the cue is set on
    // the current track at all; start_pos tells us where it lives.
    AppProperty { id: hcExists1A; path: "app.traktor.decks.1.track.cue.hotcues.1.exists" }
    AppProperty { id: hcStart1A;  path: "app.traktor.decks.1.track.cue.hotcues.1.start_pos" }

    function fireHotcue(deckIdx, idx) {
        if (deckIdx < 1 || deckIdx > 4 || idx < 1 || idx > 8) return;
        // Path 1: virtual button → Wire → Hotcues element.
        var btn = hotcueButtons[deckIdx - 1][idx - 1];
        // Path 2: writable AppProperty fallback.
        var prop = hotcueActive[deckIdx - 1][idx - 1];
        console.log("TrackKommander: hotcue fire — deck=" + deckIdx + " idx=" + idx
                    + "  btn.value=" + btn.value
                    + "  prop.value=" + prop.value
                    + "  (probe deck1 hc1: exists=" + hcExists1A.value
                    + " start_pos=" + hcStart1A.value + ")");
        btn.value = true;
        prop.value = true;
        hotcueReleaseTimer.targetBtn  = btn;
        hotcueReleaseTimer.targetProp = prop;
        hotcueReleaseTimer.restart();
    }
    Timer {
        id: hotcueReleaseTimer
        interval: 50
        repeat: false
        property var targetBtn:  null
        property var targetProp: null
        onTriggered: {
            if (targetBtn)  targetBtn.value  = false;
            if (targetProp) targetProp.value = false;
            targetBtn = null;
            targetProp = null;
        }
    }

    // ── Beat jump ───────────────────────────────────────────────────────────
    // ``app.traktor.decks.<N>.beatjump`` is a writable AppProperty that
    // takes a JumpSize enum (qml/Defines/JumpSize.qml). Values we use:
    //   -10 = back 8 beats     (JumpSize.bwd_8)
    //   -11 = back 16 beats    (JumpSize.bwd_16)
    //    10 = fwd 8 beats      (JumpSize.fwd_8)
    //    11 = fwd 16 beats     (JumpSize.fwd_16)
    // Write the value once, schedule a release to 0 50 ms later so the
    // HoldProperty pattern Traktor uses internally sees a clean edge.
    AppProperty { id: bjDeck1; path: "app.traktor.decks.1.beatjump" }
    AppProperty { id: bjDeck2; path: "app.traktor.decks.2.beatjump" }
    AppProperty { id: bjDeck3; path: "app.traktor.decks.3.beatjump" }
    AppProperty { id: bjDeck4; path: "app.traktor.decks.4.beatjump" }
    property var beatjumpProps: [bjDeck1, bjDeck2, bjDeck3, bjDeck4]

    function fireBeatjump(deckIdx, value) {
        if (deckIdx < 1 || deckIdx > 4) return;
        var p = beatjumpProps[deckIdx - 1];
        console.log("TrackKommander: beatjump deck=" + deckIdx + " value=" + value);
        p.value = value;
        beatjumpReleaseTimer.target = p;
        beatjumpReleaseTimer.restart();
    }
    Timer {
        id: beatjumpReleaseTimer
        interval: 50
        repeat: false
        property var target: null
        onTriggered: { if (target) target.value = 0; target = null; }
    }

    // ── Sync ────────────────────────────────────────────────────────────────
    // Traktor has no "sync everything" switch: Sync is per deck and has to be
    // armed by hand on each one. Writing ``decks.<N>.sync.enabled`` is the
    // same thing the Sync button does, so Kommander can arm them all at once.
    function setDeckSync(deckIdx, on) {
        if      (deckIdx === 1) syncDeck1.value = on;
        else if (deckIdx === 2) syncDeck2.value = on;
        else if (deckIdx === 3) syncDeck3.value = on;
        else if (deckIdx === 4) syncDeck4.value = on;
    }

    function setAllDeckSync(on) {
        for (var i = 1; i <= 4; i++) setDeckSync(i, on);
    }

    // Deck play state
    AppProperty { id: playDeck1; path: "app.traktor.decks.1.play" }
    AppProperty { id: playDeck2; path: "app.traktor.decks.2.play" }
    AppProperty { id: playDeck3; path: "app.traktor.decks.3.play" }
    AppProperty { id: playDeck4; path: "app.traktor.decks.4.play" }

    // ── AutoMix transition state ──────────────────────────────────────────────
    // Shared timing state (reused across both phases)
    property int  amNewIdx:      0       // new deck index (1-4)
    property int  amStepMs:      100     // ms per tick (set before each start)
    property int  amStepsTotal:  0       // total ticks for this phase
    property int  amStepsDone:   0       // ticks completed so far

    // Outgoing-deck state captured at the start of Phase 2
    property bool amOut1: false;  property real amVol1: 0.0
    property bool amOut2: false;  property real amVol2: 0.0
    property bool amOut3: false;  property real amVol3: 0.0
    property bool amOut4: false;  property real amVol4: 0.0

    // ── AutoMix helpers ───────────────────────────────────────────────────────

    function amSetVol(idx, vol) {
        if      (idx === 1) volCh1.value = vol;
        else if (idx === 2) volCh2.value = vol;
        else if (idx === 3) volCh3.value = vol;
        else if (idx === 4) volCh4.value = vol;
    }
    function amGetVol(idx) {
        if      (idx === 1) return volCh1.value;
        else if (idx === 2) return volCh2.value;
        else if (idx === 3) return volCh3.value;
        else if (idx === 4) return volCh4.value;
        return 0.0;
    }
    function amSetBass(idx, val) {
        if      (idx === 1) eqBass1.value = val;
        else if (idx === 2) eqBass2.value = val;
        else if (idx === 3) eqBass3.value = val;
        else if (idx === 4) eqBass4.value = val;
    }
    function amIsPlaying(idx) {
        if      (idx === 1) return playDeck1.value;
        else if (idx === 2) return playDeck2.value;
        else if (idx === 3) return playDeck3.value;
        else if (idx === 4) return playDeck4.value;
        return false;
    }
    function amStopDeck(idx) {
        if      (idx === 1) playDeck1.value = false;
        else if (idx === 2) playDeck2.value = false;
        else if (idx === 3) playDeck3.value = false;
        else if (idx === 4) playDeck4.value = false;
    }

    // Cancel any in-progress transition
    function amStop() {
        amPhase1Timer.stop();
        amPhase2Timer.stop();
    }

    // Begin the two-phase transition for a newly loaded deck
    function amStartTransition(newIdx, rampBars) {
        amStop();
        var bpm    = propMasterTempo.value > 20 ? propMasterTempo.value : 120.0;
        var totalMs = (60000.0 / bpm) * 4.0 * rampBars;
        var stepMs  = 100;    // 10 Hz — well above the 1 Hz minimum
        var nSteps  = Math.max(1, Math.round(totalMs / stepMs));

        amNewIdx     = newIdx;
        amStepMs     = stepMs;
        amStepsTotal = nSteps;
        amStepsDone  = 0;

        amPhase1Timer.interval = stepMs;
        amPhase1Timer.start();

        console.log("TrackKommander: AutoMix phase 1 — deck=" + newIdx
                    + " bpm=" + bpm.toFixed(1)
                    + " bars=" + rampBars
                    + " totalMs=" + totalMs.toFixed(0)
                    + " steps=" + nSteps);
    }

    // Called at the end of Phase 1 — perform the bass swap then start Phase 2
    function amEndPhase1() {
        amSetVol(amNewIdx, 1.0);   // ensure exactly full volume
        amSetBass(amNewIdx, 0.5);  // the load killed its bass: back to centre even with no outgoing deck

        // Identify outgoing decks and snapshot their current volumes
        amOut1 = (1 !== amNewIdx && amIsPlaying(1));
        amOut2 = (2 !== amNewIdx && amIsPlaying(2));
        amOut3 = (3 !== amNewIdx && amIsPlaying(3));
        amOut4 = (4 !== amNewIdx && amIsPlaying(4));
        amVol1 = amOut1 ? amGetVol(1) : 0.0;
        amVol2 = amOut2 ? amGetVol(2) : 0.0;
        amVol3 = amOut3 ? amGetVol(3) : 0.0;
        amVol4 = amOut4 ? amGetVol(4) : 0.0;

        var hasOutgoing = amOut1 || amOut2 || amOut3 || amOut4;
        if (!hasOutgoing) {
            console.log("TrackKommander: AutoMix complete — no outgoing decks");
            return;
        }

        // Instant bass swap: kill bass on outgoing (the new deck's came back above)
        if (amOut1) amSetBass(1, 0.0);
        if (amOut2) amSetBass(2, 0.0);
        if (amOut3) amSetBass(3, 0.0);
        if (amOut4) amSetBass(4, 0.0);

        amStepsDone = 0;
        amPhase2Timer.interval = amStepMs;
        amPhase2Timer.start();

        console.log("TrackKommander: AutoMix phase 2 — outgoing:"
                    + (amOut1 ? " 1" : "") + (amOut2 ? " 2" : "")
                    + (amOut3 ? " 3" : "") + (amOut4 ? " 4" : ""));
    }

    // ── AutoMix Phase 1: ramp new deck volume 0 → 1 ──────────────────────────
    Timer {
        id: amPhase1Timer
        interval: 100
        repeat:   true
        onTriggered: {
            amStepsDone += 1;
            amSetVol(amNewIdx, Math.min(1.0, amStepsDone / amStepsTotal));
            if (amStepsDone >= amStepsTotal) {
                amPhase1Timer.stop();
                amEndPhase1();
            }
        }
    }

    // ── AutoMix Phase 2: fade out outgoing decks, stop when silent ───────────
    Timer {
        id: amPhase2Timer
        interval: 100
        repeat:   true
        onTriggered: {
            amStepsDone += 1;
            var t    = Math.min(1.0, amStepsDone / amStepsTotal);
            var done = (amStepsDone >= amStepsTotal);

            if (amOut1) {
                amSetVol(1, amVol1 * (1.0 - t));
                if (done) { amStopDeck(1); amSetBass(1, 0.5); }
            }
            if (amOut2) {
                amSetVol(2, amVol2 * (1.0 - t));
                if (done) { amStopDeck(2); amSetBass(2, 0.5); }
            }
            if (amOut3) {
                amSetVol(3, amVol3 * (1.0 - t));
                if (done) { amStopDeck(3); amSetBass(3, 0.5); }
            }
            if (amOut4) {
                amSetVol(4, amVol4 * (1.0 - t));
                if (done) { amStopDeck(4); amSetBass(4, 0.5); }
            }

            if (done) {
                amPhase2Timer.stop();
                console.log("TrackKommander: AutoMix transition complete");
            }
        }
    }

    // ── Commands from Track Kommander: a long poll ────────────────────────────
    // The app holds GET /command?wait=N until it has a command (or N s pass),
    // so a command arrives the moment it is queued. The client keeps one
    // request outstanding; this timer only issues the next one after the last
    // reply — within 100 ms — and does nothing while one is held.
    Timer {
        id: pollTimer
        interval: 100
        repeat:   true
        running:  true
        onTriggered: {
            ApiClient.get("command?wait=" + ApiClient.LONG_POLL_S, function(cmd) {
                if (!cmd || !cmd.action) return;

                // ── Resync ────────────────────────────────────────────────
                if (cmd.action === "resync") {
                    deckA.sendCurrentState(); deckB.sendCurrentState();
                    deckC.sendCurrentState(); deckD.sendCurrentState();
                    channelA.sendCurrentState(); channelB.sendCurrentState();
                    channelC.sendCurrentState(); channelD.sendCurrentState();
                    console.log("TrackKommander: resync complete");
                    return;
                }

                // ── Stop ──────────────────────────────────────────────────
                if (cmd.action === "stop") {
                    var stopIdx = ["A","B","C","D"].indexOf(cmd.deck || "A") + 1;
                    if      (stopIdx === 1) playDeck1.value = false;
                    else if (stopIdx === 2) playDeck2.value = false;
                    else if (stopIdx === 3) playDeck3.value = false;
                    else if (stopIdx === 4) playDeck4.value = false;
                    console.log("TrackKommander: stop deck " + (cmd.deck || "A"));
                    return;
                }

                // ── Play ───────────────────────────────────────────────────
                if (cmd.action === "play") {
                    var playIdx = ["A","B","C","D"].indexOf(cmd.deck || "A") + 1;
                    if      (playIdx === 1) { syncDeck1.value = true; playDeck1.value = true; }
                    else if (playIdx === 2) { syncDeck2.value = true; playDeck2.value = true; }
                    else if (playIdx === 3) { syncDeck3.value = true; playDeck3.value = true; }
                    else if (playIdx === 4) { syncDeck4.value = true; playDeck4.value = true; }
                    console.log("TrackKommander: play deck " + (cmd.deck || "A"));
                    return;
                }

                // ── Cancel AutoMix ─────────────────────────────────────────
                if (cmd.action === "cancel_automix") {
                    amStop();
                    console.log("TrackKommander: cancel_automix");
                    return;
                }

                // ── Hotcue trigger ────────────────────────────────────────
                if (cmd.action === "hotcue") {
                    var hcDeck = ["A","B","C","D"].indexOf(cmd.deck || "A") + 1;
                    var hcIdx  = parseInt(cmd.index, 10);
                    fireHotcue(hcDeck, hcIdx);
                    return;
                }

                // ── Sync (one deck, or every deck) ────────────────────────
                if (cmd.action === "sync") {
                    var syncOn = cmd.enabled !== false;
                    if (cmd.deck) {
                        var syncIdx = ["A","B","C","D"].indexOf(cmd.deck) + 1;
                        if (syncIdx > 0) setDeckSync(syncIdx, syncOn);
                        console.log("TrackKommander: sync " + (syncOn ? "on" : "off")
                                    + " deck " + cmd.deck);
                    } else {
                        setAllDeckSync(syncOn);
                        console.log("TrackKommander: sync " + (syncOn ? "on" : "off")
                                    + " all decks");
                    }
                    return;
                }

                // ── Beat jump ─────────────────────────────────────────────
                if (cmd.action === "beatjump") {
                    var bjDeck  = ["A","B","C","D"].indexOf(cmd.deck || "A") + 1;
                    var bjValue = parseInt(cmd.value, 10);
                    fireBeatjump(bjDeck, bjValue);
                    return;
                }

                if (cmd.action !== "load") return;

                var deck    = cmd.deck || "A";
                var deckIdx = ["A","B","C","D"].indexOf(deck) + 1;

                console.log("TrackKommander: load — deck=" + deck
                            + " play=" + cmd.play + " mute=" + cmd.mute
                            + " cue=" + cmd.cue + " automix=" + cmd.automix
                            + " bars=" + cmd.ramp_bars);

                if (deckIdx < 1 || deckIdx > 4) {
                    console.log("TrackKommander: invalid deck, ignoring");
                    return;
                }

                var filePath = cmd.path   || "";
                var search   = cmd.search || filePath;
                var slash = filePath.lastIndexOf("/");
                if (slash >= 0) search = filePath.substring(slash + 1);
                var dot = search.lastIndexOf(".");
                if (dot > 0)    search = search.substring(0, dot);

                loadDelayTimer.targetIdx  = deckIdx;
                loadDelayTimer.searchStr  = search;
                loadDelayTimer.doMute     = cmd.mute    !== false;
                loadDelayTimer.doCue      = cmd.cue     !== false;
                loadDelayTimer.doPlay     = cmd.play    !== false;
                loadDelayTimer.doAutomix  = cmd.automix === true;
                loadDelayTimer.skipLoad   = cmd.skip_load === true;
                // null means "leave the Key knob alone" — the axis is not
                // in Key-T mode and the knob belongs to the DJ.
                // Already normalised by Track Kommander: the property is not
                // a semitone count. null means "leave the knob alone".
                loadDelayTimer.transpose  = (cmd.key_adjust === null
                                             || cmd.key_adjust === undefined)
                                            ? null : cmd.key_adjust;
                loadDelayTimer.semitones  = cmd.transpose || 0;
                loadDelayTimer.rampBars   = cmd.ramp_bars || 4;
                loadDelayTimer.restart();
            }, ApiClient.LONG_POLL_TIMEOUT_MS);
        }
    }

    // ── Step 1: wait 800 ms for osascript, then mute/cue/load ────────────────
    Timer {
        id: loadDelayTimer
        interval: 800
        repeat:   false

        property int    targetIdx: 0
        property string searchStr: ""
        property bool   doMute:    true
        property bool   doCue:     true
        property bool   doPlay:    true
        property bool   doAutomix: false
        property bool   skipLoad:  false
        property int    rampBars:  4
        property var    transpose: null
        property int    semitones: 0

        onTriggered: {
            var idx = targetIdx;
            console.log("TrackKommander: loading deck idx=" + idx
                        + " mute=" + doMute + " cue=" + doCue
                        + " play=" + doPlay + " automix=" + doAutomix
                        + " skipLoad=" + skipLoad
                        + " bars=" + rampBars);

            // Silence channel fader
            if (doMute) {
                if      (idx === 1) volCh1.value = 0.0;
                else if (idx === 2) volCh2.value = 0.0;
                else if (idx === 3) volCh3.value = 0.0;
                else if (idx === 4) volCh4.value = 0.0;
            }

            // Kill bass on the new channel (AutoMix only — restored in phase 2 side)
            if (doAutomix) {
                if      (idx === 1) eqBass1.value = 0.0;
                else if (idx === 2) eqBass2.value = 0.0;
                else if (idx === 3) eqBass3.value = 0.0;
                else if (idx === 4) eqBass4.value = 0.0;
            }

            // Headphone cue: enable only on target channel
            if (doCue) {
                pflCh1.value = (idx === 1);
                pflCh2.value = (idx === 2);
                pflCh3.value = (idx === 3);
                pflCh4.value = (idx === 4);
            }

            // Search + load are skipped when Track Kommander already placed the track
            // on the deck via a synthesized drag. The rest of the automation
            // (sync+play, AutoMix) still runs in playDelayTimer below.
            if (!skipLoad) {
                searchProp.value = searchStr;
                if      (idx === 1) loadDeck1.value = true;
                else if (idx === 2) loadDeck2.value = true;
                else if (idx === 3) loadDeck3.value = true;
                else if (idx === 4) loadDeck4.value = true;
            }

            // Only while the Key axis is transposing. Then 0 is written too,
            // because a deck keeps the shift it was last left on.
            if (transpose !== null) {
                if      (idx === 1) keyAdj1.value = transpose;
                else if (idx === 2) keyAdj2.value = transpose;
                else if (idx === 3) keyAdj3.value = transpose;
                else if (idx === 4) keyAdj4.value = transpose;
                console.log("TrackKommander: key adjust " + semitones
                            + " semitones (" + transpose
                            + ") on deck idx=" + idx);
            }

            playDelayTimer.targetIdx = idx;
            playDelayTimer.doPlay    = doPlay;
            playDelayTimer.doAutomix = doAutomix;
            playDelayTimer.rampBars  = rampBars;
            playDelayTimer.restart();
        }
    }

    // ── Step 2: wait 600 ms then start playback (and AutoMix if requested) ────
    Timer {
        id: playDelayTimer
        interval: 600
        repeat:   false

        property int  targetIdx: 0
        property bool doPlay:    true
        property bool doAutomix: false
        property int  rampBars:  4

        onTriggered: {
            var idx = targetIdx;
            if (doPlay) {
                if      (idx === 1) { syncDeck1.value = true; playDeck1.value = true; }
                else if (idx === 2) { syncDeck2.value = true; playDeck2.value = true; }
                else if (idx === 3) { syncDeck3.value = true; playDeck3.value = true; }
                else if (idx === 4) { syncDeck4.value = true; playDeck4.value = true; }
                console.log("TrackKommander: sync+play deck idx=" + idx);

                if (doAutomix) {
                    amStartTransition(idx, rampBars);
                }
            }
        }
    }

    // ── Keep-alive: POST all state every 1 s (drives playhead position updates) ─
    Timer {
        id: keepAliveTimer
        interval: 1000
        repeat:   true
        running:  true
        onTriggered: {
            ApiClient.send("keepAlive", {
                "version":  ApiClient.D2_VERSION,
                "decks":    [ deckA.currentState(), deckB.currentState(),
                              deckC.currentState(), deckD.currentState() ],
                "channels": [ channelA.currentState(), channelB.currentState(),
                              channelC.currentState(), channelD.currentState() ],
                "gui_layout": probeGuiLayout.value
            });
        }
    }
}
