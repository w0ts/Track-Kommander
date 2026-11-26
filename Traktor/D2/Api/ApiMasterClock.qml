import CSI 1.0
import QtQuick 2.0
import "ApiClient.js" as ApiClient

// Reports master clock BPM changes.
Module {
    id: root

    AppProperty {
        id: propMasterTempo
        path: "app.traktor.masterclock.tempo"
        onValueChanged: {
            ApiClient.send("updateMasterClock", { "bpm": value });
        }
    }
}
