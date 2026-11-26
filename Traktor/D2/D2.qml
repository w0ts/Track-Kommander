import CSI 1.0
import "Api"   // track-kommander:patch v1

// Track Kommander's virtual D2 mapping.
//
// This file *replaces* Traktor's own CSI/D2/D2.qml, so a real Kontrol D2
// loses its native mapping while it is installed. The app's Install button
// does not use this file — it adds two lines to Traktor's own mapping
// instead, which leaves everything Native Instruments shipped in place.
// Keep this one for the manual route in INSTALL.txt, and for machines with
// no D2 hardware to lose.
//
// No D2 hardware is required either way: add a Kontrol D2 under Traktor
// Preferences → Controller Manager and Traktor loads the mapping.
//
// Traktor reads this once, at startup, and Qt caches the compiled result
// against the file's mtime — so after replacing it, restart Traktor, and if
// nothing arrives clear ~/Library/Caches/Traktor Pro 4/qmlcache.
Mapping {
  ApiModule { name: "track_kommander_api" }   // track-kommander:patch v1
}
