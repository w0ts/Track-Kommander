import CSI 1.0
import QtQuick 2.0
import "ApiClient.js" as ApiClient

// Tracks one mixer channel and reports on-air state to Track Kommander.
// index: 1=A, 2=B, 3=C, 4=D  (Traktor channel numbers, sent as /updateChannel/<index>)
//
// Matches the lord-carlos structure exactly: Item (not Module), onValueChanged
// directly on AppProperty, Timer for throttled level updates.
Item {
    property int  index:           1
    property bool isOnAirState:    false
    property real onAirLevelState: -1.0

    readonly property string pathPrefix: "app.traktor.mixer.channels." + index + "."

    AppProperty { id: propVolume;            path: pathPrefix + "volume";              onValueChanged: updateOnAirState() }
    AppProperty { id: propXfaderAssignLeft;  path: pathPrefix + "xfader_assign.left";  onValueChanged: updateOnAirState() }
    AppProperty { id: propXfaderAssignRight; path: pathPrefix + "xfader_assign.right"; onValueChanged: updateOnAirState() }
    AppProperty { id: propXfaderAdjust;      path: "app.traktor.mixer.xfader.adjust";  onValueChanged: updateOnAirState() }

    // Throttle onAirLevel updates to once every 250ms
    Timer {
        id: onAirLevelTimer
        interval: 250
        onTriggered: {
            var level = propVolume.value;
            if ((propXfaderAssignLeft.value  && propXfaderAdjust.value > 0.5)
             || (propXfaderAssignRight.value && propXfaderAdjust.value < 0.5)) {
                level *= 1 - Math.abs(propXfaderAdjust.value * 2 - 1);
            }
            if (level !== onAirLevelState) {
                ApiClient.send("updateChannel/" + index, { onAirLevel: level });
                onAirLevelState = level;
            }
        }
    }

    // ── Snapshot of current on-air level (used by keep-alive and resync) ────
    function currentState() {
        // Use the last computed level if available; fall back to raw fader value.
        return { "index": index, "onAirLevel": onAirLevelState >= 0 ? onAirLevelState : propVolume.value };
    }

    // ── Re-send current channel state (called on resync after reconnect) ─────
    function sendCurrentState() {
        isOnAirState    = !isOnAirState;   // invalidate cache to force a send
        onAirLevelState = -1.0;
        updateOnAirState();
    }

    function updateOnAirState() {
        var onAir = propVolume.value > 0
            && ((!propXfaderAssignLeft.value && !propXfaderAssignRight.value)
                || (propXfaderAssignLeft.value  && propXfaderAdjust.value < 1.0)
                || (propXfaderAssignRight.value && propXfaderAdjust.value > 0.0));

        if (onAir !== isOnAirState) {
            ApiClient.send("updateChannel/" + index, { isOnAir: onAir });
            isOnAirState = onAir;
        }
        onAirLevelTimer.restart();
    }
}
